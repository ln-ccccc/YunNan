import csv
from contextlib import suppress
import json
import logging
import math
import os
import shutil
import uuid
from pathlib import Path

from applications.extensions import db
from applications.kml_roi.change_matrix import write_change_matrix_csv, write_class_ratio_json
from applications.kml_roi.tiles import scan_year_masks
from applications.models.project import Project, ProjectDataset
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


LOGGER = logging.getLogger(__name__)


def _parse_year(value):
    text = str(value or "").strip()
    if len(text) != 4 or not text.isdigit():
        raise ValueError("项目推理结果缺少有效年份")
    return int(text)


def _is_year_image(fid, filename):
    prefix = f"{fid}+"
    if not filename.startswith(prefix) or not filename.endswith(".png"):
        return False
    try:
        _parse_year(filename[len(prefix):-len(".png")])
    except ValueError:
        return False
    return True


def _is_label_geotiff(fid, filename):
    prefix = f"{fid}+"
    suffix = "_label.tif"
    if not filename.startswith(prefix) or not filename.endswith(suffix):
        return False
    try:
        _parse_year(filename[len(prefix):-len(suffix)])
    except ValueError:
        return False
    return True


def _is_publishable_inference_asset(fid, filename):
    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".json", ".csv"}:
        return True
    return suffix == ".tif" and _is_label_geotiff(fid, filename)


def _parse_matrix(path):
    if not path.is_file():
        return [], []
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    if len(rows) < 2:
        return [], []
    headers = rows[0][1:]
    matrix = []
    for row in rows[1:]:
        if not row:
            continue
        matrix.append({
            "label": row[0],
            "values": [float(value) for value in row[1:]],
        })
    return headers, matrix


def _asset_url(project_id, fid, filename):
    return f"/api/projects/{project_id}/outputs/inference/{fid}/{filename}"


def _write_json_temporary(parent, payload):
    parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / f".{uuid.uuid4().hex}.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return temporary


def _upsert_inference_dataset(project_id, fid, year, output_dir, files):
    dataset = ProjectDataset.query.filter_by(
        project_id=project_id,
        dataset_kind="inference_result",
        mine_fid=fid,
        year_start=year,
        year_end=year,
    ).first()
    if dataset is None:
        dataset = ProjectDataset(
            project_id=project_id,
            dataset_kind="inference_result",
            mine_fid=fid,
            year_start=year,
            year_end=year,
        )
        db.session.add(dataset)
    dataset.display_name = f"地物分类 FID {fid} {year}"
    dataset.file_path = str(output_dir)
    dataset.source_format = "classification_dir"
    dataset.slice_config_json = json.dumps(
        {"year": year, "files": sorted(files)},
        ensure_ascii=False,
    )
    return dataset


def _link_inference_dataset_to_classification_result(dataset, classification_result):
    try:
        metadata = json.loads(dataset.slice_config_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(
        {
            "classification_result_id": classification_result["result_id"],
            "vector_status": classification_result["vector_status"],
            "vector_error": classification_result["vector_error"],
        }
    )
    dataset.slice_config_json = json.dumps(metadata, ensure_ascii=False)
    db.session.commit()


def _prepare_fid_publication(project_id, fid, stage_dir, target_dir):
    publication_dir = target_dir.parent / f".{fid}.publish-{uuid.uuid4().hex}"
    if target_dir.is_dir():
        shutil.copytree(target_dir, publication_dir)
    else:
        publication_dir.mkdir(parents=True)

    copied = []
    for source in stage_dir.iterdir():
        if not source.is_file():
            continue
        if not source.name.startswith(f"{fid}+"):
            continue
        if not _is_publishable_inference_asset(fid, source.name):
            continue
        shutil.copy2(source, publication_dir / source.name)
        copied.append(source.name)

    for source in stage_dir.iterdir():
        if not source.is_file() or not _is_year_image(fid, source.name):
            continue
        label_name = f"{source.stem}_label.tif"
        if not (stage_dir / label_name).is_file():
            with suppress(OSError):
                (publication_dir / label_name).unlink()

    for published in publication_dir.iterdir():
        if published.is_file() and published.suffix.lower() == ".tif" and not _is_label_geotiff(fid, published.name):
            with suppress(OSError):
                published.unlink()
    if not copied:
        shutil.rmtree(publication_dir, ignore_errors=True)
        raise ValueError(f"FID {fid} 没有可发布的推理文件")

    year_masks = scan_year_masks(str(fid), publication_dir)
    if len(year_masks) >= 2:
        _, _, old_mask = year_masks[-2]
        _, _, new_mask = year_masks[-1]
        write_change_matrix_csv(old_mask, new_mask, publication_dir)
    if year_masks:
        write_class_ratio_json(fid=str(fid), year_masks=year_masks, out_dir=publication_dir)
    return publication_dir, year_masks, copied


def publish_project_inference_result(project_id, request_payload, pipeline_summary):
    project = Project.query.filter_by(id=int(project_id), deleted_at=None).first()
    if project is None:
        raise ValueError("项目不存在")
    bound_fids = {binding.mine_fid for binding in project.mines}
    requested_fids = {int(fid) for fid in request_payload.get("mine_fids") or []}
    written_fids = sorted({int(fid) for fid in pipeline_summary.get("written_fid_list") or []})
    if not written_fids:
        return {"synced_fids": [], "display_results": []}
    if any(fid not in requested_fids or fid not in bound_fids for fid in written_fids):
        raise ValueError("推理结果包含不属于当前项目的矿山 FID")

    year = _parse_year(request_payload.get("year"))
    stage_root = Path(pipeline_summary.get("output_root") or "").expanduser().resolve()
    if not stage_root.is_dir():
        raise ValueError("推理暂存结果目录不存在")
    storage_root = get_storage_root()
    final_root = resolve_storage_path(
        storage_root,
        Path("projects") / str(project.id) / "outputs" / "inference",
    )
    requested_output_root = Path(request_payload.get("output_root") or "").expanduser().resolve()
    if requested_output_root != final_root:
        raise ValueError("项目推理输出目录不匹配")
    final_root.mkdir(parents=True, exist_ok=True)
    summary_root = resolve_storage_path(
        storage_root,
        Path("projects") / str(project.id) / "outputs" / "change_matrix",
    )
    summary_root.mkdir(parents=True, exist_ok=True)

    display_results = []
    synced_fids = []
    classification_results = []
    for fid in written_fids:
        stage_dir = stage_root / str(fid)
        if not stage_dir.is_dir():
            raise ValueError(f"FID {fid} 的推理暂存目录不存在")
        target_dir = final_root / str(fid)
        publication_dir, year_masks, copied = _prepare_fid_publication(
            project.id,
            fid,
            stage_dir,
            target_dir,
        )
        latest = year_masks[-1] if year_masks else None
        previous = year_masks[-2] if len(year_masks) >= 2 else None
        headers, matrix = _parse_matrix(publication_dir / "change_matrix_percent_rownorm.csv")
        old_image = previous[1] if previous and previous[1].is_file() else None
        new_image = latest[1] if latest and latest[1].is_file() else None
        summary_payload = {
            "fid": fid,
            "old_year": previous[0] if previous else None,
            "new_year": latest[0] if latest else None,
            "headers": headers if previous else [],
            "matrix": matrix if previous else [],
            "images": {
                "old": _asset_url(project.id, fid, old_image.name) if old_image else None,
                "new": _asset_url(project.id, fid, new_image.name) if new_image else None,
            },
            "has_change_matrix": bool(previous and headers and matrix),
            "data_source": "project_inference",
        }
        summary_temp = _write_json_temporary(summary_root, summary_payload)
        summary_target = summary_root / f"{fid}.json"
        directory_backup = final_root / f".{fid}.backup-{uuid.uuid4().hex}"
        summary_backup = summary_root / f".{fid}.backup-{uuid.uuid4().hex}.json"
        had_directory = target_dir.exists()
        had_summary = summary_target.exists()
        inference_dataset = None
        try:
            if had_directory:
                os.replace(target_dir, directory_backup)
            os.replace(publication_dir, target_dir)
            if had_summary:
                os.replace(summary_target, summary_backup)
            os.replace(summary_temp, summary_target)
            inference_dataset = _upsert_inference_dataset(project.id, fid, year, target_dir, copied)
            db.session.commit()
        except Exception:
            db.session.rollback()
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            if had_directory and directory_backup.exists():
                os.replace(directory_backup, target_dir)
            if summary_target.exists():
                summary_target.unlink()
            if had_summary and summary_backup.exists():
                os.replace(summary_backup, summary_target)
            shutil.rmtree(publication_dir, ignore_errors=True)
            summary_temp.unlink(missing_ok=True)
            raise
        else:
            shutil.rmtree(directory_backup, ignore_errors=True)
            summary_backup.unlink(missing_ok=True)

        current_image = target_dir / f"{fid}+{year}.png"
        current_source = target_dir / f"{fid}+{year}_src.png"
        display_result = {
            "fid": fid,
            "year": year,
            "before_img": _asset_url(project.id, fid, current_source.name)
            if current_source.is_file() else None,
            "after_img": _asset_url(project.id, fid, current_image.name)
            if current_image.is_file() else None,
            "result_id": None,
            "vector_status": None,
            "vector_error": None,
        }
        display_results.append(display_result)
        if request_payload.get("inference_job_id") and request_payload.get("mine_resource_id"):
            try:
                from applications.project_hub.classification_results import publish_classification_result

                classification_result = publish_classification_result(
                    project_id=project.id,
                    fid=fid,
                    year=year,
                    inference_job_id=request_payload["inference_job_id"],
                    mine_resource_id=request_payload["mine_resource_id"],
                    model_id=request_payload.get("model_id") or "cc-ln/CUGRS",
                    label_path=target_dir / f"{fid}+{year}_label.tif",
                )
            except Exception:
                db.session.rollback()
                LOGGER.warning(
                    "分类成果矢量化发布失败，已保留 PNG 推理结果: project_id=%s fid=%s",
                    project.id,
                    fid,
                    exc_info=True,
                )
            else:
                classification_results.append(classification_result)
                display_result.update(
                    {
                        "result_id": classification_result["result_id"],
                        "vector_status": classification_result["vector_status"],
                        "vector_error": classification_result["vector_error"],
                    }
                )
                try:
                    _link_inference_dataset_to_classification_result(
                        inference_dataset,
                        classification_result,
                    )
                except Exception:
                    db.session.rollback()
                    LOGGER.warning(
                        "分类成果历史关联写入失败，分类成果仍可通过项目成果查询: project_id=%s fid=%s",
                        project.id,
                        fid,
                        exc_info=True,
                    )
        synced_fids.append(fid)

    return {
        "synced_fids": synced_fids,
        "display_results": display_results,
        "classification_results": classification_results,
    }


def _index_summary(points):
    values = [float(point["value"]) for point in points]
    years = [int(point["year"]) for point in points]
    mean = sum(values) / len(values)
    if len(points) < 2:
        trend = 0.0
    else:
        mean_year = sum(years) / len(years)
        denominator = sum((year - mean_year) ** 2 for year in years)
        trend = (
            sum((year - mean_year) * (value - mean) for year, value in zip(years, values))
            / denominator
            if denominator
            else 0.0
        )
    if trend > 1e-12:
        mk_trend = "upward"
    elif trend < -1e-12:
        mk_trend = "downward"
    else:
        mk_trend = "stable"
    return {
        "data": points,
        "mean": mean,
        "trend": trend,
        "mk_trend": mk_trend,
        "available": True,
        "source_file": "project_inference",
        "reason": None,
        "message": "",
    }


def upsert_project_index_results(project_id, index_type, year, fid_stats):
    project = Project.query.filter_by(id=int(project_id), deleted_at=None).first()
    if project is None:
        raise ValueError("项目不存在")
    index_key = str(index_type or "").strip().lower()
    if index_key not in {"ndvi", "ndbi", "ndwi", "ndsi"}:
        raise ValueError("不支持的指数类型")
    year_value = _parse_year(year)
    bound_fids = {binding.mine_fid for binding in project.mines}
    normalized = []
    for row in fid_stats or []:
        fid = int(row.get("fid"))
        value = float(row.get("mean"))
        if fid not in bound_fids:
            raise ValueError("指数结果包含不属于当前项目的矿山 FID")
        if not math.isfinite(value):
            raise ValueError("指数结果包含无效数值")
        normalized.append((fid, value))

    output_root = resolve_storage_path(
        get_storage_root(),
        Path("projects") / str(project.id) / "outputs" / "indices",
    )
    output_root.mkdir(parents=True, exist_ok=True)
    synced_fids = []
    for fid, value in normalized:
        target = output_root / f"{fid}.json"
        if target.is_file():
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"FID {fid} 的项目指数文件无法读取") from exc
        else:
            payload = {"fid": fid}
        current = payload.get(index_key) or {}
        by_year = {
            int(point["year"]): float(point["value"])
            for point in current.get("data") or []
        }
        by_year[year_value] = value
        points = [
            {"year": point_year, "value": by_year[point_year]}
            for point_year in sorted(by_year)
        ]
        payload["fid"] = fid
        payload[index_key] = _index_summary(points)
        temporary = _write_json_temporary(output_root, payload)
        os.replace(temporary, target)
        synced_fids.append(fid)
    return {"synced_fids": sorted(set(synced_fids)), "index_type": index_key.upper(), "year": year_value}
