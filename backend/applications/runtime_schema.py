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
    _ensure_classification_feature_count(inspector)


def _ensure_classification_feature_count(inspector):
    """classification_results.feature_count 冗余列（M1 看板 2026-09-22）：
    存量库补列并按 current FC 回填；新库由模型直接建对。"""
    if "classification_results" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("classification_results")}
    if "feature_count" not in columns:
        db.session.execute(
            text("ALTER TABLE classification_results ADD COLUMN feature_count INTEGER NOT NULL DEFAULT 0")
        )
        db.session.commit()
        backfill_classification_feature_counts()


def backfill_classification_feature_counts():
    """按 current FC 回填 feature_count（表小逐条解析即可，MEDIUMTEXT 大 JSON 不走 SQL 函数）。"""
    import json

    from applications.models.classification_result import ClassificationResult

    updated = 0
    for result in ClassificationResult.query.all():
        try:
            collection = json.loads(result.current_feature_collection_json or "{}")
        except (TypeError, ValueError):
            collection = {}
        count = len(collection.get("features") or []) if isinstance(collection, dict) else 0
        if result.feature_count != count:
            result.feature_count = count
            updated += 1
    if updated:
        db.session.commit()
    return updated


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
