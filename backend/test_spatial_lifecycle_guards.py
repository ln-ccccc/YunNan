# -*- coding: utf-8 -*-
"""归档项目空间写入入口的生命周期约束（2026-11-14 审查问题18）。

capability 声明 can_configure_spatial = not archived；服务层各写入口
必须与其一致：配置类写入（导入矿山/登记底图/重试/重新激活/上传候选）
统一拒绝归档项目，已在运行任务的取消是明确例外——归档仍允许取消，
否则归档后的运行任务无法停止。
"""
import hashlib
import os
import sys
import time
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.project_spatial import (
    ProjectSpatialJob,
    ProjectSpatialResource,
)

from test_project_api import TestProjectAPI


class TestSpatialLifecycleGuards(TestProjectAPI):
    def _archive_project(self, project_id):
        from applications.models.project import Project

        project = Project.query.get(project_id)
        project.status = "archived"
        db.session.commit()

    def _add_basemap(self, project_id, version, status):
        resource = ProjectSpatialResource(
            project_id=project_id,
            resource_type="basemap",
            version=version,
            status=status,
            source_path=f"projects/{project_id}/basemaps/{version}",
            normalized_path=f"projects/{project_id}/basemaps/{version}",
            source_format="tif",
            min_zoom=8,
            max_zoom=15,
        )
        db.session.add(resource)
        db.session.flush()
        # main 现行逻辑要求 retained 底图自身仍有 .active 标记才可重激活
        tile_dir = (
            self.storage_root / "projects" / str(project_id) / "tiles" / str(resource.id)
        )
        tile_dir.mkdir(parents=True, exist_ok=True)
        (tile_dir / ".active").write_text("1", encoding="utf-8")
        db.session.commit()
        return resource

    def _seed(self, name="归档约束项目"):
        self.login_as_admin()
        project_id = self._create_project(name)
        return project_id

    def test_archived_project_reactivate_basemap_rejected(self):
        project_id = self._seed()
        retained = self._add_basemap(project_id, version=1, status="retained")
        self._add_basemap(project_id, version=2, status="active")
        self._archive_project(project_id)

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{retained.id}/reactivate",
            json={},
        )
        body = self._json(response)
        self.assertFalse(body["success"], body)
        self.assertIn("归档项目不允许修改空间资源", body["msg"])
        # 状态未变：仍为 retained
        db.session.expire_all()
        self.assertEqual(
            ProjectSpatialResource.query.get(retained.id).status, "retained"
        )

    def test_archived_project_basemap_upload_rejected(self):
        # 单发上传与分片 init/complete 都是"为归档项目配置空间资源"
        project_id = self._seed()
        self._archive_project(project_id)

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload?filename=archived.tif",
            data=b"II*\x00",
            headers={"Content-Type": "application/octet-stream"},
        )
        body = self._json(response)
        self.assertFalse(body["success"], body)
        self.assertIn("归档项目不允许修改空间资源", body["msg"])
        self.assertFalse((self.storage_root / "incoming" / "archived.tif").exists())

    def test_archived_project_chunked_init_and_complete_rejected(self):
        project_id = self._seed()
        payload_bytes = b"z" * (1024 * 1024)
        upload_key = hashlib.sha256(
            f"lifecycle:{time.time()}".encode()
        ).hexdigest()

        # 先在未归档时正常 init + 上传分块（会话是客户端断点资产，归档前建立）
        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/init",
            json={
                "upload_key": upload_key,
                "filename": "lifecycle.tif",
                "total_size": len(payload_bytes),
                "mime": "application/octet-stream",
                "chunk_size": 1024 * 1024,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        session_id = self._json(response)["data"]["session_id"]

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/chunk/{session_id}/0",
            data=payload_bytes,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Chunk-Sha256": hashlib.sha256(payload_bytes).hexdigest(),
            },
        )
        self.assertEqual(response.status_code, 200)

        # 归档后：init（断点续传重开）与 complete 都被拒
        self._archive_project(project_id)

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/init",
            json={
                "upload_key": upload_key,
                "filename": "lifecycle.tif",
                "total_size": len(payload_bytes),
                "mime": "application/octet-stream",
                "chunk_size": 1024 * 1024,
            },
        )
        body = self._json(response)
        self.assertFalse(body["success"], body)
        self.assertIn("归档项目不允许修改空间资源", body["msg"])

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/complete/{session_id}",
            json={"filename": "lifecycle.tif"},
        )
        body = self._json(response)
        self.assertFalse(body["success"], body)
        self.assertIn("归档项目不允许修改空间资源", body["msg"])
        self.assertFalse((self.storage_root / "incoming" / "lifecycle.tif").exists())

    def test_archived_project_cancel_running_job_still_allowed(self):
        # 问题18 明确例外：取消已运行/排队任务不属于"配置空间资源"，
        # 归档项目仍须能停掉任务
        project_id = self._seed()
        resource = self._add_basemap(project_id, version=1, status="processing")
        job = ProjectSpatialJob(
            id="job-archived-cancel",
            project_id=project_id,
            resource_id=resource.id,
            job_type="basemap_slice",
            status="queued",
        )
        db.session.add(job)
        db.session.commit()
        self._archive_project(project_id)

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/jobs/{job.id}/cancel", json={}
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        db.session.expire_all()
        self.assertEqual(ProjectSpatialJob.query.get(job.id).status, "cancelled")

    def test_archived_project_configure_writes_match_capability(self):
        # 既有守卫回归（防退化）：导入矿山矢量/登记底图/直挂登记/重试与
        # can_configure_spatial 一致，归档后统一拒绝
        project_id = self._seed()
        self._archive_project(project_id)

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/mines",
            json={"filename": "arch.kml", "content": "<kml></kml>"},
        )
        self.assertIn("归档项目不允许修改空间资源", self._json(response)["msg"])

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps",
            json={"candidate": "incoming/none.tif"},
        )
        self.assertIn("归档项目不允许修改空间资源", self._json(response)["msg"])


if __name__ == "__main__":
    unittest.main()
