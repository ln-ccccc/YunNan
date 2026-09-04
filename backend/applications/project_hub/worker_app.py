import os

from flask import Flask

from applications.configs import config
from applications.extensions import db


def create_spatial_worker_app(config_name=None):
    app = Flask(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
    config_name = config_name or os.getenv("FLASK_CONFIG", "development")
    app.config.from_object(config[config_name])
    if config_name == "production" and not os.getenv("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is required in production")
    db.init_app(app)
    with app.app_context():
        from applications.models.project import Project, ProjectActivityLog  # noqa: F401
        from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource  # noqa: F401

        db.create_all()
    return app
