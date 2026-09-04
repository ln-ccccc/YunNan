import datetime

from applications.extensions import db


class InferenceJob(db.Model):
    __tablename__ = "inference_jobs"

    id = db.Column(db.String(36), primary_key=True)
    project_id = db.Column(db.Integer, nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    requested_device = db.Column(db.String(32), nullable=False, default="auto")
    effective_device = db.Column(db.String(32))
    fallback_reason = db.Column(db.String(64))
    warnings_json = db.Column(db.Text, nullable=False, default="[]")
    request_payload_json = db.Column(db.Text, nullable=False)
    result_json = db.Column(db.Text)
    error_code = db.Column(db.String(64))
    error_message = db.Column(db.Text)
    progress_current = db.Column(db.Integer, nullable=False, default=0)
    progress_total = db.Column(db.Integer, nullable=False, default=0)
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    worker_id = db.Column(db.String(128))
    work_dir = db.Column(db.String(1024))
    create_time = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False, index=True)
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
    )
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)


class InferenceWorkerState(db.Model):
    __tablename__ = "inference_worker_states"

    worker_id = db.Column(db.String(128), primary_key=True)
    requested_device = db.Column(db.String(32), nullable=False)
    effective_device = db.Column(db.String(32), nullable=False)
    fallback_reason = db.Column(db.String(64))
    warnings_json = db.Column(db.Text, nullable=False, default="[]")
    gpu_name = db.Column(db.String(255))
    compute_capability = db.Column(db.String(32))
    update_time = db.Column(
        db.DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
        index=True,
    )
