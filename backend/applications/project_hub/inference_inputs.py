"""Resolve browser-safe project inference references into private worker inputs."""

from collections.abc import Mapping
import re
from pathlib import Path, PureWindowsPath

from rasterio.errors import RasterioError

from applications.models.project import Project, ProjectDataset
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.service import get_project_overview
from applications.project_hub.spatial_storage import (
    SUPPORTED_INCOMING_RASTER_FORMATS,
    get_storage_root,
    resolve_storage_path,
    resolve_project_imagery_path,
    resolve_basemap_source_path,
)

class ProjectInferenceInputError(ValueError):
    """Raised when a project reference cannot safely start an inference job."""


_YEAR_VALUE = re.compile(r"^\d{4}$")


def _positive_integer(value, field_name):
    if isinstance(value, bool):
        raise ProjectInferenceInputError(f"{field_name} 必须是正整数")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise ProjectInferenceInputError(f"{field_name} 必须是正整数")
    if result <= 0:
        raise ProjectInferenceInputError(f"{field_name} 必须是正整数")
    return result


def _resolve_registered_tif(storage_root, file_path, project_id=None):
    storage_key = str(file_path or "").strip()
    path = Path(storage_key)
    windows_path = PureWindowsPath(storage_key)
    if (
        not storage_key
        or ".." in path.parts
        or ".." in windows_path.parts
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ProjectInferenceInputError("数据集影像路径必须是 incoming/ 下的相对 TIFF 文件")
    try:
        tif_path = resolve_project_imagery_path(storage_root, storage_key, project_id)
    except ValueError as exc:
        raise ProjectInferenceInputError("数据集影像路径必须位于 incoming/ 或本项目 inputs/imagery 目录") from exc
    if tif_path.suffix.lower().lstrip(".") not in SUPPORTED_INCOMING_RASTER_FORMATS:
        raise ProjectInferenceInputError(
            "数据集影像必须是受支持的栅格格式（tif/tiff/img/jp2）"
        )
    if not tif_path.is_file():
        raise ProjectInferenceInputError("数据集影像文件不存在或不是普通文件")
    return tif_path


def _resolve_inference_year(value, dataset=None):
    candidate = value
    if candidate is None or (isinstance(candidate, str) and not candidate.strip()):
        if dataset is None:
            raise ProjectInferenceInputError("底图原影像必须显式提供四位 year，请填写影像年份")
        candidate = dataset.year_end if dataset.year_end not in (None, "") else dataset.year_start
    if isinstance(candidate, bool) or not _YEAR_VALUE.fullmatch(str(candidate or "").strip()):
        raise ProjectInferenceInputError("year 必须是四位年份，或数据集必须登记年份")
    return str(candidate).strip()


def _normalize_fids(values, field_name):
    if not isinstance(values, (list, tuple, set)):
        raise ProjectInferenceInputError(f"{field_name} 必须是非空正整数列表")
    normalized = sorted({_positive_integer(value, field_name) for value in values})
    if not normalized:
        raise ProjectInferenceInputError(f"{field_name} 必须是非空正整数列表")
    return normalized


def _resolve_scope(scope_resolver, project_id, tif_path):
    if scope_resolver is None:
        from applications.inference.routing import resolve_interpretation_scope

        scope_resolver = resolve_interpretation_scope
    try:
        scope = scope_resolver(project_id, tif_path)
    except (OSError, RasterioError, ValueError) as exc:
        raise ProjectInferenceInputError(
            "无法解析项目影像空间范围，请检查 TIFF 与活动矿山资源"
        ) from exc
    if not isinstance(scope, Mapping) or scope.get("mode") != "project":
        raise ProjectInferenceInputError("影像未与项目已绑定矿山空间相交，无法启动项目推理")
    matched_fids = _normalize_fids(scope.get("matched_fids"), "可用矿山 FID")
    mine_resource_id = _positive_integer(scope.get("mine_resource_id"), "矿山资源")
    vector_path = Path(str(scope.get("vector_path") or "")).expanduser().resolve()
    if not vector_path.is_file():
        raise ProjectInferenceInputError("项目活动矿山资源文件不存在")
    return mine_resource_id, vector_path, matched_fids


def resolve_project_inference_inputs(
    *,
    project_id,
    dataset_id=None,
    input_asset_id=None,
    year=None,
    mine_fids=None,
    device=None,
    scope_resolver=None,
):
    """Return private, canonical Worker inputs for an approved project dataset."""
    project_id = _positive_integer(project_id, "project_id")
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ProjectInferenceInputError("项目不存在")
    storage_root = get_storage_root()
    references = {}
    if input_asset_id is not None:
        match = re.fullmatch(r"(imagery|basemap_imagery):([1-9]\d*)", str(input_asset_id))
        if dataset_id is not None or match is None:
            raise ProjectInferenceInputError("input_asset_id 与 dataset_id 必须且只能提供一项有效引用")
        references["input_asset_id"] = input_asset_id
        asset_type, source_id = match.group(1), int(match.group(2))
        if asset_type == "imagery":
            dataset_id = source_id
        else:
            basemap = (
                ProjectSpatialResource.query.filter_by(
                    project_id=project.id, resource_type="basemap", status="active",
                ).order_by(ProjectSpatialResource.version.desc()).first()
            )
            if basemap is None or basemap.id != source_id:
                raise ProjectInferenceInputError("底图原影像不存在、不属于当前项目或已不是当前激活底图，请刷新影像清单")
            try:
                tif_path = resolve_basemap_source_path(storage_root, basemap)
            except ValueError as exc:
                raise ProjectInferenceInputError("当前激活底图的原影像不可用，请检查源文件并重新登记底图") from exc
            references["basemap_resource_id"] = basemap.id
    dataset = None
    if dataset_id is not None:
        dataset_id = _positive_integer(dataset_id, "dataset_id")
        dataset = ProjectDataset.query.filter_by(id=dataset_id, project_id=project.id).first()
        if dataset is None:
            raise ProjectInferenceInputError("数据集不存在或不属于当前项目")
        if str(dataset.dataset_kind or "").strip().lower() != "imagery":
            raise ProjectInferenceInputError("当前仅支持使用影像数据集启动推理")
        tif_path = _resolve_registered_tif(storage_root, dataset.file_path, project.id)
        references["dataset_id"] = dataset.id
    elif "basemap_resource_id" not in references:
        raise ProjectInferenceInputError("缺少推理影像资产引用")
    normalized_year = _resolve_inference_year(year, dataset)
    overview = get_project_overview(project.id)
    if not bool((overview.get("capabilities") or {}).get("can_start_inference")):
        raise ProjectInferenceInputError("项目当前不满足启动推理条件")

    mine_resource_id, vector_path, matched_fids = _resolve_scope(
        scope_resolver,
        project.id,
        tif_path,
    )
    mine_resource = ProjectSpatialResource.query.filter_by(
        id=mine_resource_id,
        project_id=project.id,
        resource_type="mine_vector",
        status="active",
    ).first()
    if mine_resource is None or not mine_resource.normalized_path:
        raise ProjectInferenceInputError("项目尚未激活矿山资源")
    try:
        expected_vector_path = resolve_storage_path(storage_root, mine_resource.normalized_path)
        vector_path.relative_to(storage_root)
    except ValueError as exc:
        raise ProjectInferenceInputError("项目活动矿山资源路径不合法") from exc
    if vector_path != expected_vector_path:
        raise ProjectInferenceInputError("项目活动矿山资源路径不匹配")

    bound_fids = {
        _positive_integer(binding.mine_fid, "项目矿山 FID")
        for binding in project.mines
    }
    if not set(matched_fids).issubset(bound_fids):
        raise ProjectInferenceInputError("影像相交矿山不属于当前项目")
    selected_fids = matched_fids if mine_fids is None else _normalize_fids(mine_fids, "mine_fids")
    if not set(selected_fids).issubset(matched_fids):
        raise ProjectInferenceInputError("mine_fids 必须是影像相交矿山的非空正整数子集")

    output_root = resolve_storage_path(
        storage_root,
        f"projects/{project.id}/outputs/inference",
    )
    return {
        "project_id": project.id,
        **references,
        "mine_resource_id": mine_resource.id,
        "mine_fids": selected_fids,
        "old_tif_path": str(tif_path),
        "new_tif_path": str(tif_path),
        "kml_path": str(vector_path),
        "output_root": str(output_root),
        "year": normalized_year,
        "device": device,
    }
