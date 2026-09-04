import datetime
import os

from flask import current_app
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

    user = AdminUser.query.filter_by(username=username).first()
    password_hash = generate_password_hash(password)

    if user is None:
        user = AdminUser(username=username, password_hash=password_hash, is_active=True)
        db.session.add(user)
    else:
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
