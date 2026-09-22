import datetime

from sqlalchemy.dialects.mysql import MEDIUMTEXT

from applications.extensions import db

# 矢量化 GeoJSON 实测可达 64KB 以上（MySQL TEXT 上限 65535 字节，实测 67KB 触发 1406），
# MySQL 下统一使用 MEDIUMTEXT；SQLite 无此限制，variant 忽略
LongJSON = db.Text().with_variant(MEDIUMTEXT(), "mysql")


class ClassificationResult(db.Model):
    __tablename__ = "classification_results"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, nullable=False, index=True)
    mine_fid = db.Column(db.Integer, nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    inference_job_id = db.Column(db.String(36), nullable=False, index=True)
    model_id = db.Column(db.String(128), nullable=False)
    mine_resource_id = db.Column(db.Integer, nullable=False, index=True)
    mine_resource_version = db.Column(db.Integer)
    roi_geometry_json = db.Column(LongJSON)
    label_path = db.Column(db.String(1024))
    auto_geojson_path = db.Column(db.String(1024))
    auto_feature_collection_json = db.Column(LongJSON)
    current_feature_collection_json = db.Column(LongJSON)
    current_revision_no = db.Column(db.Integer)
    # 冗余列（M1 看板 2026-09-22）：current FC 的要素数，发布/保存修订时同步维护，
    # 项目卡片"图斑数量"与跨项目统计避免逐条解析 MEDIUMTEXT JSON
    feature_count = db.Column(db.Integer, nullable=False, default=0)
    vector_status = db.Column(db.String(32), nullable=False, default="vector_failed", index=True)
    vector_error = db.Column(db.String(255))
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "project_id",
            "mine_fid",
            "year",
            "inference_job_id",
            name="uq_classification_result_job_fid_year",
        ),
    )


class ClassificationRevision(db.Model):
    __tablename__ = "classification_revisions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    result_id = db.Column(
        db.Integer,
        db.ForeignKey("classification_results.id"),
        nullable=False,
        index=True,
    )
    revision_no = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(16), nullable=False)
    author = db.Column(db.String(255), nullable=False, default="system")
    feature_count = db.Column(db.Integer, nullable=False)
    snapshot_path = db.Column(db.String(1024), nullable=False)
    feature_collection_json = db.Column(LongJSON, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("result_id", "revision_no", name="uq_classification_revision"),
    )


class ClassificationEditAudit(db.Model):
    __tablename__ = "classification_edit_audits"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    result_id = db.Column(
        db.Integer,
        db.ForeignKey("classification_results.id"),
        nullable=False,
        index=True,
    )
    base_revision_no = db.Column(db.Integer)
    revision_no = db.Column(db.Integer, nullable=False)
    actor = db.Column(db.String(255), nullable=False, default="system")
    action = db.Column(db.String(64), nullable=False)
    request_source = db.Column(db.String(64), nullable=False)
    details_json = db.Column(db.Text, nullable=False, default="{}")
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
