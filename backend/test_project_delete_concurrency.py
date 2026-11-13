# -*- coding: utf-8 -*-
"""问题5（35项审查）：项目删除与任务创建的并发窗口回归。

旧实现一次 `_get_project_or_404` 读到 archived 后直接移动目录+软删——
"检查任务"与"删除提交"之间没有共享事务约束，归档后到达的推理/空间任务
会挂到目录已被移入 trash 的项目上。本文件契约：

1. 归档项目存在 queued/running 推理任务（InferenceJob）→ 删除拒绝 409；
2. 归档项目存在 queued/running 空间任务（ProjectSpatialJob）→ 删除拒绝 409；
3. 接缝注入竞争：删除流程的活动任务检查执行期间新任务落库 → 删除必须拒绝
   且项目目录不动（模拟"检查-删除同时发生"的 TOCTOU）；
4. 归档项目允许取消空间任务（先取消清理，再删项目）；
5. 已删除项目不能再创建推理/空间任务。
"""
import os
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.inference_job import InferenceJob
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource
from applications.project_hub import service as project_service
from test_project_api import TestProjectAPI


class DeleteConcurrencyBase(TestProjectAPI):
    def _make_archived_project(self, name="并发删除回归项目"):
        self.login_as_admin()
        project_id = self._create_project(name)
        project_root = self.storage_root / "projects" / str(project_id)
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / "marker.txt").write_text("data")
        response = self.client.post(f"/api/projects/{project_id}/archive")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return project_id, project_root

    def _running_inference_job(self, project_id, job_id=None):
        job = InferenceJob(
            id=job_id or f"job-{uuid4().hex[:12]}",
            project_id=project_id,
            status="running",
            requested_device="auto",
            request_payload_json='{"project_id": %d}' % project_id,
        )
        db.session.add(job)
        db.session.commit()
        return job

    def _queued_spatial_job(self, project_id):
        resource = ProjectSpatialResource(
            project_id=project_id,
            resource_type="basemap",
            version=1,
            status="pending",
            source_path="incoming/basemap.tif",
            source_format="tif",
            bounds_json="{}",
        )
        db.session.add(resource)
        db.session.flush()
        job = ProjectSpatialJob(
            id=str(uuid4()),
            project_id=project_id,
            resource_id=resource.id,
            job_type="basemap_tiles",
            status="queued",
            stage="queued",
            progress=0.0,
        )
        db.session.add(job)
        db.session.commit()
        return job


class TestDeleteBlockedByActiveJobs(DeleteConcurrencyBase):
    def test_archived_project_with_running_inference_job_rejects_delete(self):
        project_id, project_root = self._make_archived_project()
        self._running_inference_job(project_id)

        response = self.client.delete(f"/api/projects/{project_id}")

        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("任务", response.get_json()["msg"])
        self.assertTrue(project_root.exists(), "目录不应被移入 trash")
        self.assertFalse(list((self.storage_root / "trash").glob(f"{project_id}_*")))
        self.assertIsNone(db.session.get(Project, project_id).deleted_at)

    def test_archived_project_with_queued_spatial_job_rejects_delete(self):
        project_id, project_root = self._make_archived_project()
        self._queued_spatial_job(project_id)

        response = self.client.delete(f"/api/projects/{project_id}")

        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("任务", response.get_json()["msg"])
        self.assertTrue(project_root.exists())
        self.assertIsNone(db.session.get(Project, project_id).deleted_at)

    def test_archived_project_with_finished_jobs_still_deletes(self):
        project_id, project_root = self._make_archived_project()
        resource = ProjectSpatialResource(
            project_id=project_id,
            resource_type="basemap",
            version=1,
            status="retained",
            source_path="incoming/basemap.tif",
            source_format="tif",
            bounds_json="{}",
        )
        db.session.add(resource)
        db.session.flush()
        job = InferenceJob(
            id=f"job-done-{uuid4().hex[:8]}",
            project_id=project_id,
            status="succeeded",
            requested_device="auto",
            request_payload_json="{}",
        )
        spatial = ProjectSpatialJob(
            id=str(uuid4()),
            project_id=project_id,
            resource_id=resource.id,
            job_type="basemap_tiles",
            status="cancelled",
            stage="cancelled",
            progress=0.0,
        )
        db.session.add_all([job, spatial])
        db.session.commit()

        response = self.client.delete(f"/api/projects/{project_id}")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertFalse(project_root.exists())


class TestDeleteCreateRaceSeam(DeleteConcurrencyBase):
    """接缝注入：把"活动任务出现"塞进删除流程的检查与提交之间，
    断言共享事务约束让删除侧看到它（否则即 TOCTOU 窗口实证）。"""

    def test_job_created_during_delete_check_blocks_delete(self):
        project_id, project_root = self._make_archived_project()
        original = project_service._project_has_active_jobs

        def race(checked_project_id):
            # 模拟另一请求在删除检查的同时为该项目落库一个 running 任务
            self._running_inference_job(checked_project_id)
            return original(checked_project_id)

        with patch.object(project_service, "_project_has_active_jobs", side_effect=race):
            response = self.client.delete(f"/api/projects/{project_id}")

        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("任务", response.get_json()["msg"])
        self.assertTrue(project_root.exists(), "竞争窗口内目录不得移动")
        self.assertIsNone(db.session.get(Project, project_id).deleted_at)
        trash_hits = list((self.storage_root / "trash").glob(f"{project_id}_*"))
        self.assertFalse(trash_hits)

    def test_job_created_before_delete_check_visible_via_seam(self):
        """接缝在检查之前已落库（普通顺序场景的服务层视角），仍须拒绝。"""
        project_id, _ = self._make_archived_project()
        self._running_inference_job(project_id)

        outcome = project_service._project_has_active_jobs(project_id)

        self.assertTrue(outcome)


class TestArchivedCancelThenDelete(DeleteConcurrencyBase):
    def test_archived_project_can_cancel_spatial_job_then_delete(self):
        project_id, project_root = self._make_archived_project()
        job = self._queued_spatial_job(project_id)

        cancel = self.client.post(f"/api/projects/{project_id}/spatial/jobs/{job.id}/cancel")
        self.assertEqual(cancel.status_code, 200, cancel.get_data(as_text=True))
        cancel_body = cancel.get_json()
        self.assertEqual(cancel_body["code"], 0, cancel.get_data(as_text=True))
        self.assertEqual(cancel_body["data"]["status"], "cancelled")

        delete = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(delete.status_code, 200, delete.get_data(as_text=True))
        self.assertFalse(project_root.exists())

    def test_deleted_project_rejects_new_jobs(self):
        project_id, _ = self._make_archived_project()
        delete = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(delete.status_code, 200)

        inference = self.client.post(
            "/api/inference/jobs",
            json={"project_id": project_id, "dataset_id": 1, "year": "2024"},
        )
        self.assertEqual(inference.status_code, 400, inference.get_data(as_text=True))

        from applications.project_hub.spatial_service import register_basemap
        try:
            register_basemap(project_id, "incoming/basemap.tif")
        except ValueError as exc:
            self.assertIn("项目不存在", str(exc))
        else:
            self.fail("已删除项目不应允许创建空间任务")


if __name__ == "__main__":
    unittest.main()
