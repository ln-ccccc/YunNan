from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window
from rasterio.warp import transform_geom
from rasterio.windows import Window

from applications.kml_roi.kml import load_vector_features
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


def _standalone_scope(*, project_id=None, mine_resource_id=None, vector_path=None, warnings=None):
    return {
        "mode": "standalone",
        "project_id": project_id,
        "mine_resource_id": mine_resource_id,
        "vector_path": str(vector_path) if vector_path else None,
        "matched_fids": [],
        "warnings": list(warnings or []),
    }


def _geometry_has_valid_pixels(dataset, geometry_4326):
    geometry = transform_geom("EPSG:4326", dataset.crs, geometry_4326, precision=6)
    try:
        window = geometry_window(dataset, [geometry])
        window = window.intersection(Window(0, 0, dataset.width, dataset.height))
    except (WindowError, ValueError):
        return False

    height = int(window.height)
    width = int(window.width)
    if height <= 0 or width <= 0:
        return False
    mine_mask = geometry_mask(
        [geometry],
        out_shape=(height, width),
        transform=dataset.window_transform(window),
        invert=True,
    )
    valid_mask = dataset.read_masks(1, window=window) > 0
    return bool(np.any(mine_mask & valid_mask))


def resolve_interpretation_scope(project_id, tif_path):
    if project_id in (None, ""):
        return _standalone_scope()
    try:
        project_id = int(project_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("project_id 必须是正整数") from exc
    if project_id <= 0:
        raise ValueError("project_id 必须是正整数")

    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError("项目不存在")
    resource = (
        ProjectSpatialResource.query.filter_by(
            project_id=project_id,
            resource_type="mine_vector",
            status="active",
        )
        .order_by(ProjectSpatialResource.version.desc())
        .first()
    )
    if resource is None or not resource.normalized_path:
        raise ValueError("项目尚未激活矿山资源")

    vector_path = resolve_storage_path(get_storage_root(), resource.normalized_path)
    if not vector_path.is_file():
        raise ValueError("项目活动矿山资源文件不存在")
    bound_fids = {binding.mine_fid for binding in project.mines}
    features = []
    for fid, geometry in load_vector_features(vector_path):
        try:
            fid_value = int(fid)
        except (TypeError, ValueError):
            continue
        if fid_value in bound_fids:
            features.append((fid_value, geometry))

    tif_path = Path(tif_path).expanduser().resolve()
    if not tif_path.is_file():
        raise ValueError("TIFF 文件不存在")
    with rasterio.open(tif_path) as dataset:
        if dataset.crs is None:
            return _standalone_scope(
                project_id=project_id,
                mine_resource_id=resource.id,
                vector_path=vector_path,
                warnings=["TIFF 缺少 CRS，无法进行项目空间关联"],
            )
        matched_fids = sorted(
            fid
            for fid, geometry in features
            if _geometry_has_valid_pixels(dataset, geometry)
        )

    if not matched_fids:
        return _standalone_scope(
            project_id=project_id,
            mine_resource_id=resource.id,
            vector_path=vector_path,
        )
    return {
        "mode": "project",
        "project_id": project_id,
        "mine_resource_id": resource.id,
        "vector_path": str(vector_path),
        "matched_fids": matched_fids,
        "warnings": [],
    }
