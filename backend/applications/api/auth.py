from flask import Blueprint, jsonify, request, session

from applications.auth.service import authenticate_admin, change_admin_password
from applications.common.utils.http import success_api

auth_api = Blueprint("auth_api", __name__, url_prefix="/api/auth")


@auth_api.post("/login")
def login_api():
    payload = request.json or {}
    user = authenticate_admin(payload.get("username"), payload.get("password"))
    if user is None:
        return jsonify(success=False, code=401, msg="账号或密码错误"), 401

    session.clear()
    session.permanent = True
    session["admin_user_id"] = user.id
    session["admin_username"] = user.username
    return success_api(data={"authenticated": True, "username": user.username})


@auth_api.get("/session")
def session_api():
    return success_api(
        data={
            "authenticated": bool(session.get("admin_user_id")),
            "username": session.get("admin_username"),
        }
    )


@auth_api.post("/change-password")
def change_password_api():
    """管理员自助改密：session 中的账号 + 旧口令验证 + 新口令落库。"""
    if not session.get("admin_user_id"):
        return jsonify(success=False, code=401, msg="未登录"), 401
    payload = request.json or {}
    try:
        change_admin_password(
            session["admin_user_id"],
            payload.get("old_password"),
            payload.get("new_password"),
        )
    except PermissionError as exc:
        return jsonify(success=False, code=403, msg=str(exc)), 403
    except ValueError as exc:
        return jsonify(success=False, code=422, msg=str(exc)), 422
    return success_api(msg="口令修改成功，下次登录请使用新口令")


@auth_api.post("/logout")
def logout_api():
    session.clear()
    return success_api(msg="已退出登录", data={"authenticated": False, "username": None})
