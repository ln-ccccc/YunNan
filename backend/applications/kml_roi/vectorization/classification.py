from typing import Dict, List, Tuple

import cv2
import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.warp import transform_geom
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union

from applications.kml_roi.raster_ops import WGS84, has_valid_geotransform, rasterize_roi_for_label


CLASS_DEFINITIONS = {
    0: {"class_name": "grassland", "class_name_zh": "草地", "rgb": [0, 255, 0]},
    1: {"class_name": "forest", "class_name_zh": "林地", "rgb": [0, 128, 0]},
    2: {"class_name": "building", "class_name_zh": "建筑", "rgb": [255, 0, 0]},
    3: {"class_name": "road", "class_name_zh": "道路", "rgb": [255, 255, 0]},
    4: {"class_name": "bareground", "class_name_zh": "裸地", "rgb": [255, 0, 255]},
    5: {"class_name": "water", "class_name_zh": "水体", "rgb": [0, 191, 255]},
}
NODATA_CLASS = 255
MINIMUM_COMPONENT_PIXELS = 16


def _polygonal_geometry(geometry):
    if geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    if geometry.is_empty:
        return None
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygon_parts = [
            item
            for item in geometry.geoms
            if isinstance(item, (Polygon, MultiPolygon)) and not item.is_empty
        ]
        if not polygon_parts:
            return None
        merged = unary_union(polygon_parts)
        return _polygonal_geometry(merged)
    return None


def _validate_label_dataset(dataset, labels):
    if dataset.count != 1:
        raise ValueError("分类标签必须是单波段 GeoTIFF")
    if dataset.dtypes[0] != "uint8":
        raise ValueError("分类标签必须为 uint8")
    if dataset.nodata != NODATA_CLASS:
        raise ValueError("分类标签 NoData 必须为 255")
    if dataset.crs is None or not has_valid_geotransform(dataset.transform):
        raise ValueError("分类标签缺少有效空间参考")
    if np.any((labels < 0) | ((labels > 5) & (labels != NODATA_CLASS))):
        raise ValueError("分类标签包含不支持的类别代码")


def _component_polygon(component_mask, transform, roi_in_label_crs):
    for geometry, value in shapes(
        component_mask.astype(np.uint8),
        mask=component_mask,
        transform=transform,
    ):
        if int(value) != 1:
            continue
        polygon = _polygonal_geometry(shape(geometry).intersection(roi_in_label_crs))
        if polygon is not None:
            return polygon
    return None


def _auto_feature(*, geometry, result_id, class_code, ordinal):
    definition = CLASS_DEFINITIONS[class_code]
    return {
        "type": "Feature",
        "properties": {
            "feature_id": f"auto-{result_id}-{class_code}-{ordinal:04d}",
            "class_code": class_code,
            "class_name": definition["class_name"],
            "source": "auto",
            "result_id": result_id,
            "revision_no": 0,
        },
        "geometry": mapping(geometry),
    }


def vectorize_label_geotiff(label_path, roi_geometry_4326: Dict, *, result_id: int) -> Dict:
    """Convert one v1 label GeoTIFF to deterministic, ROI-clipped EPSG:4326 features."""
    try:
        result_id = int(result_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("result_id 必须是正整数") from exc
    if result_id <= 0:
        raise ValueError("result_id 必须是正整数")
    if not isinstance(roi_geometry_4326, dict):
        raise ValueError("矿山 ROI 必须是 GeoJSON geometry")

    with rasterio.open(label_path) as dataset:
        labels = dataset.read(1)
        _validate_label_dataset(dataset, labels)
        roi_mask = rasterize_roi_for_label(
            roi_geometry_4326,
            dataset.crs,
            dataset.transform,
            labels.shape,
        )
        roi_in_label_crs = _polygonal_geometry(
            shape(transform_geom(WGS84, dataset.crs, roi_geometry_4326, precision=10))
        )
        if roi_in_label_crs is None:
            raise ValueError("矿山 ROI 不是有效面几何")

        features: List[Dict] = []
        for class_code in sorted(CLASS_DEFINITIONS):
            class_mask = (labels == class_code) & roi_mask
            component_count, component_labels, component_stats, _ = cv2.connectedComponentsWithStats(
                class_mask.astype(np.uint8),
                connectivity=4,
            )
            components: List[Tuple[Tuple[int, int], object]] = []
            for component_id in range(1, component_count):
                if int(component_stats[component_id, cv2.CC_STAT_AREA]) < MINIMUM_COMPONENT_PIXELS:
                    continue
                component_mask = component_labels == component_id
                rows, columns = np.where(component_mask)
                polygon = _component_polygon(component_mask, dataset.transform, roi_in_label_crs)
                if polygon is not None:
                    components.append(((int(rows.min()), int(columns.min())), polygon))

            valid_polygons = []
            for _, polygon in sorted(components, key=lambda item: item[0]):
                geometry_4326 = _polygonal_geometry(
                    shape(transform_geom(dataset.crs, WGS84, mapping(polygon), precision=10))
                )
                if geometry_4326 is not None:
                    valid_polygons.append(geometry_4326)
            for ordinal, geometry_4326 in enumerate(valid_polygons, start=1):
                features.append(
                    _auto_feature(
                        geometry=geometry_4326,
                        result_id=result_id,
                        class_code=class_code,
                        ordinal=ordinal,
                    )
                )

    return {"type": "FeatureCollection", "features": features}
