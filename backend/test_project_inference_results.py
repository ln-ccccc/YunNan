import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult, ClassificationRevision
from applications.models.project import ProjectDataset, ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.service import create_project


class TestProjectInferenceResults(unittest.TestCase):
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

    def _create_project(self, name="Dali", fid=101):
        created = create_project({"name": name, "region": name})
        db.session.add(
            ProjectMineBinding(
                project_id=created["id"],
                mine_fid=fid,
                sort_order=0,
            )
        )
        db.session.commit()
        return created["id"]

    def _stage_year(self, fid, year, mask_values=None):
        stage_root = self.root / f"stage-{fid}-{year}"
        shutil.rmtree(stage_root, ignore_errors=True)
        fid_dir = stage_root / str(fid)
        fid_dir.mkdir(parents=True)
        image = np.full((4, 4, 3), 120, dtype=np.uint8)
        mask = np.asarray(
            mask_values if mask_values is not None else [[0, 0, 1, 1]] * 4,
            dtype=np.uint8,
        )
        self.assertTrue(cv2.imwrite(str(fid_dir / f"{fid}+{year}.png"), image))
        self.assertTrue(cv2.imwrite(str(fid_dir / f"{fid}+{year}_src.png"), image))
        self.assertTrue(cv2.imwrite(str(fid_dir / f"{fid}+{year}_mask.png"), mask))
        return stage_root

    def _add_mine_resource(self, project_id, fid=101):
        relative_path = Path("projects") / str(project_id) / "resources" / "mine-vector-1" / "mines.geojson"
        resource = ProjectSpatialResource(
            project_id=project_id,
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
                            "properties": {"FID_1": fid},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [[0.0, 0.0], [16.0, 0.0], [16.0, 16.0], [0.0, 16.0], [0.0, 0.0]]
                                ],
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return resource

    def _write_label(self, stage_root, fid, year):
        labels = np.full((16, 16), 255, dtype=np.uint8)
        labels[0:4, 0:4] = 0
        path = stage_root / str(fid) / f"{fid}+{year}_label.tif"
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

    def _publish(self, project_id, fid, year, mask_values=None):
        from applications.project_hub.inference_results import publish_project_inference_result

        stage_root = self._stage_year(fid, year, mask_values=mask_values)
        return publish_project_inference_result(
            project_id,
            {
                "year": str(year),
                "mine_fids": [fid],
                "output_root": str(
                    Path(os.environ["PROJECT_STORAGE_ROOT"])
                    / f"projects/{project_id}/outputs/inference"
                ),
            },
            {
                "written_fid_list": [str(fid)],
                "output_root": str(stage_root),
            },
        )

    def test_single_year_publishes_classification_without_fake_matrix(self):
        from applications.project_hub.inference_results import publish_project_inference_result

        project_id = self._create_project()
        stage_root = self._stage_year(101, 2022)

        result = publish_project_inference_result(
            project_id,
            {
                "year": "2022",
                "mine_fids": [101],
                "output_root": str(
                    Path(os.environ["PROJECT_STORAGE_ROOT"])
                    / f"projects/{project_id}/outputs/inference"
                ),
            },
            {
                "written_fid_list": ["101"],
                "output_root": str(stage_root),
            },
        )

        summary_path = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / f"projects/{project_id}/outputs/change_matrix/101.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertFalse(summary["has_change_matrix"])
        self.assertIsNone(summary["old_year"])
        self.assertEqual(summary["new_year"], 2022)
        self.assertIsNone(summary["images"]["old"])
        self.assertTrue(summary["images"]["new"].endswith("/101/101+2022.png"))
        self.assertEqual(result["synced_fids"], [101])
        self.assertEqual(result["display_results"][0]["fid"], 101)
        self.assertEqual(ProjectDataset.query.count(), 1)

    def test_latest_two_years_drive_matrix_even_after_older_backfill(self):
        project_id = self._create_project()
        self._publish(project_id, 101, 2018, [[0, 0, 1, 1]] * 4)
        self._publish(project_id, 101, 2020, [[0, 1, 1, 1]] * 4)
        self._publish(project_id, 101, 2022, [[1, 1, 1, 0]] * 4)

        summary_path = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / f"projects/{project_id}/outputs/change_matrix/101.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertTrue(summary["has_change_matrix"])
        self.assertEqual((summary["old_year"], summary["new_year"]), (2020, 2022))
        self.assertEqual(len(summary["headers"]), 6)
        self.assertEqual(len(summary["matrix"]), 6)

        self._publish(project_id, 101, 2017, [[0, 0, 0, 0]] * 4)

        backfilled = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual((backfilled["old_year"], backfilled["new_year"]), (2020, 2022))

    def test_same_year_rerun_upserts_dataset(self):
        project_id = self._create_project()
        self._publish(project_id, 101, 2022)
        self._publish(project_id, 101, 2022, [[2, 2, 2, 2]] * 4)

        rows = ProjectDataset.query.filter_by(
            project_id=project_id,
            dataset_kind="inference_result",
            mine_fid=101,
            year_start=2022,
            year_end=2022,
        ).all()
        self.assertEqual(len(rows), 1)

    def test_project_publication_creates_vector_baseline_without_changing_png_success(self):
        from applications.project_hub.inference_results import publish_project_inference_result

        project_id = self._create_project()
        resource = self._add_mine_resource(project_id)
        stage_root = self._stage_year(101, 2024)
        self._write_label(stage_root, 101, 2024)

        result = publish_project_inference_result(
            project_id,
            {
                "year": "2024",
                "mine_fids": [101],
                "mine_resource_id": resource.id,
                "inference_job_id": "job-vector-success",
                "output_root": str(
                    Path(os.environ["PROJECT_STORAGE_ROOT"])
                    / f"projects/{project_id}/outputs/inference"
                ),
            },
            {"written_fid_list": ["101"], "output_root": str(stage_root)},
        )

        self.assertEqual(result["synced_fids"], [101])
        self.assertEqual(result["classification_results"][0]["vector_status"], "ready")
        vector = ClassificationResult.query.one()
        self.assertEqual(result["display_results"][0]["result_id"], vector.id)
        self.assertEqual(result["display_results"][0]["vector_status"], "ready")
        dataset = ProjectDataset.query.filter_by(
            project_id=project_id,
            dataset_kind="inference_result",
            mine_fid=101,
            year_start=2024,
            year_end=2024,
        ).one()
        self.assertEqual(
            json.loads(dataset.slice_config_json)["classification_result_id"],
            vector.id,
        )
        self.assertEqual(vector.current_revision_no, 0)
        self.assertEqual(ClassificationRevision.query.filter_by(result_id=vector.id).count(), 1)
        published_png = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / f"projects/{project_id}/outputs/inference/101/101+2024.png"
        )
        self.assertTrue(published_png.is_file())

    def test_project_publication_keeps_png_when_label_is_missing(self):
        from applications.project_hub.inference_results import publish_project_inference_result

        project_id = self._create_project()
        resource = self._add_mine_resource(project_id)
        stage_root = self._stage_year(101, 2024)

        result = publish_project_inference_result(
            project_id,
            {
                "year": "2024",
                "mine_fids": [101],
                "mine_resource_id": resource.id,
                "inference_job_id": "job-vector-missing-label",
                "output_root": str(
                    Path(os.environ["PROJECT_STORAGE_ROOT"])
                    / f"projects/{project_id}/outputs/inference"
                ),
            },
            {"written_fid_list": ["101"], "output_root": str(stage_root)},
        )

        self.assertEqual(result["synced_fids"], [101])
        self.assertEqual(result["classification_results"][0]["vector_status"], "vector_failed")
        self.assertEqual(ClassificationResult.query.one().vector_error, "label_missing")
        published_png = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / f"projects/{project_id}/outputs/inference/101/101+2024.png"
        )
        self.assertTrue(published_png.is_file())

    def test_project_publication_rolls_back_vector_failure_without_losing_png_result(self):
        from applications.project_hub.inference_results import publish_project_inference_result

        project_id = self._create_project()
        resource = self._add_mine_resource(project_id)
        stage_root = self._stage_year(101, 2024)
        with patch(
            "applications.project_hub.classification_results.publish_classification_result",
            side_effect=RuntimeError("simulated vector database failure"),
        ), patch.object(db.session, "rollback", wraps=db.session.rollback) as rollback:
            result = publish_project_inference_result(
                project_id,
                {
                    "year": "2024",
                    "mine_fids": [101],
                    "mine_resource_id": resource.id,
                    "inference_job_id": "job-vector-db-failure",
                    "output_root": str(
                        Path(os.environ["PROJECT_STORAGE_ROOT"])
                        / f"projects/{project_id}/outputs/inference"
                    ),
                },
                {"written_fid_list": ["101"], "output_root": str(stage_root)},
            )

        self.assertEqual(result["synced_fids"], [101])
        self.assertEqual(result["classification_results"], [])
        self.assertEqual(rollback.call_count, 1)
        published_png = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / f"projects/{project_id}/outputs/inference/101/101+2024.png"
        )
        self.assertTrue(published_png.is_file())

    def test_rejects_cross_project_fids_without_writing_other_project(self):
        project_a = self._create_project(name="Dali", fid=101)
        project_b = self._create_project(name="Kunming", fid=202)
        self._publish(project_a, 101, 2022)

        with self.assertRaisesRegex(ValueError, "不属于当前项目"):
            self._publish(project_b, 101, 2022)

        other_output = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / f"projects/{project_b}/outputs/inference/101"
        )
        self.assertFalse(other_output.exists())

    def test_project_index_points_are_upserted_by_fid_type_and_year(self):
        from applications.project_hub.inference_results import upsert_project_index_results

        project_id = self._create_project()
        upsert_project_index_results(
            project_id,
            "NDVI",
            2022,
            [{"fid": 101, "mean": 0.2}],
        )
        upsert_project_index_results(
            project_id,
            "NDVI",
            2024,
            [{"fid": 101, "mean": 0.4}],
        )
        upsert_project_index_results(
            project_id,
            "NDVI",
            2022,
            [{"fid": 101, "mean": 0.3}],
        )

        path = (
            Path(os.environ["PROJECT_STORAGE_ROOT"])
            / f"projects/{project_id}/outputs/indices/101.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["ndvi"]["data"], [
            {"year": 2022, "value": 0.3},
            {"year": 2024, "value": 0.4},
        ])
        self.assertAlmostEqual(payload["ndvi"]["mean"], 0.35)
        self.assertGreater(payload["ndvi"]["trend"], 0)

        with self.assertRaisesRegex(ValueError, "不属于当前项目"):
            upsert_project_index_results(
                project_id,
                "NDVI",
                2024,
                [{"fid": 999, "mean": 0.5}],
            )


if __name__ == "__main__":
    unittest.main()
