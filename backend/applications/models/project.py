import datetime

from applications.extensions import db


class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    region = db.Column(db.String(255))
    manager = db.Column(db.String(255))
    remark = db.Column(db.Text)
    status = db.Column(db.String(32), nullable=False, default="draft")
    monitor_start_year = db.Column(db.Integer)
    monitor_end_year = db.Column(db.Integer)
    deleted_at = db.Column(db.DateTime)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )

    mines = db.relationship(
        "ProjectMineBinding",
        backref="project",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProjectMineBinding.sort_order.asc()",
    )
    datasets = db.relationship(
        "ProjectDataset",
        backref="project",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProjectDataset.create_time.desc()",
    )
    activities = db.relationship(
        "ProjectActivityLog",
        backref="project",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProjectActivityLog.create_time.desc()",
    )
    exports = db.relationship(
        "ProjectExportRecord",
        backref="project",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProjectExportRecord.create_time.desc()",
    )
    backups = db.relationship(
        "ProjectBackupRecord",
        backref="project",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProjectBackupRecord.create_time.desc()",
    )
    spatial_resources = db.relationship(
        "ProjectSpatialResource",
        backref="project",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProjectSpatialResource.version.desc()",
    )
    spatial_jobs = db.relationship(
        "ProjectSpatialJob",
        backref="project",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="ProjectSpatialJob.create_time.desc()",
    )


class ProjectMineBinding(db.Model):
    __tablename__ = "project_mine_binding"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    mine_fid = db.Column(db.Integer, nullable=False, index=True)
    mine_name_snapshot = db.Column(db.String(255))
    city_snapshot = db.Column(db.String(255))
    area_snapshot = db.Column(db.Float)
    status_snapshot = db.Column(db.String(255))
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )


class ProjectDataset(db.Model):
    __tablename__ = "project_dataset"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    dataset_kind = db.Column(db.String(64), nullable=False)
    display_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(1024), nullable=False)
    source_format = db.Column(db.String(64))
    mine_fid = db.Column(db.Integer, index=True)
    year_start = db.Column(db.Integer)
    year_end = db.Column(db.Integer)
    slice_config_json = db.Column(db.Text, nullable=False, default="{}")
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )


class ProjectActivityLog(db.Model):
    __tablename__ = "project_activity_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    event_type = db.Column(db.String(64), nullable=False)
    actor = db.Column(db.String(255), nullable=False, default="system")
    payload_json = db.Column(db.Text, nullable=False, default="{}")
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)


class ProjectExportRecord(db.Model):
    __tablename__ = "project_export_record"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    format = db.Column(db.String(32), nullable=False)
    file_path = db.Column(db.String(1024))
    status = db.Column(db.String(32), nullable=False, default="pending")
    request_params_json = db.Column(db.Text, nullable=False, default="{}")
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )


class ProjectBackupRecord(db.Model):
    __tablename__ = "project_backup_record"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False, index=True)
    scope = db.Column(db.String(64), nullable=False, default="metadata_index")
    manifest_path = db.Column(db.String(1024), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="completed")
    restorable = db.Column(db.Boolean, nullable=False, default=True)
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )
