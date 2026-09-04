from functools import wraps

from flask import jsonify, request, session


def ensure_logged_in():
    if request.method == "OPTIONS":
        return None
    if not session.get("admin_user_id"):
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
