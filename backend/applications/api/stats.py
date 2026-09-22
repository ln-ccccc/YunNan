"""跨项目统计聚合（M1 主控台看板，2026-09-22）。

GET /api/stats/overview：
- 项目计数（按 lifecycle status 分组，仅未软删）
- 矿山绑定总数、图斑要素总数（每 fid+year 取最新成果，不重复计数）
- 地类占比：按各成果 current FC 的矢量要素球面面积（m²）按类别聚合
- 新增修复面积：各矿山 change_matrix_pixels.csv（草/林净获得像素）× 像元面积
- 疑似异常矿山数：最新两期中草林净流向裸建占比 ≥ SUSPECT_LOSS_RATIO 的矿山

指标口径见 docs/refactor/2026-09-22-console-master-plan.md §4（阈值常量化可调）。
"""
import csv
import json
import logging
import math
from pathlib import Path

import rasterio
from flask import Blueprint

from applications.auth.guard import ensure_logged_in
from applications.common.utils.http import success_api
from applications.extensions import db
from applications.models.project import Project
from applications.models.classification_result import ClassificationResult

stats_api = Blueprint('stats_api', __name__, url_prefix='/api/stats')
LOGGER = logging.getLogger(__name__)

# 类别码 → 类别名（与 kml_roi.change_matrix.CLASS_NAMES 同源）
CLASS_BY_CODE = {0: "grassland", 1: "forest", 2: "building", 3: "road", 4: "bareground", 5: "water"}
RESTORATION_CLASSES = {"grassland", "forest"}
DISTURBANCE_CLASSES = {"building", "bareground"}
# 疑似异常判定：草林净流向裸建像素 / 有效像素总数 ≥ 该比例（计划 §4.3，可调）
SUSPECT_LOSS_RATIO = 0.10

_EARTH_RADIUS_M = 6371008.8


def _spherical_ring_area_m2(rings):
    """球面鞋带公式（近似等积，无 pyproj 依赖）：外环面积 − 内环面积。"""
    total = 0.0
    for ring_index, ring in enumerate(rings):
        points = [
            (math.radians(float(p[0])), math.radians(float(p[1])))
            for p in (ring or [])
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        if len(points) < 3:
            continue
        s = 0.0
        for i in range(len(points)):
            lon1, lat1 = points[i]
            lon2, lat2 = points[(i + 1) % len(points)]
            s += (lon2 - lon1) * (2.0 + math.sin(lat1) + math.sin(lat2))
        ring_area = abs(s) * _EARTH_RADIUS_M * _EARTH_RADIUS_M / 2.0
        total += ring_area if ring_index == 0 else -ring_area
    return max(total, 0.0)


def _polygon_area_m2(geometry):
    gtype = (geometry or {}).get("type")
    if gtype == "Polygon":
        coords = geometry.get("coordinates") or []
        # _spherical_ring_area_m2 接收"环列表"：Polygon 的 coordinates[0] 是外环点列表
        return _spherical_ring_area_m2([coords[0]] if coords else [])
    if gtype == "MultiPolygon":
        return sum(
            _spherical_ring_area_m2([poly[0]] if poly else [])
            for poly in (geometry.get("coordinates") or [])
        )
    return 0.0


def _latest_results_per_fid_year(project_ids):
    """每 (project, fid, year) 最新一条成果（同年多任务不重复计数）。"""
    rows = (
        ClassificationResult.query.filter(
            ClassificationResult.project_id.in_(project_ids)
        )
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    latest = {}
    for result in rows:
        key = (result.project_id, result.mine_fid, result.year)
        if key not in latest:
            latest[key] = result
    return list(latest.values())


def _class_area_share(results):
    area_by_class = {name: 0.0 for name in CLASS_BY_CODE.values()}
    for result in results:
        try:
            collection = json.loads(result.current_feature_collection_json or "{}")
        except (TypeError, ValueError):
            continue
        for feature in (collection.get("features") or []) if isinstance(collection, dict) else []:
            code = (feature or {}).get("properties", {}).get("class_code")
            area_by_class[CLASS_BY_CODE.get(code, "")] = (
                area_by_class.get(CLASS_BY_CODE.get(code, ""), 0.0)
                + _polygon_area_m2(feature.get("geometry"))
            )
    total = sum(area_by_class.values())
    share = {
        name: round(area / total * 100.0, 2) if total > 0 else 0.0
        for name, area in area_by_class.items()
    }
    return {"total_area_m2": round(total, 1), "percent": share}


def _read_change_matrix_pixels(fid_dir: Path):
    csv_path = fid_dir / "change_matrix_pixels.csv"
    if not csv_path.is_file():
        return None
    try:
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except OSError:
        return None
    if len(rows) < 2:
        return None
    header = [cell.strip() for cell in rows[0][1:]]
    matrix = {}
    for row in rows[1:]:
        matrix[row[0].strip()] = {
            header[i]: int(row[i + 1]) for i in range(len(header)) if row[i + 1].strip().isdigit()
        }
    return matrix


def _pixel_area_m2(fid_dir: Path):
    """像元面积：最新 label.tif 的 transform（度→米按中心纬度换算）。"""
    labels = sorted(fid_dir.glob("*_label.tif"))
    if not labels:
        return None
    try:
        with rasterio.open(labels[-1]) as src:
            center_lat = math.radians((src.bounds.top + src.bounds.bottom) / 2.0)
            return abs(src.transform.a) * abs(src.transform.e) * (111320.0 ** 2) * math.cos(center_lat)
    except (OSError, ValueError):
        return None


def _change_metrics(project_root: Path):
    """新增修复面积（m²）与疑似异常矿山数（按计划 §4.3/§4.4 口径）。"""
    restoration_m2 = 0.0
    suspect_fids = []
    inference_root = project_root / "outputs" / "inference"
    if not inference_root.is_dir():
        return {"restored_area_m2": 0.0, "suspect_mine_count": 0, "suspect_fids": []}
    for fid_dir in inference_root.iterdir():
        if not fid_dir.is_dir():
            continue
        matrix = _read_change_matrix_pixels(fid_dir)
        if matrix is None:
            continue
        gained = 0
        lost = 0
        to_disturbance = 0
        total_valid = 0
        for old_class, transitions in matrix.items():
            for new_class, pixels in transitions.items():
                total_valid += pixels
                if old_class not in RESTORATION_CLASSES and new_class in RESTORATION_CLASSES:
                    gained += pixels
                if old_class in RESTORATION_CLASSES and new_class not in RESTORATION_CLASSES:
                    lost += pixels
                if (
                    old_class in RESTORATION_CLASSES
                    and new_class in DISTURBANCE_CLASSES
                ):
                    to_disturbance += pixels
        pixel_area = _pixel_area_m2(fid_dir)
        if pixel_area:
            restoration_m2 += (gained - lost) * pixel_area
        if total_valid > 0 and to_disturbance / total_valid >= SUSPECT_LOSS_RATIO:
            suspect_fids.append(fid_dir.name)
    return {
        "restored_area_m2": round(max(restoration_m2, 0.0), 1),
        "suspect_mine_count": len(suspect_fids),
        "suspect_fids": sorted(suspect_fids, key=str),
    }


def build_stats_overview(storage_root: Path = None):
    projects = Project.query.filter_by(deleted_at=None).all()
    project_counts = {"draft": 0, "active": 0, "completed": 0, "archived": 0}
    for project in projects:
        project_counts[project.status] = project_counts.get(project.status, 0) + 1

    active_projects = [p for p in projects if p.status != "archived"]
    mine_total = sum(len(p.mines) for p in active_projects)

    from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

    results = _latest_results_per_fid_year([p.id for p in active_projects])
    feature_total = sum(result.feature_count or 0 for result in results)

    overview = {
        "project_counts": project_counts,
        "project_total": len(projects),
        "mine_total": mine_total,
        "feature_total": feature_total,
        "class_area": _class_area_share(results),
    }

    root = storage_root or get_storage_root()
    restored_m2 = 0.0
    suspect_fids = []
    for project in active_projects:
        project_root = resolve_storage_path(root, Path("projects") / str(project.id))
        metrics = _change_metrics(project_root)
        restored_m2 += metrics["restored_area_m2"]
        suspect_fids.extend(f"{project.id}:{fid}" for fid in metrics["suspect_fids"])
    overview["restored_area_m2"] = round(max(restored_m2, 0.0), 1)
    overview["suspect_mine_count"] = len(suspect_fids)
    return overview


@stats_api.before_request
def require_stats_auth():
    return ensure_logged_in()


@stats_api.get('/overview')
def stats_overview_api():
    return success_api(data=build_stats_overview())
