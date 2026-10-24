import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from applications.extensions import db

system_health = Blueprint("system_health", __name__, url_prefix="/api")
LOGGER = logging.getLogger(__name__)


@system_health.get("/health")
def health_api():
    """容器健康探针（compose healthcheck 与部署门禁调用，免登录）。

    比旧探针 /api/auth/session 更深一层：DB SELECT 1 + 存储根可写探针
    （2026-10-24 审查 P2-12）。正文只含检查项与异常类型名，不泄露路径、
    SQL 或连接串；任一失败返回 503。
    """
    checks = {}
    try:
        db.session.execute(text("SELECT 1"))
        db.session.rollback()
        checks["database"] = "ok"
    except Exception as exc:
        LOGGER.exception("healthcheck 数据库探针失败")
        checks["database"] = f"error:{type(exc).__name__}"

    try:
        from applications.project_hub.spatial_storage import get_storage_root

        probe = get_storage_root() / ".health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["storage"] = "ok"
    except Exception as exc:
        LOGGER.exception("healthcheck 存储探针失败")
        checks["storage"] = f"error:{type(exc).__name__}"

    healthy = all(value == "ok" for value in checks.values())
    return (
        jsonify(
            {
                "success": healthy,
                "status": "ok" if healthy else "unhealthy",
                "checks": checks,
            }
        ),
        200 if healthy else 503,
    )
