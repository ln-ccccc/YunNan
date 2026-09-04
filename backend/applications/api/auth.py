from flask import Blueprint, jsonify, request, session

from applications.auth.service import authenticate_admin
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


@auth_api.post("/logout")
def logout_api():
    session.clear()
    return success_api(msg="已退出登录", data={"authenticated": False, "username": None})
