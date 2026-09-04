from sqlalchemy import inspect, text

from applications.extensions import db


def ensure_runtime_schema():
    inspector = inspect(db.engine)
    if "inference_jobs" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("inference_jobs")}
    if "project_id" not in columns:
        db.session.execute(text("ALTER TABLE inference_jobs ADD COLUMN project_id INTEGER NULL"))
        db.session.commit()
        inspector = inspect(db.engine)
    indexes = {item["name"] for item in inspector.get_indexes("inference_jobs")}
    if "ix_inference_jobs_project_id" not in indexes:
        db.session.execute(text("CREATE INDEX ix_inference_jobs_project_id ON inference_jobs (project_id)"))
        db.session.commit()
