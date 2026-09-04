from applications.auth.service import sync_admin_from_env
from applications.common.scripts.init_db import init_db


def init_script(app):
    init_db()
    with app.app_context():
        sync_admin_from_env()
