import datetime
import os

from flask import current_app
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from applications.extensions import db
from applications.models import AdminUser


def _get_admin_setting(key, default=""):
    return current_app.config.get(key) or os.getenv(key, default)


def sync_admin_from_env():
    username = _get_admin_setting("ADMIN_USERNAME", "admin").strip() or "admin"
    password = _get_admin_setting("ADMIN_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("ADMIN_PASSWORD is required")

    password_hash = generate_password_hash(password)
    user = AdminUser.query.filter_by(username=username).first()
    if user is None:
        user = AdminUser(username=username, password_hash=password_hash, is_active=True)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # gunicorn 多 worker 首次启动空库竞态：另一 worker 已插入同名账号，
            # 回滚后改为更新（否则该 worker 会陷入崩溃重启循环——江西 03247b9 同款修复）
            db.session.rollback()
            user = AdminUser.query.filter_by(username=username).first()
            if user is None:
                raise
            user.password_hash = password_hash
            user.is_active = True
            db.session.commit()
        return user

    user.password_hash = password_hash
    user.is_active = True
    db.session.commit()
    return user


def verify_admin_password(username, password):
    user = AdminUser.query.filter_by(username=username, is_active=True).first()
    return bool(user and check_password_hash(user.password_hash, password))


def authenticate_admin(username, password):
    user = AdminUser.query.filter_by(username=username, is_active=True).first()
    if not user or not check_password_hash(user.password_hash, password or ""):
        return None
    user.last_login_at = datetime.datetime.now()
    db.session.commit()
    return user


def change_admin_password(user_id, old_password, new_password):
    """管理员自助改密：验旧密码 + 更新 hash。

    注意：部署环境 ADMIN_PASSWORD 环境变量会在 gunicorn 重启时经
    sync_admin_from_env 覆盖回旧值——改密后如重启需同步更新 .env，
    接口返回中带提示。
    """
    from applications.models import AdminUser

    if not new_password or len(str(new_password)) < 8:
        raise ValueError("新口令至少 8 位")
    user = AdminUser.query.filter_by(id=user_id, is_active=True).first()
    if user is None:
        raise ValueError("当前账号不存在")
    if not check_password_hash(user.password_hash, old_password or ""):
        raise PermissionError("原口令不正确")
    user.password_hash = generate_password_hash(str(new_password))
    db.session.commit()
    return user
