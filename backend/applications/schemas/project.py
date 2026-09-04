import json
from pathlib import Path

from marshmallow import fields

from applications.extensions import ma


def _json_text_to_obj(value):
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {}


class ProjectMineBindingSchema(ma.Schema):
    id = fields.Integer()
    mine_fid = fields.Integer()
    mine_name_snapshot = fields.Str()
    city_snapshot = fields.Str()
    area_snapshot = fields.Float(allow_none=True)
    status_snapshot = fields.Str(allow_none=True)
    sort_order = fields.Integer()
    create_time = fields.DateTime()
    update_time = fields.DateTime()


class ProjectDatasetSchema(ma.Schema):
    id = fields.Integer()
    dataset_kind = fields.Str()
    display_name = fields.Str()
    file_path = fields.Str()
    source_format = fields.Str(allow_none=True)
    mine_fid = fields.Integer(allow_none=True)
    year_start = fields.Integer(allow_none=True)
    year_end = fields.Integer(allow_none=True)
    slice_config_json = fields.Method("get_slice_config")
    create_time = fields.DateTime()
    update_time = fields.DateTime()

    def get_slice_config(self, obj):
        return _json_text_to_obj(obj.slice_config_json)


class ProjectActivityLogSchema(ma.Schema):
    id = fields.Integer()
    event_type = fields.Str()
    actor = fields.Str()
    payload = fields.Method("get_payload")
    create_time = fields.DateTime()

    def get_payload(self, obj):
        return _json_text_to_obj(obj.payload_json)


class ProjectExportRecordSchema(ma.Schema):
    id = fields.Integer()
    format = fields.Str()
    artifact_name = fields.Method("get_artifact_name", allow_none=True)
    status = fields.Str()
    create_time = fields.DateTime()
    update_time = fields.DateTime()

    def get_artifact_name(self, obj):
        raw_path = str(obj.file_path or "").replace("\\", "/")
        return Path(raw_path).name or None


class ProjectBackupRecordSchema(ma.Schema):
    id = fields.Integer()
    scope = fields.Str()
    snapshot_name = fields.Method("get_snapshot_name")
    status = fields.Str()
    restorable = fields.Boolean()
    create_time = fields.DateTime()
    update_time = fields.DateTime()

    def get_snapshot_name(self, obj):
        return "项目配置快照"


class ProjectSummarySchema(ma.Schema):
    id = fields.Integer()
    name = fields.Str()
    region = fields.Str(allow_none=True)
    manager = fields.Str(allow_none=True)
    remark = fields.Str(allow_none=True)
    status = fields.Str()
    monitor_start_year = fields.Integer(allow_none=True)
    monitor_end_year = fields.Integer(allow_none=True)
    mine_count = fields.Integer()
    dataset_count = fields.Integer()
    spatial_status = fields.Str()
    map_ready = fields.Boolean()
    missing_resources = fields.List(fields.Str())
    latest_activity_at = fields.DateTime(allow_none=True)
    create_time = fields.DateTime()
    update_time = fields.DateTime()


class ProjectAssetViewSchema(ma.Schema):
    id = fields.Str()
    source_type = fields.Str()
    source_id = fields.Integer()
    asset_type = fields.Str()
    name = fields.Str()
    format = fields.Str()
    status = fields.Str()
    version = fields.Integer()
    created_at = fields.Str(allow_none=True)
    updated_at = fields.Str(allow_none=True)
    spatial = fields.Dict(allow_none=True)
    temporal = fields.Dict(allow_none=True)
    provenance = fields.Dict(allow_none=True)
    error = fields.Dict(allow_none=True)
    capabilities = fields.Dict(allow_none=True)


class ProjectOverviewActivitySchema(ma.Schema):
    action_code = fields.Str()
    actor_id = fields.Str()
    actor_type = fields.Str()
    target_type = fields.Str()
    target_id = fields.Str()
    result = fields.Str()
    job_id = fields.Str(allow_none=True)
    payload = fields.Dict()
    created_at = fields.Str(allow_none=True)


class ProjectOverviewViewSchema(ma.Schema):
    project_id = fields.Integer()
    lifecycle_status = fields.Str()
    summary = fields.Dict()
    readiness = fields.Dict()
    capabilities = fields.Dict()
    blockers = fields.List(fields.Dict())
    next_actions = fields.List(fields.Dict())
    counts = fields.Dict()
    recent_activity = fields.List(fields.Nested(ProjectOverviewActivitySchema))
