import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from sqlalchemy.orm import sessionmaker

from applications.configs.config import TestingConfig
from applications.extensions import db
from applications.inference import jobs
from applications.inference.app import create_worker_app
from applications.inference.worker import InferenceWorker
from applications.interface.mmseg_segmentation import run_inference_with_model
from applications.models.classification_result import ClassificationResult
from applications.models.inference_job import InferenceJob
from applications.models.project import Project, ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.inference_results import publish_project_inference_result
from test_inference_batch import FakeForward, _FakePlane, _write_tiles


class TestInferenceCancellation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="inference-cancellation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.storage = self.root / "storage"
        environment = patch.dict(os.environ, {"PROJECT_STORAGE_ROOT": str(self.storage)})
        environment.start()
        self.addCleanup(environment.stop)
        with patch.object(TestingConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{self.root / 'jobs.sqlite'}"):
            self.app = create_worker_app("testing")
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.addCleanup(db.engine.dispose)
        self.addCleanup(db.session.remove)
        self.session_factory = sessionmaker(bind=db.engine)

        existing = self._project("已有成功成果")
        existing_stage = self.root / "existing-stage"
        self._stage(existing_stage)
        publish_project_inference_result(existing.id, self._payload(existing.id, "existing-success"), {
            "written_fid_list": [101], "output_root": str(existing_stage),
        })
        self.existing_result_id = ClassificationResult.query.filter_by(inference_job_id="existing-success").one().id
        self.existing_path = self.storage / "projects" / str(existing.id) / "outputs/inference/101/101+2022_mask.png"
        self.existing_sha = hashlib.sha256(self.existing_path.read_bytes()).hexdigest()
        self.project = self._project("取消测试项目")
        self.publisher = Mock(wraps=publish_project_inference_result)

    def _project(self, name):
        project = Project(name=name, status="active")
        db.session.add(project)
        db.session.flush()
        db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=101))
        mine_path = f"projects/{project.id}/mines/1/mines.geojson"
        db.session.add(ProjectSpatialResource(
            project_id=project.id, resource_type="mine_vector", version=1, status="active",
            source_path=mine_path, normalized_path=mine_path, source_format="geojson",
        ))
        db.session.commit()
        path = self.storage / mine_path
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"FID_1": 101},
            "geometry": {"type": "Polygon", "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]]},
        }]}), encoding="utf-8")
        return project

    def _payload(self, project_id, job_id):
        return {
            "project_id": project_id, "year": "2022", "mine_fids": [101],
            "inference_job_id": job_id, "model_id": "cc-ln/CUGRS",
            "mine_resource_id": ProjectSpatialResource.query.filter_by(project_id=project_id).one().id,
            "old_tif_path": "old.tif", "new_tif_path": "new.tif", "kml_path": "roi.kml",
            "output_root": str(self.storage / "projects" / str(project_id) / "outputs/inference"),
        }

    def _stage(self, stage_root):
        destination = stage_root / "101"
        destination.mkdir(parents=True)
        image = np.full((4, 4, 3), 120, dtype=np.uint8)
        for suffix, data in [("", image), ("_src", image), ("_mask", np.zeros((4, 4), dtype=np.uint8))]:
            self.assertTrue(cv2.imwrite(str(destination / f"101+2022{suffix}.png"), data))
        with rasterio.open(destination / "101+2022_label.tif", "w", driver="GTiff", width=4, height=4,
                           count=1, dtype="uint8", nodata=255, crs="EPSG:4326",
                           transform=from_bounds(100.0, 25.0, 100.1, 25.1, 4, 4)) as label:
            label.write(np.zeros((4, 4), dtype=np.uint8), 1)

    def _job(self, job_id):
        job = InferenceJob(id=job_id, project_id=self.project.id, status="running", cancel_requested=False,
                           request_payload_json=json.dumps(self._payload(self.project.id, job_id)))
        db.session.add(job)
        db.session.commit()
        return job

    def _cancel_from_other_session(self, job_id):
        session = self.session_factory()
        try:
            other_job = session.query(InferenceJob).filter_by(id=job_id).one()
            with patch.object(jobs, "_database_dependencies", return_value=(SimpleNamespace(session=session), InferenceJob)):
                jobs.request_job_cancel(other_job)
        finally:
            session.close()

    def _worker(self, pipeline, inference_fn=None):
        worker = InferenceWorker(
            pipeline_runner=pipeline, inference_fn=inference_fn,
            project_result_publisher=self.publisher, job_store=jobs,
            runtime_root=self.root / "runtime", job_timeout_seconds=0,
        )
        worker.model = object()
        worker.resolution = SimpleNamespace(requested="auto", effective="cuda:0", fallback_reason=None, warnings=())
        return worker

    def _completed(self, kwargs):
        self._stage(kwargs["output_root"])
        return {"status": "completed", "written_fid_list": [101], "output_root": str(kwargs["output_root"])}

    def _assert_existing_unchanged(self):
        self.assertIsNotNone(ClassificationResult.query.get(self.existing_result_id))
        self.assertEqual(hashlib.sha256(self.existing_path.read_bytes()).hexdigest(), self.existing_sha)

    def _assert_not_published(self, job):
        self.assertEqual(job.status, "cancelled")
        self.assertTrue(job.cancel_requested)
        self.publisher.assert_not_called()
        self.assertEqual(ClassificationResult.query.filter_by(inference_job_id=job.id).count(), 0)
        self.assertFalse((self.storage / "projects" / str(self.project.id) / "outputs/inference/101/101+2022_mask.png").exists())
        self._assert_existing_unchanged()

    def test_fresh_cancel_before_publication_does_not_publish_staged_result(self):
        job = self._job("cancel-after-pipeline")

        def pipeline(**kwargs):
            summary = self._completed(kwargs)
            self.assertFalse(job.cancel_requested)
            self._cancel_from_other_session(job.id)
            self.assertFalse(job.cancel_requested, "Worker 实例应保持旧值，必须刷新才能看到外部取消")
            return summary

        self._worker(pipeline).run_job(job)
        self._assert_not_published(job)

    def test_single_final_forward_cancel_does_not_publish(self):
        job = self._job("cancel-final-forward")

        def copy_to_cpu(plane):
            self._cancel_from_other_session(job.id)
            return plane

        def inference(model, **kwargs):
            return run_inference_with_model(model, forward_fn=FakeForward(), **kwargs)

        def pipeline(**kwargs):
            names = _write_tiles(kwargs["work_dir"] / "tiles", 1)
            kwargs["tile_runner"](data_path=kwargs["work_dir"] / "tiles", out_dir=kwargs["work_dir"] / "predictions", file_names=names)
            return self._completed(kwargs)

        with patch.object(_FakePlane, "cpu", autospec=True, side_effect=copy_to_cpu):
            self._worker(pipeline, inference_fn=inference).run_job(job)
        self._assert_not_published(job)

    def test_cancel_after_cpu_fallback_pipeline_does_not_publish(self):
        job = self._job("cancel-cpu-fallback")
        calls = []

        def pipeline(**kwargs):
            calls.append(kwargs["device"])
            if len(calls) == 1:
                return {"status": "failed", "written_fid_list": [], "tile_errors": {"tile": "CUDA out of memory"}}
            summary = self._completed(kwargs)
            self._cancel_from_other_session(job.id)
            return summary

        worker = self._worker(pipeline)
        worker.model_loader = Mock(return_value=object())
        with patch.dict("sys.modules", {"torch": None}):
            worker.run_job(job)
        self.assertEqual(calls, ["cuda:0", "cpu"])
        self.assertEqual(job.fallback_reason, "CUDA_OUT_OF_MEMORY")
        self._assert_not_published(job)

    def test_normal_completed_pipeline_still_publishes(self):
        job = self._job("normal-publication")
        self._worker(lambda **kwargs: self._completed(kwargs)).run_job(job)
        self.assertEqual(job.status, "succeeded")
        self.publisher.assert_called_once()
        self.assertEqual(ClassificationResult.query.filter_by(inference_job_id=job.id).count(), 1)
        self._assert_existing_unchanged()

    def test_cancel_after_publication_keeps_success_and_formal_result(self):
        job = self._job("cancel-after-publication")

        def publish_then_cancel(*args):
            publication = publish_project_inference_result(*args)
            self._cancel_from_other_session(job.id)
            return publication

        self.publisher.side_effect = publish_then_cancel
        self._worker(lambda **kwargs: self._completed(kwargs)).run_job(job)
        self.assertEqual(job.status, "succeeded")
        self.assertTrue(job.cancel_requested)
        result_id = ClassificationResult.query.filter_by(inference_job_id=job.id).one().id
        result_file = self.storage / "projects" / str(self.project.id) / "outputs/inference/101/101+2022_mask.png"
        self.assertTrue(result_file.is_file())
        jobs.request_job_cancel(job)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(ClassificationResult.query.filter_by(inference_job_id=job.id).one().id, result_id)
        self._assert_existing_unchanged()


if __name__ == "__main__":
    unittest.main()
