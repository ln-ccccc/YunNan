# -*- coding: utf-8 -*-
"""分类成果统计口径改造（2026-10-31 方案 b）。

人工在编辑器修订矢量后，按审校后矢量重算两类统计并回写发布产物：
- 地类占比：球面鞋带面积（复用 quality_review.geometry_area_m2，与 stats/质检
  同源口径）按 class_code 聚合归一化，写入 class_ratio_percent.json 的 revised
  块——推理口径 series_percent 原值保留，消费端优先 revised、无则回落。
- 变化矩阵：把各年审校后矢量（current FC，v0 auto 亦属矢量口径）rasterize 到
  该年 label.tif 网格，逐像素转移计数，覆盖更新 outputs/change_matrix/{fid}.json
  并标记 basis="revised_vector"；推理口径 CSV 原文件保留。

失败策略：统计产物非关键数据（溯源/趋势均有推理口径回落），重算失败 suppress
+ 日志，绝不阻断保存事务。
"""
import json
import logging
import os
import uuid
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom

from applications.kml_roi.change_matrix import CLASS_NAMES
from applications.project_hub.quality_review import geometry_area_m2
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

LOGGER = logging.getLogger(__name__)

_AREA_SOURCE_REVISED = "vector_shoelace"
_AREA_SOURCE_INFERENCE = "inference_mask"


def _inference_fid_dir(project_id, mine_fid):
    return resolve_storage_path(
        get_storage_root(),
        Path("projects") / str(project_id) / "outputs" / "inference" / str(mine_fid),
    )


def _atomic_write_json(target_dir, filename, payload):
    target_dir.mkdir(parents=True, exist_ok=True)
    temporary = target_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, target_dir / filename)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_ratio_series(ratio_doc):
    """占比序列的口径解析：有审校块优先返回审校序列，否则推理口径。

    返回 (series_percent, area_source, revised_revision_no)；ratio_doc 缺失时
    返回 (None, None, None)，调用方按原容错处理。
    """
    if not isinstance(ratio_doc, dict):
        return None, None, None
    revised = ratio_doc.get("revised")
    if isinstance(revised, dict) and revised.get("series_percent"):
        return (
            revised.get("series_percent"),
            revised.get("area_source") or _AREA_SOURCE_REVISED,
            revised.get("revision_no"),
        )
    return ratio_doc.get("series_percent"), _AREA_SOURCE_INFERENCE, None


def compute_vector_class_ratio(feature_collection):
    """审校后矢量的地类占比：球面鞋带 m² 按 class_code 聚合，归一化 0-100。

    无有效几何（空集合/全坏要素）返回 None，调用方跳过回写。
    """
    areas = {name: 0.0 for name in CLASS_NAMES}
    for feature in (feature_collection or {}).get("features") or []:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        class_code = properties.get("class_code")
        if not isinstance(class_code, int) or not 0 <= class_code < len(CLASS_NAMES):
            continue
        areas[CLASS_NAMES[class_code]] += geometry_area_m2(feature.get("geometry"))
    total = sum(areas.values())
    if total <= 0:
        return None
    return {name: round(areas[name] / total * 100.0, 2) for name in CLASS_NAMES}


def _effective_results_by_year(project_id, mine_fid):
    """同年多成果的选择规则（与溯源同源）：按 id 降序取最新；vector_failed
    成果在同城存在非 failed 兄弟时跳过。"""
    from applications.models.classification_result import ClassificationResult

    rows = (
        ClassificationResult.query.filter_by(project_id=project_id, mine_fid=mine_fid)
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    latest_by_year = {}
    for result in rows:
        if result.year in latest_by_year:
            continue
        if result.vector_status == "vector_failed" and any(
            other.year == result.year and other.vector_status != "vector_failed"
            for other in rows
        ):
            continue
        latest_by_year[result.year] = result
    return latest_by_year


def _acquire_stats_publish_lock(project_id):
    """统计发布互斥：锁项目行串行化同项目的占比/矩阵回写，封"校验通过后
    写文件"的并发窗口。SQLite 忽略 FOR UPDATE（由版本校验兜底）；MySQL
    InnoDB 真互斥。"""
    from applications.extensions import db
    from applications.models.project import Project

    db.session.query(Project.id).filter_by(id=int(project_id)).with_for_update().first()


def _revision_still_publishable(result_id, revision_no, effective_result):
    """统计发布的版本校验：(result_id, revision_no) 必须仍是该成果当前修订号，
    且该成果必须仍是该年有效成果——过期修订与历史成果的回写一律拒绝。"""
    from applications.models.classification_result import ClassificationResult

    result = ClassificationResult.query.filter_by(id=int(result_id)).first()
    if result is None:
        return False
    if int(result.current_revision_no or 0) != int(revision_no):
        return False
    return effective_result is not None and effective_result.id == result.id


def _merge_per_year_revisions(payload, years):
    """从现有 revised 块收集逐年份审校值。

    旧格式（无 per_year，仅 year/series_percent 单年覆盖）按当年索引反推一条
    记录实现自愈——旧格式语义就是"只有 revised.year 一个年份被审校"。
    """
    revised = payload.get("revised")
    per_year = {}
    if not isinstance(revised, dict):
        return per_year
    stored = revised.get("per_year")
    if isinstance(stored, dict):
        for key, entry in stored.items():
            if isinstance(entry, dict):
                per_year[str(key)] = entry
    if not per_year:
        legacy_year = revised.get("year")
        legacy_series = revised.get("series_percent")
        if (
            isinstance(legacy_year, int)
            and legacy_year in years
            and isinstance(legacy_series, dict)
        ):
            index = years.index(legacy_year)
            percent = {}
            for name in CLASS_NAMES:
                series = legacy_series.get(name)
                if isinstance(series, list) and index < len(series):
                    percent[name] = series[index]
            if percent:
                per_year[str(legacy_year)] = {
                    "revision_no": revised.get("revision_no"),
                    "percent": percent,
                }
    return per_year


def _apply_per_year_to_series(series, per_year, years, name):
    for key, entry in per_year.items():
        try:
            index = years.index(int(key))
        except (TypeError, ValueError):
            continue
        percent = entry.get("percent") if isinstance(entry, dict) else None
        if percent is None:
            series[index] = 0.0
        elif isinstance(percent, dict) and name in percent:
            series[index] = float(percent[name])
    return series


def apply_revised_class_ratio(
    project_id, mine_fid, year, revision_no, feature_collection, result_id=None
):
    """保存修订后把审校占比写入发布产物的 revised 块（按年索引定位，原子写）。

    - 逐年份合并（问题7）：revised.per_year 持久化各年份审校值，后一次保存
      只覆盖自己年份，其他年份已审校值不回退；旧格式块自愈补建。
    - 空成果显式空状态（问题7）：compute_vector_class_ratio 返回 None（空
      集合/全坏要素）时写入该年全 0 + per_year empty 标记，不保留旧占比
      伪装成当前结果。
    - 版本保护（问题8）：result_id 提供时先锁项目行，再校验 (result_id,
      revision_no) 仍是当前修订号且该成果仍是该年有效成果；过期修订或历史
      成果的回写被拒绝。

    返回 True/False 供日志与补算脚本判断；json 缺失（如旧任务未生成）静默跳过。
    """
    vector_ratio = compute_vector_class_ratio(feature_collection)
    locked = result_id is not None
    if locked:
        from applications.extensions import db

        _acquire_stats_publish_lock(project_id)
    try:
        fid_dir = _inference_fid_dir(project_id, mine_fid)
        ratio_path = fid_dir / "class_ratio_percent.json"
        if not ratio_path.is_file():
            LOGGER.info(
                "审校占比跳过（推理占比文件不存在）project_id=%s mine_fid=%s", project_id, mine_fid
            )
            return False
        if locked and not _revision_still_publishable(
            result_id,
            revision_no,
            _effective_results_by_year(project_id, mine_fid).get(int(year)),
        ):
            LOGGER.info(
                "审校占比回写跳过（修订已过期或非当前有效成果）project_id=%s "
                "mine_fid=%s year=%s result_id=%s revision_no=%s",
                project_id, mine_fid, year, result_id, revision_no,
            )
            return False
        with open(ratio_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        years = [int(value) for value in (payload.get("years") or [])]
        try:
            years.index(int(year))
        except ValueError:
            LOGGER.info(
                "审校占比跳过（修订年份 %s 不在占比序列 %s）project_id=%s mine_fid=%s",
                year, years, project_id, mine_fid,
            )
            return False
        per_year = _merge_per_year_revisions(payload, years)
        per_year[str(int(year))] = {
            "revision_no": int(revision_no),
            "percent": vector_ratio,
            "empty": vector_ratio is None,
        }
        original = payload.get("series_percent") or {}
        revised_series = {}
        for name in CLASS_NAMES:
            series = [float(value) for value in (original.get(name) or [])]
            while len(series) < len(years):
                series.append(0.0)
            revised_series[name] = _apply_per_year_to_series(series, per_year, years, name)
        payload["revised"] = {
            "revision_no": int(revision_no),
            "area_source": _AREA_SOURCE_REVISED,
            "year": int(year),
            "series_percent": revised_series,
            "per_year": per_year,
        }
        _atomic_write_json(fid_dir, "class_ratio_percent.json", payload)
        return True
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning(
            "审校占比回写失败 project_id=%s mine_fid=%s year=%s: %s",
            project_id, mine_fid, year, exc,
        )
        return False
    finally:
        if locked:
            try:
                db.session.commit()
            except Exception:  # noqa: BLE001 - 释放锁失败不掩盖主流程返回值
                db.session.rollback()


def rasterize_year_vector(project_id, mine_fid, year, feature_collection, target_grid=None):
    """审校矢量栅格化：类别值 0-5、无效区 255。

    target_grid=None 时用该年自身 label.tif 网格；提供 (transform, shape, crs)
    时强制栅格化到统一网格——跨 CRS/跨分辨率年份的转移矩阵必须同网格对齐，
    否则逐像素对比在空间上不成立（2026-10-31 数值审查发现的静默错误源）。
    """
    label_path = _inference_fid_dir(project_id, mine_fid) / f"{mine_fid}+{year}_label.tif"
    if not label_path.is_file():
        return None
    shapes = []
    with rasterio.open(label_path) as dataset:
        grid_transform = target_grid[0] if target_grid else dataset.transform
        grid_shape = target_grid[1] if target_grid else dataset.shape
        grid_crs = target_grid[2] if target_grid else dataset.crs
        for feature in (feature_collection or {}).get("features") or []:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties") or {}
            class_code = properties.get("class_code")
            geometry = feature.get("geometry")
            if not isinstance(class_code, int) or not 0 <= class_code < len(CLASS_NAMES):
                continue
            if not isinstance(geometry, dict):
                continue
            shapes.append((transform_geom("EPSG:4326", grid_crs, geometry), class_code))
        if not shapes:
            return None
        return rasterize(
            shapes,
            out_shape=grid_shape,
            transform=grid_transform,
            fill=255,
            dtype="uint8",
        )


def _change_matrix_path(project_id, mine_fid):
    output_root = resolve_storage_path(
        get_storage_root(), Path("projects") / str(project_id) / "outputs"
    )
    return output_root / "change_matrix" / f"{mine_fid}.json"


def _invalidate_revised_matrix_if_stale(project_id, mine_fid, reason):
    """当前矢量内容已无法构成矩阵时，若现有矩阵仍标记为审校口径，则显式失效
    （问题7 空成果路径）：旧审校矩阵不得伪装成当前结果。推理口径矩阵来自
    label 产物、仍然有效，不受影响。
    """
    summary_path = _change_matrix_path(project_id, mine_fid)
    previous = {}
    if summary_path.is_file():
        try:
            previous = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    if previous.get("basis") != "revised_vector":
        return False
    payload = {
        "fid": str(mine_fid),
        "old_year": previous.get("old_year"),
        "new_year": previous.get("new_year"),
        "headers": list(CLASS_NAMES),
        "matrix": [],
        "images": previous.get("images") or {"old": None, "new": None},
        "has_change_matrix": False,
        "data_source": "project_inference",
        "basis": "revised_vector_invalid",
        "invalid_reason": reason,
    }
    _atomic_write_json(summary_path.parent, summary_path.name, payload)
    LOGGER.info(
        "审校矩阵失效（%s）project_id=%s mine_fid=%s", reason, project_id, mine_fid
    )
    return False


def compute_revised_change_matrix(project_id, mine_fid):
    """各年审校矢量栅格化后的地类转移矩阵（最近两年，行=旧年、列=新年）。

    与溯源同一取数规则：同年多成果取最新非 vector_failed；结构性缺失（不足
    两年、label.tif 缺失）跳过并保持原矩阵文件（推理口径回落）。当前有效
    矢量内容为空（无法栅格化/无有效重叠）且现有矩阵是审校口径时，矩阵被
    显式失效而非保留旧值（问题7）。

    读取与写入全程持项目行锁（问题8）：并发回写在 MySQL InnoDB 下串行化，
    避免"读到旧状态、写成旧矩阵"覆盖新结果。返回 True 表示写了审校矩阵。
    """
    from applications.extensions import db

    _acquire_stats_publish_lock(project_id)
    try:
        latest_by_year = _effective_results_by_year(project_id, mine_fid)
        years_sorted = sorted(latest_by_year)
        if len(years_sorted) < 2:
            return False

        import json as _json

        # 统一目标网格（2026-10-31 数值审查修正）：取旧年的 label 网格，两期矢量
        # 都栅格化到同一 transform/CRS——不同来源影像的 label 网格 CRS/分辨率不同
        # （GEHI=4326 度、用户 573M=3857 米），各自网格逐像素对比在空间上不成立。
        def _grid_of(year_value):
            label_path = _inference_fid_dir(project_id, mine_fid) / f"{mine_fid}+{year_value}_label.tif"
            if not label_path.is_file():
                return None
            with rasterio.open(label_path) as dataset:
                return dataset.transform, dataset.shape, dataset.crs

        old_year_value = years_sorted[-2]
        target_grid = _grid_of(old_year_value)
        if target_grid is None:
            LOGGER.info(
                "审校矩阵跳过（旧年 %s label 缺失）project_id=%s mine_fid=%s",
                old_year_value, project_id, mine_fid,
            )
            return False

        rasters = []
        for year in years_sorted[-2:]:
            result = latest_by_year[year]
            if (
                _inference_fid_dir(project_id, mine_fid) / f"{mine_fid}+{year}_label.tif"
            ).is_file():
                structural_label_present = True
            else:
                structural_label_present = False
            if not structural_label_present:
                LOGGER.info(
                    "审校矩阵跳过（年份 %s label 缺失）project_id=%s mine_fid=%s",
                    year, project_id, mine_fid,
                )
                return False
            try:
                collection = _json.loads(result.current_feature_collection_json or "null")
            except (TypeError, ValueError):
                collection = None
            raster = rasterize_year_vector(
                project_id, mine_fid, year, collection, target_grid=target_grid
            )
            if raster is None:
                # label 存在但矢量栅格化为空 → 当前内容级空态，需失效旧审校矩阵
                return _invalidate_revised_matrix_if_stale(
                    project_id, mine_fid, f"empty_vector_year_{year}"
                )
            rasters.append((int(year), raster, int(result.current_revision_no or 0)))

        old_year, old_raster, old_revision = rasters[-2]
        new_year, new_raster, new_revision = rasters[-1]

        class_count = len(CLASS_NAMES)
        valid = (old_raster < class_count) & (new_raster < class_count)
        total_valid = int(valid.sum())
        if total_valid <= 0:
            return _invalidate_revised_matrix_if_stale(
                project_id, mine_fid, "no_valid_overlap"
            )
        pairs = (old_raster[valid].astype(np.int64), new_raster[valid].astype(np.int64))
        pixels = np.zeros((class_count, class_count), dtype=np.int64)
        np.add.at(pixels, pairs, 1)
        row_totals = pixels.sum(axis=1, keepdims=True)
        percent = np.divide(
            pixels.astype(np.float64),
            row_totals,
            out=np.zeros_like(pixels, dtype=np.float64),
            where=row_totals > 0,
        ) * 100.0

        summary_path = _change_matrix_path(project_id, mine_fid)
        previous = {}
        if summary_path.is_file():
            try:
                previous = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
        payload = {
            "fid": str(mine_fid),
            "old_year": old_year,
            "new_year": new_year,
            "headers": list(CLASS_NAMES),
            "matrix": [
                {
                    "label": CLASS_NAMES[index],
                    "values": [round(float(value), 1) for value in percent[index]],
                }
                for index in range(class_count)
            ],
            "images": previous.get("images") or {"old": None, "new": None},
            "has_change_matrix": True,
            "data_source": "project_inference",
            "basis": "revised_vector",
            "area_source": _AREA_SOURCE_REVISED,
            "revised_revision_no": {str(old_year): old_revision, str(new_year): new_revision},
        }
        _atomic_write_json(summary_path.parent, summary_path.name, payload)
        return True
    finally:
        try:
            db.session.commit()
        except Exception:  # noqa: BLE001 - 释放锁失败不掩盖主结果
            db.session.rollback()
