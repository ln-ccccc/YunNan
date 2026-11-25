# -*- coding: utf-8 -*-
"""只读导出：全部未删除项目的 ready 成果（ROI + 当前版本全部图斑几何 + label 像元尺寸），NDJSON，每行一条成果。
    MSYS_NO_PATHCONV=1 docker exec -i -w /app/backend yunnan-backend /opt/conda/envs/MMSeg310/bin/python - < dump_production.py > production-results.ndjson
不写任何数据；输出只有 stdout。
"""
import json
import sys

import rasterio
from shapely.geometry import shape

from applications import create_app
from applications.models.classification_result import ClassificationResult
from applications.models.project import Project
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

sys.stdout.reconfigure(encoding="utf-8")
app = create_app("production")
root = get_storage_root()
written = 0
with app.app_context():
    alive = {project.id for project in Project.query.filter_by(deleted_at=None).all()}
    query = ClassificationResult.query.filter_by(vector_status="ready").order_by(ClassificationResult.id)
    for result in query.yield_per(20):
        if result.project_id not in alive:
            continue
        pixel = None
        try:
            with rasterio.open(resolve_storage_path(root, result.label_path)) as dataset:
                pixel = [abs(dataset.transform.a), abs(dataset.transform.e), dataset.width, dataset.height]
        except Exception:  # noqa: BLE001
            pixel = None
        collection = json.loads(result.current_feature_collection_json)
        roi = shape(json.loads(result.roi_geometry_json))
        sys.stdout.write(json.dumps({
            "result_id": result.id, "project_id": result.project_id, "mine_fid": result.mine_fid, "year": result.year,
            "revision": result.current_revision_no, "pixel": pixel, "roi": json.loads(result.roi_geometry_json),
            "roi_is_valid": bool(roi.is_valid), "roi_area": roi.area,
            "features": [{"fid": feature["properties"]["feature_id"], "cls": feature["properties"]["class_code"],
                          "source": feature["properties"]["source"], "geometry": feature["geometry"]} for feature in collection["features"]],
        }, ensure_ascii=False, separators=(",", ":")) + "\n")
        written += 1
    print(f"exported {written} results", file=sys.stderr)
