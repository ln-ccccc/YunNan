from functools import wraps

from flask import jsonify, request, session


def is_session_valid():
    """会话有效性以数据库为准（2026-11-14 审查问题20）：
    账号存在、启用中，且登录时快照的 session_version 与库中当前值一致——
    改密递增版本或停用账号后，旧 cookie 会话立即失效。"""
    user_id = session.get("admin_user_id")
    if not user_id:
        return False
    from applications.extensions import db
    from applications.models import AdminUser

    user = db.session.get(AdminUser, user_id)
    if user is None or not user.is_active:
        return False
    return session.get("admin_session_version") == (user.session_version or 1)


def ensure_logged_in():
    if request.method == "OPTIONS":
        return None
    if not is_session_valid():
        return jsonify(success=False, code=401, msg="未登录"), 401
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        unauthorized = ensure_logged_in()
        if unauthorized is not None:
            return unauthorized
        return view(*args, **kwargs)

    return wrapped
