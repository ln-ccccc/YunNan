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
