from pathlib import Path

from flask import Blueprint, request, send_from_directory
from sqlalchemy import desc

from applications.auth.guard import ensure_logged_in
from applications.common.curd import model_to_dicts
from applications.common.path_global import generate_dir, generate_url, fun_type_2, fun_type_3, fun_type_4, fun_type_5, up_dir
from applications.common.utils import type_utils
from applications.common.utils.http import fail_api, success_api, table_api
from applications.common.utils.type_utils import items_handle
from applications.common.utils.upload import img_url_handle
from applications.interface.analysis import handle, spectral_index_calculation, terrain_classification
from applications.inference.paths import resolve_output_file
from applications.inference.interpretation import prepare_geoview_interpretation, resolve_uploaded_tiff
from applications.inference.jobs import serialize_job
from applications.inference.routing import resolve_interpretation_scope
from applications.models.analysis import Analysis
from applications.models.project import Project
from applications.project_hub.inference_results import upsert_project_index_results
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path
from applications.schemas import AnalysisSchema

analysis_api = Blueprint('analysis_api', __name__, url_prefix='/api/analysis')
repo_root = Path(__file__).resolve().parents[3]
miner_change_output_root = repo_root / 'miner' / 'change_matrix_outputs'


@analysis_api.before_request
def require_analysis_auth():
    return ensure_logged_in()


def _iter_flash_records():
    records = []
    if not miner_change_output_root.exists():
        return records

    for fid_dir in miner_change_output_root.iterdir():
        if not fid_dir.is_dir():
            continue
        fid = fid_dir.name
        for p in fid_dir.glob("*.png"):
            name = p.name
            if "_mask" in name:
                continue
            if "+" not in name:
                continue
            # keep year-naming outputs, e.g. 11192+2024.png
            stem = p.stem
            parts = stem.split("+", 1)
            if len(parts) != 2 or not parts[1].isdigit():
                continue
            records.append({
                "record_id": f"{fid}|{name}",
                "fid": fid,
                "filename": name,
                "mtime": p.stat().st_mtime,
            })
    records.sort(key=lambda x: x["mtime"], reverse=True)
    return records


def _iter_project_records(project_id):
    try:
        project_id = int(project_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("project_id 必须是正整数") from exc
    if project_id <= 0:
        raise ValueError("project_id 必须是正整数")
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError("项目不存在")

    output_root = resolve_storage_path(
        get_storage_root(),
        Path("projects") / str(project.id) / "outputs" / "inference",
    )
    records = []
    for fid in sorted({binding.mine_fid for binding in project.mines}):
        fid_dir = output_root / str(fid)
        if not fid_dir.is_dir():
            continue
        for result_path in fid_dir.glob(f"{fid}+*.png"):
            year_text = result_path.stem.split("+", 1)[-1]
            if not year_text.isdigit() or len(year_text) != 4:
                continue
            source_path = fid_dir / f"{fid}+{year_text}_src.png"
            records.append({
                "record_id": f"{project.id}|{fid}|{result_path.name}",
                "project_id": project.id,
                "fid": fid,
                "year": int(year_text),
                "filename": result_path.name,
                "source_filename": source_path.name if source_path.is_file() else None,
                "mtime": result_path.stat().st_mtime,
            })
    records.sort(key=lambda item: item["mtime"], reverse=True)
    return records


@analysis_api.get('/show/<analysis_type>')
def show_result(analysis_type):
    if not hasattr(type_utils, analysis_type):
        return fail_api("当前类型暂未开放")

    page = int(request.args.get('page', 1) or 1)
    limit = int(request.args.get('limit', 10) or 10)
    query = Analysis.query.filter_by(type=getattr(type_utils, analysis_type)).order_by(desc(Analysis.create_time))

    pagination = query.paginate(page=page, per_page=limit, error_out=False)
    data = model_to_dicts(schema=AnalysisSchema, data=pagination.items)
    data = items_handle(data)

    return table_api(data=data, count=pagination.total)


@analysis_api.post('/semantic_segmentation')
def semantic_segmentation_api():
    req_json = request.json or {}
    model_path = req_json.get("model_path") or "mmseg:cc-ln/CUGRS"
    if not str(model_path).startswith("mmseg:"):
        return fail_api("地物分类仅支持多要素模型 mmseg:cc-ln/CUGRS")

    img_list = req_json.get("list")
    if not img_list:
        return fail_api("请上传图片")

    step1_ = req_json.get("prehandle")
    step2_ = req_json.get("denoise")
    if step1_ not in (0, fun_type_2, fun_type_4) or step2_ not in (0, fun_type_3, fun_type_5):
        return fail_api("参数异常")

    try:
        terrain_classification(model_path, up_dir, generate_dir, img_list, step1_, step2_, type_=3)
        return success_api()
    except Exception as e:
        return fail_api(f"推理失败: {str(e)}")

@analysis_api.post('/image_pre')
def image_pre_api():
    req_json = request.json or {}
    img_list = req_json.get("list")
    step1_ = req_json.get("prehandle")
    type_ = req_json.get("type")

    if not img_list:
        return fail_api("请上传图片")
    if step1_ not in (fun_type_2, fun_type_4):
        return fail_api("请求参数异常")
    if type_ == 1:
        return fail_api("当前模式不支持")

    temps = [img_url_handle(u) for u in img_list]
    imgs = handle(step1_, temps, up_dir, generate_dir)
    for i, img in enumerate(imgs):
        imgs[i] = generate_url + img
    return success_api(data=imgs)


@analysis_api.post('/spectral_indices')
def spectral_indices_api():
    req_json = request.json or {}
    img_list = req_json.get("list")
    if not img_list:
        return fail_api("请上传图片")

    index_type = req_json.get("index_type", "NDVI")
    year = req_json.get("year", "")
    band_map = req_json.get("band_map", {})
    kml_path = req_json.get("kml_path")
    fid = req_json.get("fid")
    project_id = req_json.get("project_id")
    # 计算核心使用小写键（nir/red/green/swir），这里统一标准化避免前端大小写差异导致映射失效
    normalized_band_map = {str(k).lower(): v for k, v in (band_map or {}).items()}

    try:
        routing = {
            "mode": "standalone",
            "project_id": None,
            "matched_fids": [],
            "synced_fids": [],
            "warnings": [],
        }
        vector_path = kml_path
        allowed_fids = [fid] if fid not in (None, "") else None
        if project_id not in (None, ""):
            scopes = []
            for item in img_list:
                raw_path = item.get("raw_tiff_path") if isinstance(item, dict) else None
                tif_path = resolve_uploaded_tiff(raw_path)
                scopes.append(resolve_interpretation_scope(project_id, tif_path))
            matched_fids = sorted({
                mine_fid
                for scope in scopes
                for mine_fid in scope.get("matched_fids") or []
            })
            warnings = [
                warning
                for scope in scopes
                for warning in scope.get("warnings") or []
            ]
            project_id = int(project_id)
            vector_path = next(
                (scope.get("vector_path") for scope in scopes if scope.get("vector_path")),
                None,
            ) if matched_fids else None
            allowed_fids = matched_fids
            routing.update({
                "mode": "project" if matched_fids else "standalone",
                "project_id": project_id,
                "matched_fids": matched_fids,
                "warnings": warnings,
            })
        result = spectral_index_calculation(
            up_dir,
            generate_dir,
            img_list,
            index_type,
            year,
            normalized_band_map,
            type_=8,
            vector_path=vector_path,
            allowed_fids=allowed_fids,
            sync_global=False,
        )
        if routing["mode"] == "project":
            fid_stats = [
                row
                for record in result.get("records") or []
                for row in record.get("fid_stats") or []
            ]
            try:
                publication = upsert_project_index_results(
                    project_id,
                    index_type,
                    year,
                    fid_stats,
                )
                routing["synced_fids"] = publication["synced_fids"]
            except Exception as sync_error:
                routing["warnings"].append(f"项目指数同步失败: {sync_error}")
        result["routing"] = routing
        if routing["warnings"]:
            return success_api(msg="计算完成，项目同步部分失败", data=result)
        if routing["mode"] == "project":
            return success_api(
                msg=f"计算完成，已同步到当前项目 {len(routing['synced_fids'])} 个矿山",
                data=result,
            )
        if routing["project_id"]:
            return success_api(msg="未匹配当前项目矿山，结果仅在解译平台展示", data=result)
        return success_api(msg="计算完成，结果仅在解译平台展示", data=result)
    except Exception as e:
        return fail_api(f"计算失败: {str(e)}")


@analysis_api.post('/kml_roi_inference')
def kml_roi_inference_api():
    try:
        result = prepare_geoview_interpretation(request.get_json(silent=True) or {})
        job = result.get("job")
        if job is None:
            return success_api(data=result)
        data = {**result, "job": serialize_job(job)}
        return success_api(data=data), 201
    except (ValueError, FileNotFoundError) as error:
        return fail_api(str(error)), 400


@analysis_api.get('/kml_roi_output/<fid>/<filename>')
def kml_roi_output_file(fid, filename):
    try:
        target = resolve_output_file(miner_change_output_root, fid, filename)
    except ValueError as error:
        return fail_api(str(error)), 400
    if not target.is_file():
        return fail_api("结果目录不存在")
    return send_from_directory(str(target.parent), target.name)


@analysis_api.get('/kml_roi_history')
def kml_roi_history_list():
    page = int(request.args.get('page', 1) or 1)
    limit = int(request.args.get('limit', 20) or 20)
    page = max(1, page)
    limit = max(1, min(100, limit))

    project_id = request.args.get("project_id")
    try:
        records = (
            _iter_project_records(project_id)
            if project_id not in (None, "")
            else _iter_flash_records()
        )
    except ValueError as error:
        return fail_api(str(error)), 400
    total = len(records)
    start = (page - 1) * limit
    end = start + limit
    page_items = records[start:end]

    data = []
    for idx, rec in enumerate(page_items):
        fid = rec["fid"]
        filename = rec["filename"]
        if rec.get("project_id"):
            base_url = f"/api/projects/{rec['project_id']}/outputs/inference/{fid}"
            img_url = f"{base_url}/{filename}"
            before_url = (
                f"{base_url}/{rec['source_filename']}"
                if rec.get("source_filename")
                else img_url
            )
            record_data = {
                "mode": "project",
                "project_id": rec["project_id"],
                "fid": fid,
                "year": rec["year"],
                "file": filename,
            }
        else:
            img_url = f"/api/analysis/kml_roi_output/{fid}/{filename}"
            stem = Path(filename).stem
            src_name = f"{stem}_src.png"
            src_path = miner_change_output_root / str(fid) / src_name
            before_url = f"/api/analysis/kml_roi_output/{fid}/{src_name}" if src_path.exists() else img_url
            record_data = {"mode": "flash", "fid": fid, "file": filename}
        data.append({
            "id": total - start - idx,
            "record_id": rec["record_id"],
            "type": "地物分类",
            "before_img": before_url,
            "after_img": img_url,
            "data": record_data,
        })
    return table_api(data=data, count=total, limit=limit)


@analysis_api.delete('/kml_roi_history/item')
def kml_roi_history_remove_one():
    req_json = request.json or {}
    record_id = str(req_json.get("record_id", "")).strip()
    if "|" not in record_id:
        return fail_api("参数异常")
    fid, filename = record_id.split("|", 1)
    if not fid or not filename:
        return fail_api("参数异常")
    try:
        target = resolve_output_file(miner_change_output_root, fid, filename)
        src_target = resolve_output_file(miner_change_output_root, fid, f"{Path(filename).stem}_src.png")
    except ValueError as error:
        return fail_api(str(error)), 400
    if not target.exists():
        return fail_api("记录不存在")
    target.unlink()
    if src_target.exists():
        src_target.unlink()
    return success_api(msg="删除成功")


@analysis_api.delete('/kml_roi_history/clear')
def kml_roi_history_clear():
    removed = 0
    for rec in _iter_flash_records():
        fid = rec["fid"]
        filename = rec["filename"]
        target = miner_change_output_root / fid / filename
        if target.exists():
            target.unlink()
            removed += 1
        src_target = miner_change_output_root / fid / f"{Path(filename).stem}_src.png"
        if src_target.exists():
            src_target.unlink()
    return success_api(msg="清理成功", data={"removed": removed})
