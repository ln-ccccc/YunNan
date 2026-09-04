import datetime

from applications.extensions import db


class ProjectSpatialResource(db.Model):
    __tablename__ = "project_spatial_resource"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    resource_type = db.Column(db.String(32), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    source_path = db.Column(db.String(1024), nullable=False)
    normalized_path = db.Column(db.String(1024))
    tile_path = db.Column(db.String(1024))
    source_format = db.Column(db.String(32), nullable=False)
    feature_count = db.Column(db.Integer)
    crs = db.Column(db.String(255))
    bounds_json = db.Column(db.Text, nullable=False, default="{}")
    min_zoom = db.Column(db.Integer)
    max_zoom = db.Column(db.Integer)
    error_message = db.Column(db.Text)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )
    jobs = db.relationship(
        "ProjectSpatialJob",
        backref="resource",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        db.UniqueConstraint("project_id", "resource_type", "version", name="uq_project_spatial_version"),
    )


class ProjectSpatialJob(db.Model):
    __tablename__ = "project_spatial_job"

    id = db.Column(db.String(36), primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    resource_id = db.Column(
        db.Integer,
        db.ForeignKey("project_spatial_resource.id"),
        nullable=False,
        index=True,
    )
    job_type = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    stage = db.Column(db.String(64), nullable=False, default="queued")
    progress = db.Column(db.Float, nullable=False, default=0.0)
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    error_message = db.Column(db.Text)
    worker_id = db.Column(db.String(128))
    heartbeat_at = db.Column(db.DateTime)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )
