import datetime

from applications.extensions import db


class AdminUser(db.Model):
    __tablename__ = "admin_user"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(64), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )
