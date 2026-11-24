"""跨项目统计聚合（M1 主控台看板，2026-09-22）。

GET /api/stats/overview：
- 项目计数（按 lifecycle status 分组，仅未软删）
- 矿山绑定总数、图斑要素总数（每 fid+year 取最新成果，不重复计数）
- 地类占比：按各成果 current FC 的矢量要素球面面积（m²）按类别聚合；逐成果的面积记在
  项目派生目录里，成果没变直接取用（2026-11-24，见 _class_area_share）
- 新增修复面积：各矿山 change_matrix_pixels.csv（草/林净获得像素）× 像元面积
- 疑似异常矿山数：最新两期中草林净流向裸建占比 ≥ SUSPECT_LOSS_RATIO 的矿山

指标口径见 docs/refactor/2026-09-22-console-master-plan.md §4（阈值常量化可调）。
"""
import csv
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import rasterio
from flask import Blueprint, current_app, has_app_context
from sqlalchemy.orm import load_only

from applications.auth.guard import ensure_logged_in
from applications.common.utils.http import success_api
from applications.extensions import db
from applications.models.project import Project
from applications.models.classification_result import ClassificationResult

stats_api = Blueprint('stats_api', __name__, url_prefix='/api/stats')
LOGGER = logging.getLogger(__name__)

# 概览聚合是纯读重计算。2026-11-22 实测约 10s（BFF 15s 超时 502）时先加了模块级 TTL 缓存：
# 跨进程失效不可行，至多 _TTL 秒有界陈旧；TESTING 旁路避免用例间共享缓存假绿。
# 2026-11-24 再把重算本身降下来（见 _class_area_share / _change_metrics），缓存过期后的
# 那一次重算不再拖慢同一 worker 上的其它接口
_OVERVIEW_CACHE_TTL_SECONDS = 120
_OVERVIEW_CACHE = {"expires_at": 0.0, "payload": None}

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
    """每 (project, fid, year) 最新一条成果（同年多任务不重复计数）。

    只取去重所需的轻量列：整行加载会连带拉出 MEDIUMTEXT 的
    current_feature_collection_json（单成果矢量 64KB+，随人工保存无上限增长），
    且去重会加载全部历史行再丢弃大部分——看板是热路径，属无界全量加载
    （AGENTS §10）；消费者按需再载重列。
    """
    rows = (
        ClassificationResult.query.filter(
            ClassificationResult.project_id.in_(project_ids)
        )
        .options(
            load_only(
                ClassificationResult.id,
                ClassificationResult.project_id,
                ClassificationResult.mine_fid,
                ClassificationResult.year,
                ClassificationResult.vector_status,
                ClassificationResult.feature_count,
                ClassificationResult.current_revision_no,
                ClassificationResult.update_time,
            )
        )
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    latest = {}
    has_ready = set()
    for result in rows:
        key = (result.project_id, result.mine_fid, result.year)
        if result.vector_status != "vector_failed":
            has_ready.add(key)
    for result in rows:
        key = (result.project_id, result.mine_fid, result.year)
        if key not in latest:
            if result.vector_status == "vector_failed" and key in has_ready:
                continue
            latest[key] = result
    return list(latest.values())


# 逐成果地类面积的记忆（2026-11-24）。概览此前每次缓存过期都要把全库有效成果的矢量 JSON
# （实测 644 条、167.8MB、440 万个顶点）取回、解析、逐点求面积：约 9 秒，其中六秒多是进程
# CPU，期间拖慢同一 worker 上的其它接口。成果几乎不变，所以把"每个有效成果各类别的面积"按
# 成果记在项目自己的派生目录，并带一个能判断成果是否变过的签名。没变的直接取用，变了的
# 只重算那几条。面积函数不变；与逐次全量计算相比只有求和次序不同带来的浮点末位差异。
# 签名 = 修订号 + 图斑数 + 矢量状态 + 行更新时间：发布、保存修订、标记失败三条写入路径
# 都至少改动其中一项。绕过保存流程直接改库且四项都不变（同秒、同图斑数、不升修订号）不会
# 被察觉——生产没有这样的写入路径。
_AREA_MEMO_VERSION = 1
_AREA_MEMO_FILE = "class_area_by_result.json"
# 变化矩阵与像元面积是纯文件读取（生产存储单次 stat 约 8ms），逐矿山并发
_CHANGE_SCAN_WORKERS = 16


def _result_area_signature(result):
    updated = result.update_time.isoformat(sep=" ") if getattr(result, "update_time", None) else ""
    return "|".join(str(part) for part in (result.current_revision_no, result.feature_count, result.vector_status, updated))


def _area_memo_path(project_root):
    return Path(project_root) / "outputs" / "stats" / _AREA_MEMO_FILE


def _load_area_memo(path):
    """读不回来、格式不对、版本不同一律当空：只影响本次耗时，不影响结果。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) and data.get("version") == _AREA_MEMO_VERSION else None
    memo = {}
    for key, entry in (entries or {}).items():
        try:
            result_id = int(key)
        except (TypeError, ValueError):
            continue
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("signature"), str)
            and isinstance(entry.get("area_m2"), dict)
        ):
            memo[result_id] = entry
    return memo


def _save_area_memo(path, entries):
    """原子替换；只保留仍然有效的成果，文件不随历史成果增长。写失败只记告警。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps({"version": _AREA_MEMO_VERSION, "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        LOGGER.warning("概览地类面积记忆写入失败 path=%s: %s", path, exc)


def _feature_collection_class_areas(raw):
    """单个成果的 {类别名: 外环球面面积 m²}，六个类别齐全；类别码不认识的记在空串键下（计入总面积）。"""
    area_by_class = {name: 0.0 for name in CLASS_BY_CODE.values()}
    try:
        collection = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return area_by_class
    for feature in (collection.get("features") or []) if isinstance(collection, dict) else []:
        code = (feature or {}).get("properties", {}).get("class_code")
        name = CLASS_BY_CODE.get(code, "")
        area_by_class[name] = area_by_class.get(name, 0.0) + _polygon_area_m2(feature.get("geometry"))
    return area_by_class


def _class_area_share(results, project_roots=None):
    """有效成果的矢量球面面积按类别聚合。

    project_roots（{project_id: 项目目录}）给出时启用逐成果面积记忆：签名没变的成果直接取
    记忆里的面积，只对新增或有变化的成果取矢量 JSON 重算。为 None 时逐条现算（测试，以及
    不该落文件的场合）。"""
    area_by_class = {name: 0.0 for name in CLASS_BY_CODE.values()}
    grouped = {}
    for result in results:
        grouped.setdefault(result.project_id, []).append(result)
    for project_id in sorted(grouped):
        # 固定求和次序（项目、成果 id 升序），同一份数据每次算出的结果一致
        group = sorted(grouped[project_id], key=lambda result: result.id)
        areas = {}
        project_root = (project_roots or {}).get(project_id)
        memo = _load_area_memo(_area_memo_path(project_root)) if project_root is not None else {}
        stale = []
        for result in group:
            entry = memo.get(result.id)
            if entry and entry.get("signature") == _result_area_signature(result):
                areas[result.id] = entry["area_m2"]
            else:
                stale.append(result)
        if stale:
            # 矢量 JSON 只为需要重算的成果按需加载（同批 id 一条查询）
            collections = db.session.query(
                ClassificationResult.id,
                ClassificationResult.current_feature_collection_json,
            ).filter(ClassificationResult.id.in_([result.id for result in stale])).all()
            computed = {result_id: _feature_collection_class_areas(raw) for result_id, raw in collections}
            for result in stale:
                areas[result.id] = computed.get(result.id, {name: 0.0 for name in CLASS_BY_CODE.values()})
        if project_root is not None:
            _save_area_memo(
                _area_memo_path(project_root),
                {
                    str(result.id): {"signature": _result_area_signature(result), "area_m2": areas[result.id]}
                    for result in group
                },
            )
        for result in group:
            for name, value in areas[result.id].items():
                area_by_class[name] = area_by_class.get(name, 0.0) + value
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
    """像元面积（m²）：按最新 label.tif 的实际 CRS 换算。

    2026-10-31 数值审查修正：原公式恒按 EPSG:4326 度换算，而推理 label 保留
    源影像 CRS——2024 期（EPSG:3857 网墨卡托，米）被重复乘 111320²，看板修复
    面积出现 5.4e14 m² 的天文数字。按 CRS 三分支：
    - 地理坐标系（度）：|a|·|e|·111320²·cos(中心纬度)
    - Web Mercator（EPSG:3857，米）：Mercator 每轴失真 1/cos(lat)，真面积
      = |a|·|e|·cos²(中心纬度)
    - 其他投影（米制）：|a|·|e| 直接为 m²
    """
    labels = sorted(fid_dir.glob("*_label.tif"))
    if not labels:
        return None
    try:
        with rasterio.open(labels[-1]) as src:
            pixel_area = abs(src.transform.a) * abs(src.transform.e)
            center_lat = (src.bounds.top + src.bounds.bottom) / 2.0
            crs = src.crs
    except (OSError, ValueError):
        return None
    if crs is None:
        return None
    if crs.is_geographic:
        return pixel_area * (111320.0 ** 2) * math.cos(math.radians(center_lat))
    if crs.to_epsg() == 3857:
        # Web Mercator：米制坐标每轴放大 1/cos(lat)，真面积乘 cos² 修正
        return pixel_area * (math.cos(math.radians(center_lat)) ** 2)
    return pixel_area


def _scan_change_dir(fid_dir: Path):
    """单座矿的净转移指标 (获得像素, 流失像素, 草林→裸建像素, 有效像素总数, 像元面积 m²)。
    没有可用的变化矩阵时返回 None。只做文件读取，在线程池里跑。"""
    matrix = _read_change_matrix_pixels(fid_dir)
    if matrix is None:
        return None
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
    return gained, lost, to_disturbance, total_valid, _pixel_area_m2(fid_dir)


def _change_metrics(project_root: Path):
    """新增修复面积（m²）与疑似异常矿山数（按计划 §4.3/§4.4 口径）。

    逐矿山并发读取（原先串行：565 个目录各做两次 stat，外加 60 个矿各读一次矩阵、打开一次
    label.tif，约 3 秒）。各矿互不依赖；汇总按矿山目录名升序，结果与串行版本一致。"""
    empty = {"restored_area_m2": 0.0, "suspect_mine_count": 0, "suspect_fids": []}
    inference_root = project_root / "outputs" / "inference"
    if not inference_root.is_dir():
        return dict(empty)
    names = sorted(path.name for path in inference_root.iterdir())

    def scan(name):
        fid_dir = inference_root / name
        if not fid_dir.is_dir():
            return None
        return _scan_change_dir(fid_dir)

    with ThreadPoolExecutor(max_workers=_CHANGE_SCAN_WORKERS) as pool:
        scanned = list(pool.map(scan, names))
    restoration_m2 = 0.0
    suspect_fids = []
    for name, item in zip(names, scanned):
        if item is None:
            continue
        gained, lost, to_disturbance, total_valid, pixel_area = item
        if pixel_area:
            restoration_m2 += (gained - lost) * pixel_area
        if total_valid > 0 and to_disturbance / total_valid >= SUSPECT_LOSS_RATIO:
            suspect_fids.append(name)
    return {
        "restored_area_m2": round(max(restoration_m2, 0.0), 1),
        "suspect_mine_count": len(suspect_fids),
        "suspect_fids": sorted(suspect_fids, key=str),
    }


def build_stats_overview(storage_root: Path = None, use_area_memo=False):
    """use_area_memo=True 时即使传了 storage_root 也启用逐成果面积记忆（给测试用）；
    正常请求路径上它与 TTL 缓存同开同关。"""
    now = time.monotonic()
    cacheable = (
        storage_root is None
        and has_app_context()
        and not current_app.config.get("TESTING")
    )
    if (
        cacheable
        and _OVERVIEW_CACHE["payload"] is not None
        and now < _OVERVIEW_CACHE["expires_at"]
    ):
        return _OVERVIEW_CACHE["payload"]
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
    }

    root = storage_root or get_storage_root()
    project_roots = {
        project.id: resolve_storage_path(root, Path("projects") / str(project.id))
        for project in active_projects
    }
    overview["class_area"] = _class_area_share(
        results, project_roots if (cacheable or use_area_memo) else None
    )
    restored_m2 = 0.0
    suspect_fids = []
    for project in active_projects:
        metrics = _change_metrics(project_roots[project.id])
        restored_m2 += metrics["restored_area_m2"]
        suspect_fids.extend(f"{project.id}:{fid}" for fid in metrics["suspect_fids"])
    overview["restored_area_m2"] = round(max(restored_m2, 0.0), 1)
    overview["suspect_mine_count"] = len(suspect_fids)
    if cacheable:
        _OVERVIEW_CACHE["expires_at"] = now + _OVERVIEW_CACHE_TTL_SECONDS
        _OVERVIEW_CACHE["payload"] = overview
    return overview


@stats_api.before_request
def require_stats_auth():
    return ensure_logged_in()


@stats_api.get('/overview')
def stats_overview_api():
    return success_api(data=build_stats_overview())
