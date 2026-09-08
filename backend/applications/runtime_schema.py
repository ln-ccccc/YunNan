from sqlalchemy import inspect, text

from applications.extensions import db

# 矢量化 GeoJSON 实测可超过 MySQL TEXT 的 65535 字节上限（2026-09-08 端到端实测 67KB
# 触发 1406 Data too long，矢量成果丢失）。存量库在此升级为 MEDIUMTEXT；新库由模型
# 的 MEDIUMTEXT variant 直接建对。
LONG_JSON_COLUMNS = (
    ("classification_results", "roi_geometry_json", "NULL"),
    ("classification_results", "auto_feature_collection_json", "NULL"),
    ("classification_results", "current_feature_collection_json", "NULL"),
    ("classification_revisions", "feature_collection_json", "NOT NULL"),
    ("inference_jobs", "warnings_json", "NOT NULL"),
    ("inference_jobs", "request_payload_json", "NOT NULL"),
    ("inference_jobs", "result_json", "NULL"),
)


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
    if db.engine.dialect.name == "mysql":
        _upgrade_long_json_columns(inspector)


def _upgrade_long_json_columns(inspector):
    table_names = set(inspector.get_table_names())
    for table, column, nullability in LONG_JSON_COLUMNS:
        if table not in table_names:
            continue
        column_info = next(
            (item for item in inspector.get_columns(table) if item["name"] == column),
            None,
        )
        if column_info is None or str(column_info["type"]).upper() != "TEXT":
            continue
        db.session.execute(
            text(f"ALTER TABLE {table} MODIFY COLUMN {column} MEDIUMTEXT {nullability}")
        )
        db.session.commit()
        inspector = inspect(db.engine)
