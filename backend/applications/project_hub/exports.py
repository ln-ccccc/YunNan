# -*- coding: utf-8 -*-
"""项目导出制品域（H3 拆分，2026-10-01）：geojson/csv/shp/xlsx/dxf 写出、
导出记录创建/清单/制品解析。契约不变——service 层 re-export 保持既有 import 兼容。"""
import csv
import datetime
import json
import logging
import shutil
import uuid
import zipfile
from pathlib import Path

from applications.extensions import db
from applications.models.project import ProjectExportRecord
from applications.project_hub._foundation import (
    ProjectStorageValidationError,
    _append_activity,
    _cleanup_storage_directory,
    _export_directory,
    _file_checksum,
    _get_project_or_404,
    _json_dump,
    _lock_project_row,
    _log_storage_cleanup_failure,
    _mark_export_failed,
    _rollback_after_storage_failure,
    _storage_key,
    _utc_timestamp,
    _write_artifact_atomic,
    _write_xlsx,
)
from applications.project_hub.project_storage import (
    export_root,
    resolve_project_record_file,
    write_json_atomic,
)
from applications.schemas.project import ProjectExportRecordSchema
from applications.project_hub.spatial_service import sanitize_public_geojson_value

LOGGER = logging.getLogger(__name__)

ALLOWED_EXPORT_FORMATS = {"geojson", "csv", "shp", "xlsx", "dxf"}


def _feature_mine_fid(properties):
    for field_name in ("FID_1", "FID", "OBJECTID", "id"):
        value = properties.get(field_name)
        if isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None

def _build_project_export_features(project):
    """自动导出要素 + 实际使用的矿山边界来源（F7：随几何同次解析冻结）。

    返回 (features, mine_vector_source)；manifest 直接用该来源，不再在
    中间 commit 后重读当前激活资源（防几何 A / manifest B）。"""
    from applications.project_hub.project_map import get_project_geojson_with_source

    mine_vector_payload, mine_vector_source = get_project_geojson_with_source(project.id)
    mine_features = {}
    for feature in mine_vector_payload.get("features", []):
        if not isinstance(feature, dict) or not feature.get("geometry"):
            continue
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            continue
        mine_fid = _feature_mine_fid(properties)
        if mine_fid is not None:
            mine_features[mine_fid] = feature

    datasets_by_mine = {}
    for dataset in project.datasets:
        datasets_by_mine.setdefault(dataset.mine_fid, dataset)

    features = []
    for binding in project.mines:
        feature = mine_features.get(binding.mine_fid)
        if feature is None:
            continue
        properties = feature.get("properties") or {}
        dataset = datasets_by_mine.get(binding.mine_fid)
        features.append(
            {
                "type": "Feature",
                "geometry": feature["geometry"],
                "properties": {
                    "project_id": project.id,
                    "mine_fid": binding.mine_fid,
                    "mine_name": binding.mine_name_snapshot
                    or properties.get("mine_name")
                    or properties.get("name")
                    or "",
                    "dataset_id": dataset.id if dataset is not None else None,
                    "result_type": dataset.dataset_kind if dataset is not None else None,
                    "year_start": dataset.year_start if dataset is not None else None,
                    "year_end": dataset.year_end if dataset is not None else None,
                },
            }
        )
    return features, mine_vector_source

def _export_reference_id(value):
    if isinstance(value, bool):
        return None
    try:
        identifier = int(value)
    except (TypeError, ValueError):
        return None
    return identifier if identifier > 0 else None

def _make_geojson_payload(project, features):
    bindings_by_fid = {binding.mine_fid: binding for binding in project.mines}
    datasets_by_id = {dataset.id: dataset for dataset in project.datasets if dataset.id is not None}
    normalized = []
    for feature in features or []:
        item = sanitize_public_geojson_value(dict(feature))
        properties = dict(item.get("properties") or {})
        binding = bindings_by_fid.get(_export_reference_id(properties.get("mine_fid")))
        dataset = datasets_by_id.get(_export_reference_id(properties.get("dataset_id")))
        if binding is not None and dataset is not None and dataset.mine_fid not in (None, binding.mine_fid):
            dataset = None
        properties.update(
            {
                "project_id": project.id,
                "mine_fid": binding.mine_fid if binding is not None else None,
                "mine_name": (
                    binding.mine_name_snapshot
                    or properties.get("mine_name")
                    or properties.get("name")
                    or ""
                )
                if binding is not None
                else "",
                "dataset_id": dataset.id if dataset is not None else None,
                "result_type": dataset.dataset_kind if dataset is not None else None,
                "year_start": dataset.year_start if dataset is not None else None,
                "year_end": dataset.year_end if dataset is not None else None,
            }
        )
        item["properties"] = properties
        normalized.append(item)
    return {"type": "FeatureCollection", "features": normalized}

def _write_geojson(path_obj, payload):
    with open(path_obj, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)

def _write_csv(path_obj, project):
    headers = [
        "project_id",
        "project_name",
        "mine_fid",
        "mine_name",
        "dataset_id",
        "dataset_kind",
        "display_name",
        "year_start",
        "year_end",
    ]
    dataset_by_mine = {}
    for dataset in project.datasets:
        dataset_by_mine.setdefault(dataset.mine_fid, []).append(dataset)
    with open(path_obj, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        if not project.mines and not project.datasets:
            writer.writerow([project.id, project.name, "", "", "", "", "", "", ""])
            return
        for binding in project.mines:
            rows = dataset_by_mine.get(binding.mine_fid) or [None]
            for dataset in rows:
                writer.writerow(
                    [
                        project.id,
                        project.name,
                        binding.mine_fid,
                        binding.mine_name_snapshot or "",
                        getattr(dataset, "id", ""),
                        getattr(dataset, "dataset_kind", ""),
                        getattr(dataset, "display_name", ""),
                        getattr(dataset, "year_start", ""),
                        getattr(dataset, "year_end", ""),
                    ]
                )

def _write_shp_zip(path_obj, geojson_payload, stable_stem=None, project_id=None, record_id=None):
    try:
        from osgeo import ogr, osr
    except Exception as exc:
        raise RuntimeError(f"SHP 导出依赖不可用: {exc}")

    archive_stem = str(stable_stem or path_obj.stem).strip()
    temp_dir = path_obj.parent / f".{archive_stem}.{uuid.uuid4().hex}.tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        base_path = temp_dir / archive_stem

        driver = ogr.GetDriverByName("ESRI Shapefile")
        dataset = driver.CreateDataSource(str(base_path.with_suffix(".shp")))
        if dataset is None:
            raise RuntimeError("无法创建 SHP 数据源")

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        layer = dataset.CreateLayer(archive_stem, srs, ogr.wkbPolygon)
        layer.CreateField(ogr.FieldDefn("project_id", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("mine_fid", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("dataset_id", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("result_type", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("year_start", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("year_end", ogr.OFTInteger))

        for feature in geojson_payload.get("features", []):
            geometry = ogr.CreateGeometryFromJson(json.dumps(feature.get("geometry")))
            if geometry is None:
                continue
            row = ogr.Feature(layer.GetLayerDefn())
            props = feature.get("properties") or {}
            row.SetField("project_id", int(props.get("project_id") or 0))
            row.SetField("mine_fid", int(props.get("mine_fid") or 0))
            row.SetField("dataset_id", int(props.get("dataset_id") or 0))
            row.SetField("result_type", str(props.get("result_type") or ""))
            row.SetField("year_start", int(props.get("year_start") or 0))
            row.SetField("year_end", int(props.get("year_end") or 0))
            row.SetGeometry(geometry)
            layer.CreateFeature(row)
            row = None
        dataset = None

        with zipfile.ZipFile(path_obj, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for ext in (".shp", ".shx", ".dbf", ".prj"):
                file_obj = base_path.with_suffix(ext)
                if file_obj.exists():
                    archive.write(file_obj, arcname=file_obj.name)
    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError as cleanup_exc:
            _log_storage_cleanup_failure(
                "exports",
                project_id,
                record_id,
                "shp_staging_cleanup",
                cleanup_exc,
            )

def _export_source_assets(project, export_format, export_features):
    """数据集来源标识（2026-11-14 审查问题31 修正）：
    - csv 写出全部数据集行 → 覆盖 project.datasets 全集（此前只识别 imagery）；
    - geojson/shp/dxf 从归一后的导出要素取 dataset_id，凡本项目数据集
      （任意 kind，含推理成果数据集）均入清单——标识格式 <kind>:<id>。
    数据集表无版本列，source_versions 维持 1（记录 id 即内容身份）；
    边界资源版本与成果修订由 mine_vector/classification_results 键承载。"""
    if export_format == "csv":
        datasets = sorted(
            (d for d in project.datasets if d.id is not None), key=lambda d: d.id
        )
    else:
        datasets_by_id = {
            dataset.id: dataset for dataset in project.datasets if dataset.id is not None
        }
        referenced = set()
        for feature in export_features or []:
            properties = (feature or {}).get("properties") or {}
            dataset_id = properties.get("dataset_id")
            if isinstance(dataset_id, bool):
                continue
            try:
                dataset_id = int(dataset_id)
            except (TypeError, ValueError):
                continue
            if dataset_id in datasets_by_id:
                referenced.add(datasets_by_id[dataset_id])
        datasets = sorted(referenced, key=lambda d: d.id)
    source_asset_ids = [f"{dataset.dataset_kind}:{dataset.id}" for dataset in datasets]
    return source_asset_ids, {asset_id: 1 for asset_id in source_asset_ids}


def _ledger_result_sources(results):
    """台账来源明细：成果 ID、修订号与推理时所用边界版本（manifest 追溯）。"""
    return [
        {
            "result_id": result.id,
            "mine_fid": result.mine_fid,
            "year": result.year,
            "vector_status": result.vector_status,
            "revision_no": result.current_revision_no,
            "mine_resource_version": result.mine_resource_version,
        }
        for result in results
    ]

def _export_manifest(
    project,
    record,
    artifact_name,
    artifact_path,
    export_features,
    mine_vector_source=None,
    ledger_results=None,
):
    source_asset_ids, source_versions = _export_source_assets(
        project,
        record.format,
        export_features,
    )
    manifest = {
        "project_id": project.id,
        "export_id": record.id,
        "format": record.format,
        "artifact_name": artifact_name,
        "created_at": _utc_timestamp(record.create_time),
        "source_asset_ids": source_asset_ids,
        "source_versions": source_versions,
        "mine_vector": mine_vector_source,
        "sha256": _file_checksum(artifact_path),
    }
    if record.format == "xlsx":
        manifest["classification_results"] = _ledger_result_sources(ledger_results or [])
    return manifest

def _within_export_budget_window(created_at):
    """pending 导出记录是否仍在"可能真的在生成"的时间窗内（预算 + 60s 余量）。

    create_time 由 datetime.now() 写入（与这里同一进程/同一时区口径），直接相减；
    缺失或类型异常按"仍在窗口内"处理——宁可多拦一次重复提交。"""
    from applications.project_hub.ledger import LEDGER_TIME_BUDGET_SECONDS

    if not isinstance(created_at, datetime.datetime):
        return True
    age = datetime.datetime.now() - created_at
    return age.total_seconds() <= LEDGER_TIME_BUDGET_SECONDS + 60


def create_export(project_id, payload, actor="system"):
    if not isinstance(payload, dict):
        raise ProjectStorageValidationError("导出请求格式不合法")
    if "output_dir" in payload:
        raise ProjectStorageValidationError("不支持指定服务端输出目录，请移除 output_dir")
    if set(payload).difference({"format", "features"}):
        raise ProjectStorageValidationError("导出请求包含不支持字段")
    if "features" in payload and not isinstance(payload["features"], list):
        raise ProjectStorageValidationError("导出要素格式不合法")
    project = _get_project_or_404(project_id)
    export_format = str(payload.get("format") or "").strip().lower()
    if export_format not in ALLOWED_EXPORT_FORMATS:
        raise ValueError("导出格式不合法")
    # 同项目同格式去重（2026-11-24 审计 D1）：导出在请求内同步生成，上一份还没
    # 生成完就再提交，会建出两条内容相同的记录（用户看到超时提示后重试即触发）。
    # 锁项目行把"查重 + 写 pending 记录"串行化；只认预算窗口内的 pending——更早的
    # pending 是后端中途崩溃留下的孤儿，不该永久堵住该项目该格式的导出
    _lock_project_row(project.id)
    running = (
        ProjectExportRecord.query.filter_by(
            project_id=project.id,
            format=export_format,
            status="pending",
        )
        .order_by(ProjectExportRecord.id.desc())
        .first()
    )
    if running is not None and _within_export_budget_window(running.create_time):
        raise ProjectStorageValidationError(
            f"{export_format} 导出正在生成中，请稍后在导出列表查看并下载，不要重复提交"
        )
    features = payload.get("features")
    # F7（2026-11-14 验收返修）：来源随几何同次解析冻结，manifest 不再于
    # 中间 commit 后重读"当前激活"边界（防几何 A / manifest B）
    mine_vector_source = None
    if features is None and export_format in {"geojson", "shp", "dxf"}:
        features, mine_vector_source = _build_project_export_features(project)
    record = ProjectExportRecord(
        project_id=project.id,
        format=export_format,
        status="pending",
        request_params_json=_json_dump({k: v for k, v in payload.items() if k != "features"}),
    )
    project_identifier = project.id
    record_id = None
    try:
        db.session.add(record)
        db.session.flush()
        record_id = record.id
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "exports",
            project_identifier,
            record_id,
            "initial_pending_rollback",
        )
        raise

    suffix = {"geojson": ".geojson", "csv": ".csv", "shp": ".zip", "xlsx": ".xlsx", "dxf": ".dxf"}[export_format]
    artifact_name = f"artifact{suffix}"
    artifact_key = _storage_key("projects", str(project_identifier), "exports", str(record_id), artifact_name)
    export_features = []
    ledger_results = None

    try:
        export_root(project_identifier, record_id)
        target_path = resolve_project_record_file(
            project_identifier,
            "exports",
            record_id,
            artifact_name,
        )
        if export_format == "geojson":
            geojson_payload = _make_geojson_payload(project, features or [])
            export_features = geojson_payload["features"]
            _write_artifact_atomic(
                target_path,
                _write_geojson,
                geojson_payload,
                project_id=project_identifier,
                record_id=record_id,
            )
        elif export_format == "csv":
            _write_artifact_atomic(
                target_path,
                _write_csv,
                project,
                project_id=project_identifier,
                record_id=record_id,
            )
        elif export_format == "xlsx":
            from applications.project_hub.ledger import (
                LedgerBudgetExceeded,
                collect_project_results,
            )

            # 台账内容与 manifest 来源共用同一次成果查询（一致快照）
            ledger_results = collect_project_results(project)
            try:
                _write_artifact_atomic(
                    target_path,
                    _write_xlsx,
                    project,
                    ledger_results,
                    project_id=project_identifier,
                    record_id=record_id,
                )
            except LedgerBudgetExceeded as exc:
                # 超时间预算：经外层统一清理目录并把记录落为 failed，以可读文案返回
                # （后端先于 BFF 超时给出明确结果，不留"后来又成功"的半成品）
                raise ProjectStorageValidationError(str(exc)) from exc
        elif export_format == "dxf":
            from applications.project_hub.export_dxf import write_dxf

            geojson_payload = _make_geojson_payload(project, features or [])
            export_features = geojson_payload["features"]
            _write_artifact_atomic(
                target_path,
                write_dxf,
                export_features,
                project_id=project_identifier,
                record_id=record_id,
            )
        else:
            geojson_payload = _make_geojson_payload(project, features or [])
            export_features = geojson_payload["features"]
            _write_artifact_atomic(
                target_path,
                _write_shp_zip,
                geojson_payload,
                target_path.stem,
                project_identifier,
                record_id,
                project_id=project_identifier,
                record_id=record_id,
            )
        manifest_path = resolve_project_record_file(
            project_identifier,
            "exports",
            record_id,
            "manifest.json",
        )
        write_json_atomic(
            manifest_path,
            _export_manifest(
                project,
                record,
                artifact_name,
                target_path,
                export_features,
                mine_vector_source=mine_vector_source,
                ledger_results=ledger_results,
            ),
            area="exports",
            project_id=project_identifier,
            record_id=record_id,
        )
        record.file_path = artifact_key
        record.status = "completed"
        _append_activity(
            project_identifier,
            "export_created",
            {"export_id": record_id, "format": export_format},
            actor=actor,
            target={"type": "export", "id": str(record_id)},
        )
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "exports",
            project_identifier,
            record_id,
            "final_export_rollback",
        )
        _cleanup_storage_directory(project_identifier, "exports", record_id)
        _mark_export_failed(project_identifier, record_id)
        raise
    return ProjectExportRecordSchema().dump(record)

def list_exports(project_id):
    project = _get_project_or_404(project_id)
    return {"items": ProjectExportRecordSchema(many=True).dump(project.exports), "count": len(project.exports)}

def resolve_project_export_artifact(project_id, export_id):
    """导出制品下载解析：校验记录归属与完成态，返回 (record, 落盘路径)。"""
    record = ProjectExportRecord.query.get(export_id)
    if record is None or record.project_id != project_id:
        raise ProjectStorageValidationError("导出记录不存在")
    if record.status != "completed":
        raise ValueError("导出尚未完成，无法下载")
    # 模型无 artifact_name 列（列表展示由 schema 从 file_path 派生），取存储键末段
    artifact_name = Path(record.file_path or "").name
    if not artifact_name:
        raise ProjectStorageValidationError("导出制品缺失")
    path = resolve_project_record_file(
        project_id, "exports", export_id, artifact_name, require_exists=True
    )
    return record, path
