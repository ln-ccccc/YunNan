import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import shape

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.kml_roi.vectorization.classification import vectorize_label_geotiff
from applications.models.classification_result import (
    ClassificationEditAudit,
    ClassificationResult,
    ClassificationRevision,
)
from applications.models.project import ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.classification_results import publish_classification_result
from applications.project_hub.service import create_project


class TestClassificationVectorization(unittest.TestCase):
    ROI = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [12.0, 0.0], [12.0, 16.0], [0.0, 16.0], [0.0, 0.0]]
        ],
    }

    def _write_label(self, path, labels):
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=labels.shape[0],
            width=labels.shape[1],
            count=1,
            dtype="uint8",
            nodata=255,
            crs="EPSG:4326",
            transform=from_origin(0.0, 16.0, 1.0, 1.0),
        ) as dataset:
            dataset.write(labels, 1)

    def test_vectorization_uses_4_connected_components_roi_and_stable_ids(self):
        labels = np.full((16, 16), 255, dtype=np.uint8)
        labels[0:4, 0:4] = 0  # Exactly 16 pixels: preserve.
        labels[4:7, 0:5] = 1  # 15 pixels: discard.
        labels[4:8, 8:12] = 2
        labels[8:12, 0:4] = 2
        labels[8:11, 4:7] = 4
        labels[11:14, 7:10] = 4  # Diagonal-only contact: two 9-pixel components.
        labels[0:4, 12:16] = 3  # Outside the ROI: discard.

        with tempfile.TemporaryDirectory() as temp_dir:
            label_path = Path(temp_dir) / "label.tif"
            self._write_label(label_path, labels)

            feature_collection = vectorize_label_geotiff(label_path, self.ROI, result_id=101)

        self.assertEqual(feature_collection["type"], "FeatureCollection")
        features = feature_collection["features"]
        self.assertEqual(
            [item["properties"]["feature_id"] for item in features],
            ["auto-101-0-0001", "auto-101-2-0001", "auto-101-2-0002"],
        )
        self.assertEqual(
            [item["properties"]["class_code"] for item in features],
            [0, 2, 2],
        )
        self.assertEqual(
            [item["properties"]["class_name"] for item in features],
            ["grassland", "building", "building"],
        )
        self.assertTrue(all(item["properties"]["source"] == "auto" for item in features))
        self.assertTrue(all(item["properties"]["result_id"] == 101 for item in features))
        self.assertTrue(all(item["properties"]["revision_no"] == 0 for item in features))
        roi = shape(self.ROI)
        self.assertTrue(all(roi.covers(shape(item["geometry"])) for item in features))


class TestClassificationResultPublishing(unittest.TestCase):
    ROI = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [16.0, 0.0], [16.0, 16.0], [0.0, 16.0], [0.0, 0.0]]
        ],
    }

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.root / "project_storage")
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root
        self.temp_dir.cleanup()

    def _create_project_resource(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        db.session.add(ProjectMineBinding(project_id=project["id"], mine_fid=101, sort_order=0))
        relative_path = Path("projects") / str(project["id"]) / "resources" / "mine-vector-1" / "mines.geojson"
        resource = ProjectSpatialResource(
            project_id=project["id"],
            resource_type="mine_vector",
            version=1,
            status="active",
            source_path=relative_path.as_posix(),
            normalized_path=relative_path.as_posix(),
            source_format="geojson",
            feature_count=1,
            crs="EPSG:4326",
            bounds_json=json.dumps([0.0, 0.0, 16.0, 16.0]),
        )
        db.session.add(resource)
        db.session.commit()
        path = Path(os.environ["PROJECT_STORAGE_ROOT"]) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"FID_1": 101},
                            "geometry": self.ROI,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return project["id"], resource

    def _write_label(self, project_id):
        path = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / "projects"
            / str(project_id)
            / "outputs"
            / "inference"
            / "101"
            / "101+2024_label.tif"
        )
        labels = np.full((16, 16), 255, dtype=np.uint8)
        labels[0:4, 0:4] = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=16,
            width=16,
            count=1,
            dtype="uint8",
            nodata=255,
            crs="EPSG:4326",
            transform=from_origin(0.0, 16.0, 1.0, 1.0),
        ) as dataset:
            dataset.write(labels, 1)
        return path

    def test_publish_creates_immutable_auto_snapshot_and_revision_zero(self):
        project_id, resource = self._create_project_resource()
        label_path = self._write_label(project_id)

        published = publish_classification_result(
            project_id=project_id,
            fid=101,
            year=2024,
            inference_job_id="job-101",
            mine_resource_id=resource.id,
            model_id="cc-ln/CUGRS",
            label_path=label_path,
        )

        result = ClassificationResult.query.get(published["result_id"])
        self.assertEqual(result.vector_status, "ready")
        self.assertEqual(result.current_revision_no, 0)
        self.assertIsNone(result.vector_error)
        self.assertEqual(result.mine_resource_id, resource.id)
        self.assertEqual(result.mine_resource_version, 1)
        self.assertEqual(result.model_id, "cc-ln/CUGRS")
        self.assertEqual(json.loads(result.roi_geometry_json), self.ROI)
        self.assertTrue((Path(os.environ["PROJECT_STORAGE_ROOT"]) / result.label_path).is_file())
        self.assertTrue((Path(os.environ["PROJECT_STORAGE_ROOT"]) / result.auto_geojson_path).is_file())
        self.assertEqual(len(json.loads(result.auto_feature_collection_json)["features"]), 1)
        revision = ClassificationRevision.query.filter_by(result_id=result.id, revision_no=0).one()
        self.assertEqual(revision.source, "auto")
        self.assertEqual(revision.feature_count, 1)
        self.assertEqual(revision.snapshot_path, result.auto_geojson_path)
        audit = ClassificationEditAudit.query.filter_by(result_id=result.id, revision_no=0).one()
        self.assertEqual(audit.action, "baseline_created")
        self.assertIsNone(audit.base_revision_no)
        self.assertEqual(audit.request_source, "system_publish")

        again = publish_classification_result(
            project_id=project_id,
            fid=101,
            year=2024,
            inference_job_id="job-101",
            mine_resource_id=resource.id,
            model_id="cc-ln/CUGRS",
            label_path=label_path,
        )
        self.assertEqual(again["result_id"], result.id)
        self.assertEqual(ClassificationResult.query.count(), 1)

    def test_missing_label_becomes_vector_failed_without_revision_or_snapshot(self):
        project_id, resource = self._create_project_resource()
        missing_path = self.root / "missing-label.tif"

        published = publish_classification_result(
            project_id=project_id,
            fid=101,
            year=2024,
            inference_job_id="job-missing-label",
            mine_resource_id=resource.id,
            model_id="cc-ln/CUGRS",
            label_path=missing_path,
        )

        result = ClassificationResult.query.get(published["result_id"])
        self.assertEqual(result.vector_status, "vector_failed")
        self.assertEqual(result.vector_error, "label_missing")
        self.assertIsNone(result.label_path)
        self.assertIsNone(result.auto_geojson_path)
        self.assertIsNone(result.auto_feature_collection_json)
        self.assertIsNone(result.current_feature_collection_json)
        self.assertIsNone(result.current_revision_no)
        self.assertEqual(ClassificationRevision.query.filter_by(result_id=result.id).count(), 0)
        self.assertEqual(ClassificationEditAudit.query.filter_by(result_id=result.id).count(), 0)
        snapshot_dir = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / "projects"
            / str(project_id)
            / "outputs"
            / "classification-results"
            / str(result.id)
        )
        self.assertFalse(snapshot_dir.exists())

    def test_original_resource_snapshot_remains_usable_after_current_binding_changes(self):
        project_id, resource = self._create_project_resource()
        label_path = self._write_label(project_id)
        binding = ProjectMineBinding.query.filter_by(project_id=project_id, mine_fid=101).one()
        db.session.delete(binding)
        db.session.commit()

        published = publish_classification_result(
            project_id=project_id,
            fid=101,
            year=2024,
            inference_job_id="job-retained-resource",
            mine_resource_id=resource.id,
            model_id="cc-ln/CUGRS",
            label_path=label_path,
        )

        result = ClassificationResult.query.get(published["result_id"])
        self.assertEqual(result.vector_status, "ready")
        self.assertEqual(result.mine_resource_id, resource.id)
        self.assertEqual(result.mine_resource_version, resource.version)


if __name__ == "__main__":
    unittest.main()
