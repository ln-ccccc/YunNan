"""图斑清单汇总（M3 补交，2026-09-22）：项目工作台"图斑清单"面板数据。

GET /api/projects/<id>/mines/parcel-summary：每矿山一行——
fid/名称快照/治理状态/图斑数（每 fid+year 取最新成果去重）/最新成果年份/
最近解译时间，支持分页与 fid 过滤。
"""
import logging
from pathlib import Path

from sqlalchemy import func

from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.project import Project, ProjectMineBinding

LOGGER = logging.getLogger(__name__)


def build_parcel_summary(project_id, page=1, limit=50, fid_filter=None):
    from applications.project_hub.service import _get_project_or_404

    project = _get_project_or_404(project_id)

    bindings = ProjectMineBinding.query.filter_by(project_id=project.id).order_by(
        ProjectMineBinding.sort_order, ProjectMineBinding.mine_fid
    )
    if fid_filter:
        try:
            fid_value = int(fid_filter)
            bindings = bindings.filter(ProjectMineBinding.mine_fid == fid_value)
        except (TypeError, ValueError):
            raise ValueError("fid 过滤必须是整数")
    bindings = bindings.all()
    total = len(bindings)

    start = max(0, (page - 1) * limit)
    rows = bindings[start:start + limit]

    # 图斑统计：每 (fid, year) 取最新成果（同年多任务去重），一次查询内存聚合
    latest = {}
    results = (
        db.session.query(
            ClassificationResult.mine_fid,
            ClassificationResult.year,
            ClassificationResult.id,
            ClassificationResult.feature_count,
            ClassificationResult.create_time,
            ClassificationResult.vector_status,
        )
        .filter(ClassificationResult.project_id == project.id)
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    stats = {}
    for mine_fid, year, _rid, feature_count, create_time, vector_status in results:
        key = (mine_fid, year)
        if key not in latest:
            latest[key] = True
            entry = stats.setdefault(mine_fid, {
                "feature_total": 0, "latest_year": None, "latest_at": None, "result_count": 0,
            })
            entry["feature_total"] += feature_count or 0
            entry["result_count"] += 1
            if entry["latest_year"] is None or year > entry["latest_year"]:
                entry["latest_year"] = year
            if entry["latest_at"] is None or (create_time and create_time > entry["latest_at"]):
                entry["latest_at"] = create_time

    items = []
    for binding in rows:
        stat = stats.get(binding.mine_fid) or {
            "feature_total": 0, "latest_year": None, "latest_at": None, "result_count": 0,
        }
        items.append({
            "mine_fid": binding.mine_fid,
            "mine_name": binding.mine_name_snapshot,
            "city": binding.city_snapshot,
            "status": binding.status_snapshot,
            "feature_count": stat["feature_total"],
            "latest_year": stat["latest_year"],
            "latest_analyzed_at": stat["latest_at"].isoformat() if stat["latest_at"] else None,
            "result_count": stat["result_count"],
        })
    return {"items": items, "count": total, "page": page, "limit": limit}
