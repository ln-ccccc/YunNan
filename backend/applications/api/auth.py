import threading
import time

from flask import Blueprint, jsonify, request, session

from applications.auth.service import authenticate_admin, change_admin_password
from applications.common.utils.http import success_api

auth_api = Blueprint("auth_api", __name__, url_prefix="/api/auth")

# 登录限速（2026-11-05 生产化检查，迁移审查 P2-14）：此前登录无失败锁定，
# 可对弱口令在线爆破（缓解只有 scrypt 慢哈希）。进程内滑动窗口实现——
# gunicorn 每 worker 各自计数（等效阈值 ×worker 数），对内网单机部署够用；
# 不引 Redis（离线栈无 redis，引入即新增故障面）。
_LOGIN_WINDOW_SECONDS = 900
_LOGIN_MAX_FAILURES = 5
_LOGIN_MAX_ATTEMPTS_PER_IP = 30
_login_failures = {}  # username -> [failure timestamps]
_login_ip_attempts = {}  # ip -> [attempt timestamps]
_login_guard_lock = threading.Lock()


def _prune_stale(entries, now):
    return [ts for ts in entries if now - ts < _LOGIN_WINDOW_SECONDS]


def _login_locked(username, client_ip):
    """返回 (locked, retry_after_seconds)。只读检查，计数在失败时记录——
    成功登录不消耗额度（测试/多用户同源 IP 不误伤）。"""
    now = time.time()
    with _login_guard_lock:
        failures = _prune_stale(_login_failures.get(username, []), now)
        _login_failures[username] = failures
        if len(failures) >= _LOGIN_MAX_FAILURES:
            return True, int(_LOGIN_WINDOW_SECONDS - (now - failures[0])) + 1
        ip_failures = _prune_stale(_login_ip_attempts.get(client_ip, []), now)
        _login_ip_attempts[client_ip] = ip_failures
        if len(ip_failures) >= _LOGIN_MAX_ATTEMPTS_PER_IP:
            return True, int(_LOGIN_WINDOW_SECONDS - (now - ip_failures[0])) + 1
        return False, 0


def _record_login_failure(username, client_ip):
    now = time.time()
    with _login_guard_lock:
        # 防无界增长：喷洒海量不同用户名/IP 时直接重置（最坏情况限速归零重启计数，
        # 内存安全优先于严格性）
        if len(_login_failures) > 10000:
            _login_failures.clear()
        if len(_login_ip_attempts) > 10000:
            _login_ip_attempts.clear()
        failures = _prune_stale(_login_failures.get(username, []), now)
        failures.append(now)
        _login_failures[username] = failures
        ip_failures = _prune_stale(_login_ip_attempts.get(client_ip, []), now)
        ip_failures.append(now)
        _login_ip_attempts[client_ip] = ip_failures


def _clear_login_failures(username):
    with _login_guard_lock:
        _login_failures.pop(username, None)


@auth_api.post("/login")
def login_api():
    payload = request.json or {}
    username = str(payload.get("username") or "")
    client_ip = request.remote_addr or "unknown"
    locked, retry_after = _login_locked(username, client_ip)
    if locked:
        return jsonify(
            success=False,
            code=429,
            msg=f"登录尝试过于频繁，请 {max(retry_after, 1) // 60 + 1} 分钟后再试",
        ), 429
    user = authenticate_admin(payload.get("username"), payload.get("password"))
    if user is None:
        _record_login_failure(username, client_ip)
        return jsonify(success=False, code=401, msg="账号或密码错误"), 401

    _clear_login_failures(username)
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
