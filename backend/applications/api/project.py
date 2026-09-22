import json
import logging
from io import BytesIO
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file, send_from_directory, session
from werkzeug.exceptions import HTTPException

from applications.api.error_responses import business_or_server_failure
from applications.auth.guard import login_required
from applications.common.utils.code import SUCCESS
from applications.common.utils.http import fail_api, success_api
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.classification_results import (
    ClassificationConflictError,
    ClassificationValidationError,
    export_current_classification_result,
    get_classification_result,
    list_classification_revisions,
    save_classification_revision,
)
from applications.project_hub.assets import ProjectAssetFilterError
from applications.project_hub.project_storage import ProjectStorageValidationError
from applications.project_hub.service import (
    ProjectDeleteNotAllowed,
    archive_project,
    batch_archive_projects,
    batch_delete_projects,
    create_backup,
    create_dataset,
    create_export,
    create_project,
    delete_project,
    get_project_assets,
    get_project_detail,
    get_project_overview,
    get_project_timeline,
    import_backup_manifest,
    list_backups,
    list_exports,
    list_projects,
    replace_project_mines,
    restore_backup,
    restore_project,
    update_project,
)
from applications.project_hub.spatial_service import (
    cancel_spatial_job,
    get_project_spatial,
    get_spatial_job,
    import_mine_vector,
    list_basemap_candidates,
    preview_mine_vector,
    register_basemap,
    retry_spatial_job,
)
from applications.project_hub.project_map import (
    get_project_change_matrix,
    get_project_geojson,
    get_project_map_manifest,
    get_project_mine_indices,
    get_project_stats,
    get_project_trend_report,
    list_project_original_imagery,
    resolve_project_original_imagery_download,
    search_project_mines,
)
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

project_api = Blueprint("project_api", __name__, url_prefix="/api/projects")
LOGGER = logging.getLogger(__name__)


def _read_model_success_api(data):
    return jsonify(success=True, code=SUCCESS, data=data)


def _request_actor():
    return str(session.get("admin_username") or "system")


def _spatial_failure_response(error, message, project_id):
    if isinstance(error, ValueError):
        return fail_api(str(error))
    if isinstance(error, HTTPException):
        # 客户端请求体问题（非法 JSON 400 / Content-Type 415）按原状态码收口，
        # 不吞成 500 + 服务端堆栈日志
        return fail_api(msg="请求数据不合法，请提交正确的 JSON", status=error.code or 400)
    LOGGER.exception("%s project_id=%s", message, project_id)
    return fail_api(f"{message}，请检查服务日志", status=500)


def _project_hub_failure_response(error, message, project_id=None):
    if isinstance(error, ValueError):
        return fail_api(str(error))
    if isinstance(error, HTTPException):
        return fail_api(msg="请求数据不合法，请提交正确的 JSON", status=error.code or 400)
    if project_id is None:
        LOGGER.exception(message)
    else:
        LOGGER.exception("%s project_id=%s", message, project_id)
    return fail_api(f"{message}，请检查服务日志", status=500)


def _is_label_geotiff_filename(fid, filename):
    prefix = f"{fid}+"
    suffix = "_label.tif"
    if not filename.startswith(prefix) or not filename.endswith(suffix):
        return False
    year = filename[len(prefix):-len(suffix)]
    return len(year) == 4 and year.isdigit()


def _classification_error_response(error):
    if isinstance(error, ClassificationConflictError):
        return fail_api(str(error), status=409, details=error.details)
    if isinstance(error, ClassificationValidationError):
        return fail_api(str(error), status=422, details=error.details)
    if isinstance(error, ValueError):
        return fail_api(str(error), status=404)
    LOGGER.error("分类成果接口发生内部错误", exc_info=error)
    return fail_api("分类成果服务暂时不可用", status=500)


@project_api.get("/<int:project_id>/outputs/inference/<int:fid>/<filename>")
@login_required
def project_inference_output_api(project_id, fid, filename):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        return fail_api("项目不存在"), 404
    if not any(binding.mine_fid == fid for binding in project.mines):
        return fail_api("矿山不属于当前项目"), 404
    if Path(filename).name != filename or not filename.startswith(f"{fid}+"):
        return fail_api("结果文件名非法"), 400
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".json", ".csv", ".tif"}:
        return fail_api("结果文件类型非法"), 400
    if suffix == ".tif" and not _is_label_geotiff_filename(fid, filename):
        return fail_api("结果文件类型非法"), 400
    try:
        target = resolve_storage_path(
            get_storage_root(),
            Path("projects") / str(project_id) / "outputs" / "inference" / str(fid) / filename,
        )
    except ValueError as error:
        return fail_api(str(error)), 400
    if not target.is_file():
        return fail_api("项目推理结果不存在"), 404
    return send_from_directory(str(target.parent), target.name)


@project_api.get("/<int:project_id>/map-resources/<int:resource_id>/tiles/<int:z>/<int:x>/<int:y>.png")
@login_required
def project_map_tile_api(project_id, resource_id, z, x, y):
    resource = ProjectSpatialResource.query.filter_by(
        id=resource_id,
        project_id=project_id,
        resource_type="basemap",
        status="active",
    ).first()
    if resource is None or not resource.tile_path:
        return fail_api("项目底图资源不存在"), 404
    if z < 0 or x < 0 or y < 0:
        return fail_api("瓦片坐标非法"), 404
    if resource.min_zoom is not None and z < resource.min_zoom:
        return fail_api("项目底图瓦片不存在"), 404
    if resource.max_zoom is not None and z > resource.max_zoom:
        return fail_api("项目底图瓦片不存在"), 404
    try:
        target = resolve_storage_path(
            get_storage_root(),
            Path(resource.tile_path) / str(z) / str(x) / f"{y}.png",
        )
    except ValueError:
        return fail_api("瓦片坐标非法"), 404
    if not target.is_file():
        return fail_api("项目底图瓦片不存在"), 404
    return send_from_directory(str(target.parent), target.name)


@project_api.get("")
@login_required
def project_list_api():
    filters = {
        "name": request.args.get("name", type=str),
        "region": request.args.get("region", type=str),
        "status": request.args.get("status", type=str),
        "monitor_year": request.args.get("monitor_year", type=int),
    }
    return success_api(data=list_projects(filters))


@project_api.post("")
@login_required
def project_create_api():
    try:
        return success_api(data=create_project(request.json or {}, actor=_request_actor()))
    except Exception as exc:
        return _project_hub_failure_response(exc, "项目创建失败")


@project_api.get("/<int:project_id>/overview")
@login_required
def project_overview_api(project_id):
    try:
        return _read_model_success_api(get_project_overview(project_id))
    except ValueError as error:
        return fail_api(str(error), status=404)


@project_api.get("/<int:project_id>/assets")
@login_required
def project_assets_api(project_id):
    filters = {
        "asset_type": request.args.get("type", type=str),
        "status": request.args.get("status", type=str),
    }
    try:
        return _read_model_success_api(get_project_assets(project_id, filters))
    except ProjectAssetFilterError as error:
        return fail_api(str(error), status=400)
    except Exception as exc:
        # 失败态收口：内部异常只记日志返回通用文案（此前未捕获会裸 500）
        return business_or_server_failure(
            exc, "项目资产读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>")
@login_required
def project_detail_api(project_id):
    try:
        return success_api(data=get_project_detail(project_id))
    except Exception as exc:
        return business_or_server_failure(exc, "项目详情读取失败", logger=LOGGER)


@project_api.patch("/<int:project_id>")
@login_required
def project_update_api(project_id):
    try:
        return success_api(
            data=update_project(project_id, request.json or {}, actor=_request_actor())
        )
    except Exception as exc:
        return _project_hub_failure_response(exc, "项目更新失败", project_id)


@project_api.put("/<int:project_id>/mines")
@login_required
def project_replace_mines_api(project_id):
    try:
        return success_api(
            data=replace_project_mines(
                project_id,
                (request.json or {}).get("mines") or [],
                actor=_request_actor(),
            )
        )
    except Exception as exc:
        return _project_hub_failure_response(exc, "矿山绑定更新失败", project_id)


@project_api.get("/<int:project_id>/spatial")
@login_required
def project_spatial_api(project_id):
    try:
        return success_api(data=get_project_spatial(project_id))
    except Exception as exc:
        return _spatial_failure_response(exc, "空间资源读取失败", project_id)


@project_api.post("/<int:project_id>/spatial/mines/preview")
@login_required
def project_mines_preview_api(project_id):
    try:
        payload = request.json or {}
        return success_api(data=preview_mine_vector(payload.get("filename"), payload.get("content")))
    except Exception as exc:
        return _spatial_failure_response(exc, "矿山文件预览失败", project_id)


@project_api.post("/<int:project_id>/spatial/mines")
@login_required
def project_mines_import_api(project_id):
    try:
        payload = request.json or {}
        return success_api(
            data=import_mine_vector(
                project_id,
                payload.get("filename"),
                payload.get("content"),
                payload.get("field_mapping") or {},
                actor=_request_actor(),
            )
        )
    except Exception as exc:
        return _spatial_failure_response(exc, "矿山导入失败", project_id)


@project_api.get("/<int:project_id>/spatial/basemap-candidates")
@login_required
def project_basemap_candidates_api(project_id):
    try:
        return success_api(data={"items": list_basemap_candidates(project_id)})
    except Exception as exc:
        return _spatial_failure_response(exc, "底图候选读取失败", project_id)


def _request_zoom(value, default):
    """min/max zoom 路由层校验：非整数时返回 None（解释器报错原文不下发客户端）。"""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@project_api.post("/<int:project_id>/spatial/basemaps")
@login_required
def project_basemap_register_api(project_id):
    try:
        payload = request.json or {}
        min_zoom = _request_zoom(payload.get("min_zoom"), 8)
        max_zoom = _request_zoom(payload.get("max_zoom"), 15)
        if min_zoom is None or max_zoom is None:
            return fail_api(msg="min_zoom/max_zoom 必须是整数", status=422)
        return success_api(
            data=register_basemap(
                project_id,
                payload.get("candidate"),
                min_zoom,
                max_zoom,
                actor=_request_actor(),
            )
        )
    except Exception as exc:
        return _spatial_failure_response(exc, "底图处理启动失败", project_id)


@project_api.get("/<int:project_id>/spatial/jobs/<string:job_id>")
@login_required
def project_spatial_job_api(project_id, job_id):
    try:
        return success_api(data=get_spatial_job(project_id, job_id))
    except Exception as exc:
        return _spatial_failure_response(exc, "空间任务读取失败", project_id)


@project_api.post("/<int:project_id>/spatial/jobs/<string:job_id>/retry")
@login_required
def project_spatial_job_retry_api(project_id, job_id):
    try:
        return success_api(data=retry_spatial_job(project_id, job_id, actor=_request_actor()))
    except Exception as exc:
        return _spatial_failure_response(exc, "空间任务重试失败", project_id)


@project_api.post("/<int:project_id>/spatial/jobs/<string:job_id>/cancel")
@login_required
def project_spatial_job_cancel_api(project_id, job_id):
    try:
        return success_api(data=cancel_spatial_job(project_id, job_id, actor=_request_actor()))
    except Exception as exc:
        return _spatial_failure_response(exc, "空间任务取消失败", project_id)


@project_api.get("/<int:project_id>/map/manifest")
@login_required
def project_map_manifest_api(project_id):
    try:
        return success_api(data=get_project_map_manifest(project_id))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目地图清单读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/classification-results")
@login_required
def project_classification_results_list_api(project_id):
    """分类成果清单（M3 编辑导航入口数据）。"""
    try:
        from applications.project_hub.mine_traceability import (
            list_project_classification_results,
        )

        return success_api(data=list_project_classification_results(project_id))
    except Exception as exc:
        return business_or_server_failure(
            exc, "分类成果清单读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/classification-results/<int:result_id>")
@login_required
def classification_result_api(project_id, result_id):
    try:
        return success_api(data=get_classification_result(project_id, result_id))
    except Exception as error:
        return _classification_error_response(error)


@project_api.get("/<int:project_id>/classification-results/<int:result_id>/revisions")
@login_required
def classification_revision_list_api(project_id, result_id):
    try:
        return success_api(data=list_classification_revisions(project_id, result_id))
    except Exception as error:
        return _classification_error_response(error)


@project_api.post("/<int:project_id>/classification-results/<int:result_id>/revisions")
@login_required
def classification_revision_save_api(project_id, result_id):
    raw_body = request.get_data(cache=True)
    payload = request.get_json(silent=True)
    if payload is None:
        return fail_api(
            "分类成果保存数据不合法",
            status=422,
            details={"field": "body", "reason": "必须提交 JSON"},
        )
    try:
        return success_api(
            data=save_classification_revision(
                project_id,
                result_id,
                payload,
                # actor 语义统一为用户名（与其余活动记录一致），混用 user_id 会导致审计无法按人追溯
                actor=_request_actor(),
                body_size=len(raw_body),
            )
        )
    except Exception as error:
        return _classification_error_response(error)


@project_api.post("/<int:project_id>/classification-results/<int:result_id>/export")
@login_required
def classification_result_export_api(project_id, result_id):
    if request.get_data(cache=False):
        return fail_api(
            "分类成果导出不接受请求体",
            status=422,
            details={"field": "body", "reason": "导出仅使用服务器当前版本"},
        )
    try:
        result, feature_collection = export_current_classification_result(project_id, result_id)
    except Exception as error:
        return _classification_error_response(error)
    return send_file(
        BytesIO(json.dumps(feature_collection, ensure_ascii=False).encode("utf-8")),
        mimetype="application/geo+json",
        as_attachment=True,
        download_name=f"classification-result-{result.id}-revision-{result.current_revision_no}.geojson",
    )


@project_api.get("/<int:project_id>/geojson")
@login_required
def project_geojson_api(project_id):
    try:
        return success_api(data=get_project_geojson(project_id))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/stats")
@login_required
def project_stats_api(project_id):
    try:
        return success_api(data=get_project_stats(project_id))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/mines/search")
@login_required
def project_mines_search_api(project_id):
    try:
        return success_api(data=search_project_mines(project_id, request.args.get("q")))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/mines/indices")
@login_required
def project_mines_indices_api(project_id):
    try:
        return success_api(data=get_project_mine_indices(project_id, request.args.get("fid")))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/mines/original-imagery")
@login_required
def project_mines_original_imagery_api(project_id):
    try:
        return success_api(data=list_project_original_imagery(project_id, request.args.get("fid")))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/imagery/candidates")
@login_required
def project_imagery_candidates_api(project_id):
    """可操作影像清单（S3）：数据集 imagery + 推理输入。"""
    try:
        from applications.project_hub.imagery_processing import list_imagery_candidates

        return success_api(data=list_imagery_candidates(project_id))
    except Exception as exc:
        return business_or_server_failure(
            exc, "影像清单读取失败", logger=LOGGER, business_status=404
        )


@project_api.post("/<int:project_id>/imagery/clip")
@login_required
def project_imagery_clip_api(project_id):
    """范围裁剪（S3）：多边形/矿山边界 + 外扩（米）。"""
    try:
        from applications.project_hub.imagery_processing import ImageryProcessingError, clip_imagery

        return success_api(data=clip_imagery(project_id, request.get_json(silent=True) or {}))
    except ImageryProcessingError as exc:
        return fail_api(str(exc)), 400
    except Exception as exc:
        return business_or_server_failure(exc, "影像裁剪失败", logger=LOGGER)


@project_api.post("/<int:project_id>/imagery/slice")
@login_required
def project_imagery_slice_api(project_id):
    """自动切片（S3）：固定像素/固定面积网格，实体切片登记数据集。"""
    try:
        from applications.project_hub.imagery_processing import ImageryProcessingError, slice_imagery

        return success_api(data=slice_imagery(project_id, request.get_json(silent=True) or {}))
    except ImageryProcessingError as exc:
        return fail_api(str(exc)), 400
    except Exception as exc:
        return business_or_server_failure(exc, "影像切片失败", logger=LOGGER)


@project_api.get("/<int:project_id>/mines/parcel-summary")
@login_required
def project_mines_parcel_summary_api(project_id):
    """图斑清单（M3 补交）：每矿山图斑数/最新年份/最近解译时间。"""
    try:
        from applications.project_hub.parcel_summary import build_parcel_summary

        page = max(1, request.args.get("page", 1, type=int) or 1)
        limit = min(200, max(1, request.args.get("limit", 50, type=int) or 50))
        return success_api(data=build_parcel_summary(
            project_id, page=page, limit=limit, fid_filter=request.args.get("fid"),
        ))
    except ValueError as exc:
        return fail_api(str(exc)), 400
    except Exception as exc:
        return business_or_server_failure(
            exc, "图斑清单读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/mines/<int:fid>/traceability")
@login_required
def project_mine_traceability_api(project_id, fid):
    """图斑溯源（M3）：按矿山一键调取历年成果/占比/修订/变化/指数。"""
    try:
        from applications.project_hub.mine_traceability import build_mine_traceability

        return success_api(data=build_mine_traceability(project_id, fid))
    except Exception as exc:
        return business_or_server_failure(
            exc, "图斑溯源数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/mines/original-imagery/<job_id>/download")
@login_required
def project_mines_original_imagery_download_api(project_id, job_id):
    try:
        path = resolve_project_original_imagery_download(project_id, job_id)
    except FileNotFoundError as exc:
        return fail_api(str(exc)), 404
    except Exception as exc:
        return business_or_server_failure(
            exc, "原始影像读取失败", logger=LOGGER, business_status=404
        )
    return send_file(path, as_attachment=True, download_name=path.name)


@project_api.get("/<int:project_id>/mines/change-matrix")
@login_required
def project_mines_change_matrix_api(project_id):
    try:
        return success_api(data=get_project_change_matrix(project_id, request.args.get("fid")))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.get("/<int:project_id>/mines/trend-report")
@login_required
def project_mines_trend_report_api(project_id):
    try:
        return success_api(data=get_project_trend_report(project_id, request.args.get("fid")))
    except Exception as exc:
        return business_or_server_failure(
            exc, "项目数据读取失败", logger=LOGGER, business_status=404
        )


@project_api.post("/<int:project_id>/datasets")
@login_required
def project_dataset_create_api(project_id):
    try:
        return success_api(
            data=create_dataset(project_id, request.json or {}, actor=_request_actor())
        )
    except ProjectStorageValidationError as exc:
        return fail_api(str(exc), status=422)
    except ValueError as exc:
        return fail_api(str(exc))
    except Exception as exc:
        return _project_hub_failure_response(exc, "影像登记失败", project_id)


@project_api.get("/<int:project_id>/timeline")
@login_required
def project_timeline_api(project_id):
    try:
        return success_api(data=get_project_timeline(project_id))
    except Exception as exc:
        return _project_hub_failure_response(exc, "项目活动读取失败", project_id)


@project_api.delete("/<int:project_id>")
@login_required
def project_delete_api(project_id):
    """删除项目（M2）：仅归档项目可删；目录移入 trash 可人工恢复。"""
    try:
        return success_api(data=delete_project(project_id, actor=_request_actor()))
    except ProjectDeleteNotAllowed as exc:
        return fail_api(str(exc), status=409)
    except ValueError as exc:
        return fail_api(str(exc), status=404)
    except Exception as exc:
        return business_or_server_failure(exc, "项目删除失败", logger=LOGGER)


@project_api.post("/batch/archive")
@login_required
def project_batch_archive_api():
    payload = request.get_json(silent=True) or {}
    project_ids = payload.get("project_ids")
    if not isinstance(project_ids, list) or not project_ids:
        return fail_api("project_ids 必须是非空数组", status=422)
    if len(project_ids) > 100:
        return fail_api("单次批量操作最多 100 个项目", status=422)
    return success_api(
        data=batch_archive_projects(project_ids, actor=_request_actor())
    )


@project_api.post("/batch/delete")
@login_required
def project_batch_delete_api():
    payload = request.get_json(silent=True) or {}
    project_ids = payload.get("project_ids")
    if not isinstance(project_ids, list) or not project_ids:
        return fail_api("project_ids 必须是非空数组", status=422)
    if len(project_ids) > 100:
        return fail_api("单次批量操作最多 100 个项目", status=422)
    return success_api(
        data=batch_delete_projects(project_ids, actor=_request_actor())
    )


@project_api.post("/<int:project_id>/archive")
@login_required
def project_archive_api(project_id):
    try:
        return success_api(data=archive_project(project_id, actor=_request_actor()))
    except Exception as exc:
        return _project_hub_failure_response(exc, "项目归档失败", project_id)


@project_api.post("/<int:project_id>/restore")
@login_required
def project_restore_api(project_id):
    try:
        return success_api(data=restore_project(project_id, actor=_request_actor()))
    except Exception as exc:
        return _project_hub_failure_response(exc, "项目恢复失败", project_id)


@project_api.post("/<int:project_id>/exports")
@login_required
def project_export_create_api(project_id):
    try:
        return success_api(
            data=create_export(project_id, request.json or {}, actor=_request_actor())
        )
    except ProjectStorageValidationError as exc:
        return fail_api(str(exc), status=422)
    except ValueError as exc:
        return fail_api(str(exc))
    except Exception:
        LOGGER.exception("项目导出创建失败 project_id=%s", project_id)
        return fail_api("项目导出失败，请检查服务日志", status=500)


@project_api.get("/<int:project_id>/exports")
@login_required
def project_export_list_api(project_id):
    try:
        return success_api(data=list_exports(project_id))
    except Exception as exc:
        return business_or_server_failure(exc, "项目导出记录读取失败", logger=LOGGER)


@project_api.post("/<int:project_id>/backups")
@login_required
def project_backup_create_api(project_id):
    try:
        return success_api(
            data=create_backup(project_id, request.json or {}, actor=_request_actor())
        )
    except ProjectStorageValidationError as exc:
        return fail_api(str(exc), status=422)
    except ValueError as exc:
        return fail_api(str(exc))
    except Exception:
        LOGGER.exception("项目配置快照创建失败 project_id=%s", project_id)
        return fail_api("项目配置快照创建失败，请检查服务日志", status=500)


@project_api.post("/<int:project_id>/backups/import")
@login_required
def project_backup_import_api(project_id):
    """跨环境快照导入（M2）：JSON body 直传 manifest（与 KML 上传同模式）。"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("manifest"), dict):
        return fail_api("请求必须包含 manifest 对象", status=422)
    try:
        return success_api(
            data=import_backup_manifest(
                project_id, payload["manifest"], actor=_request_actor()
            )
        )
    except ProjectStorageValidationError as exc:
        return fail_api(str(exc), status=422)
    except ValueError as exc:
        return fail_api(str(exc), status=404)
    except Exception as exc:
        return business_or_server_failure(exc, "快照导入失败", logger=LOGGER)


@project_api.get("/<int:project_id>/backups")
@login_required
def project_backup_list_api(project_id):
    try:
        return success_api(data=list_backups(project_id))
    except Exception as exc:
        return business_or_server_failure(exc, "项目快照列表读取失败", logger=LOGGER)


@project_api.post("/<int:project_id>/backups/<int:backup_id>/restore")
@login_required
def project_backup_restore_api(project_id, backup_id):
    try:
        return success_api(
            data=restore_backup(project_id, backup_id, actor=_request_actor())
        )
    except ProjectStorageValidationError as exc:
        return fail_api(str(exc), status=422)
    except ValueError as exc:
        return fail_api(str(exc))
    except Exception:
        LOGGER.exception(
            "项目配置快照恢复失败 project_id=%s backup_id=%s", project_id, backup_id
        )
        return fail_api("项目配置快照恢复失败，请检查服务日志", status=500)
