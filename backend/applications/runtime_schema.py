import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from applications.extensions import db

logger = logging.getLogger(__name__)

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

# 多启动进程（backend Flask 与 inference Worker）并发执行 bootstrap 的串行化锁
# （2026-11-14 审查问题27）：MySQL 命名排它锁为连接级，用独立连接持有，与执行
# DDL 的会话连接解耦；SQLite 无命名锁，靠幂等 DDL 收口
_SCHEMA_LOCK_NAME = "yunnan_runtime_schema"
_SCHEMA_LOCK_TIMEOUT_SECONDS = 60

# 并行 bootstrap 落败方的重复 DDL 错误：MySQL 1050 表已存在 / 1060 列已存在 /
# 1061 索引已存在；SQLite 以错误文本识别
_DUPLICATE_MYSQL_ERROR_CODES = {1050, 1060, 1061}


def ensure_runtime_schema():
    if db.engine.dialect.name != "mysql":
        return _ensure_runtime_schema_locked()
    with db.engine.connect() as lock_conn:
        acquired = lock_conn.execute(
            text(f"SELECT GET_LOCK('{_SCHEMA_LOCK_NAME}', {_SCHEMA_LOCK_TIMEOUT_SECONDS})")
        ).scalar()
        try:
            if not acquired:
                # 锁等待超时：另一进程大概率已完成 bootstrap；各步骤自身幂等，
                # 验证式重跑安全，不阻塞服务启动
                logger.warning("schema bootstrap 锁等待超时，直接执行幂等校验流程")
            return _ensure_runtime_schema_locked()
        finally:
            if acquired:
                lock_conn.execute(text(f"SELECT RELEASE_LOCK('{_SCHEMA_LOCK_NAME}')"))


def _ensure_runtime_schema_locked():
    inspector = inspect(db.engine)
    if "inference_jobs" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("inference_jobs")}
    if "project_id" not in columns:
        _execute_idempotent_ddl(text("ALTER TABLE inference_jobs ADD COLUMN project_id INTEGER NULL"))
        inspector = inspect(db.engine)
    indexes = {item["name"] for item in inspector.get_indexes("inference_jobs")}
    if "ix_inference_jobs_project_id" not in indexes:
        _execute_idempotent_ddl(
            text("CREATE INDEX ix_inference_jobs_project_id ON inference_jobs (project_id)")
        )
    if db.engine.dialect.name == "mysql":
        _upgrade_long_json_columns(inspector)
    _ensure_classification_feature_count(inspector)


def _execute_idempotent_ddl(statement):
    """并发 bootstrap 下另一进程可能已抢先完成同名 DDL：重复错误视为成功，
    其余错误照常上抛（2026-11-14 审查问题27）。"""
    try:
        db.session.execute(statement)
        db.session.commit()
    except OperationalError as error:
        db.session.rollback()
        if _is_duplicate_ddl_error(error):
            return
        raise


def _is_duplicate_ddl_error(error):
    original = getattr(error, "orig", None)
    args = getattr(original, "args", None)
    if args and isinstance(args[0], int) and args[0] in _DUPLICATE_MYSQL_ERROR_CODES:
        return True
    message = str(original or error).lower()
    return "duplicate column" in message or "duplicate index" in message or "already exists" in message


def _ensure_classification_feature_count(inspector):
    """classification_results.feature_count 冗余列（M1 看板 2026-09-22）：
    存量库补列；新库由模型直接建对。回填与补列解耦——列存在不等于回填完成
    （补列后中断、历史回填中断都会留下默认 0），每次启动无条件重跑幂等回填
    收敛，不静默遗漏（2026-11-14 审查问题27）。"""
    if "classification_results" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("classification_results")}
    if "feature_count" not in columns:
        _execute_idempotent_ddl(
            text("ALTER TABLE classification_results ADD COLUMN feature_count INTEGER NOT NULL DEFAULT 0")
        )
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
