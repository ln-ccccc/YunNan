"""底图重激活：门槛、回滚顺序，以及"半成品为什么会出现在历史底图列表里"的根因。

2026-11-24 审计 C1 实证的两条路径：
- 正常取消一张底图的切片：只切出一张 z8 瓦片的任务被标成 retained，随后重激活返回
  200，换掉原本可用的整幅底图，并对外宣称可放大到 z15；
- 注入数据库提交失败：旧底图在库里仍是激活态，它的 .active 标记却已被删除。

修法分两层：
- 根因——切片被取消的资源不是"切换下来的历史底图"，落 failed（spatial_worker）；
- 防线——重激活必须同时满足：状态是 retained、最近一次切片任务成功、声明的每个缩放
  级别都有瓦片；标记切换顺序与切片 worker 一致（写新 → commit → 成功后才删旧）。
"""

import json
from unittest.mock import patch

from applications.extensions import db
from applications.models.project import Project, ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource
from applications.project_hub import spatial_worker
from applications.project_hub.spatial_worker import CANCELLED_TILING_MESSAGE

from test_project_api import TestProjectAPI

ALL_LEVELS = range(8, 16)


class TestBasemapReactivationGate(TestProjectAPI):
    def _add_basemap(
        self, project_id, version, status, levels=ALL_LEVELS, job_status="succeeded", with_job=True
    ):
        """造数对齐真实状态机：retained 底图没有 .active 标记（被替换激活时已删），
        瓦片仍在；只有当前激活者持有标记。levels 决定哪些缩放级别有瓦片。"""
        resource = ProjectSpatialResource(
            project_id=project_id,
            resource_type="basemap",
            version=version,
            status=status,
            source_path=f"projects/{project_id}/basemaps/{version}",
            normalized_path=f"projects/{project_id}/basemaps/{version}",
            tile_path=f"projects/{project_id}/tiles/pending",
            source_format="tif",
            min_zoom=min(ALL_LEVELS),
            max_zoom=max(ALL_LEVELS),
        )
        db.session.add(resource)
        db.session.flush()
        resource.tile_path = f"projects/{project_id}/tiles/{resource.id}"
        if with_job:
            db.session.add(ProjectSpatialJob(
                id=f"job-basemap-{resource.id}",
                project_id=project_id,
                resource_id=resource.id,
                job_type="basemap_tiling",
                status=job_status,
                stage="completed" if job_status == "succeeded" else job_status,
                progress=100.0 if job_status == "succeeded" else 12.0,
            ))
        tile_dir = (
            self.storage_root / "projects" / str(project_id) / "tiles" / str(resource.id)
        )
        for level in levels:
            level_dir = tile_dir / str(level) / "207"
            level_dir.mkdir(parents=True, exist_ok=True)
            (level_dir / "107.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        if status == "active":
            tile_dir.mkdir(parents=True, exist_ok=True)
            (tile_dir / ".active").write_text(str(resource.id), encoding="ascii")
        db.session.commit()
        return resource

    def _seed_project(self, name="底图重激活项目", status=None):
        self.login_as_admin()
        project_id = self._create_project(name)
        db.session.add(ProjectMineBinding(project_id=project_id, mine_fid=501, sort_order=0))
        if status is not None:
            Project.query.filter_by(id=project_id).one().status = status
        db.session.commit()
        return project_id

    def _marker(self, project_id, resource_id):
        return self.storage_root / "projects" / str(project_id) / "tiles" / str(resource_id) / ".active"

    def _reactivate(self, project_id, resource_id):
        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{resource_id}/reactivate", json={}
        )
        return response, self._json(response)

    # ------------------------------------------------------------ 门槛

    def test_incomplete_tile_levels_cannot_replace_a_working_basemap(self):
        """审计复现：只切出 z8 一层瓦片的半成品，重激活不得换掉正在用的整幅底图。"""
        project_id = self._seed_project()
        partial = self._add_basemap(project_id, version=1, status="retained", levels=[8])
        current = self._add_basemap(project_id, version=2, status="active")

        response, body = self._reactivate(project_id, partial.id)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], 1)
        self.assertIn("无法重新激活", body["msg"])
        self.assertIn("9–15", body["msg"])
        db.session.expire_all()
        self.assertEqual(ProjectSpatialResource.query.get(partial.id).status, "retained")
        self.assertEqual(ProjectSpatialResource.query.get(current.id).status, "active")
        self.assertFalse(self._marker(project_id, partial.id).exists())
        self.assertTrue(self._marker(project_id, current.id).is_file())

    def test_tiling_job_that_did_not_succeed_blocks_reactivation(self):
        project_id = self._seed_project()
        cancelled = self._add_basemap(
            project_id, version=1, status="retained", job_status="cancelled"
        )
        self._add_basemap(project_id, version=2, status="active")

        response, body = self._reactivate(project_id, cancelled.id)

        self.assertEqual(response.status_code, 400)
        self.assertIn("未成功结束", body["msg"])
        self.assertIn("cancelled", body["msg"])

    def test_only_retained_status_is_reactivatable(self):
        project_id = self._seed_project()
        self._add_basemap(project_id, version=4, status="active")
        for offset, status in enumerate(("failed", "processing", "pending")):
            resource = self._add_basemap(
                project_id, version=1 + offset, status=status, with_job=False
            )
            response, body = self._reactivate(project_id, resource.id)
            self.assertEqual(response.status_code, 400, status)
            self.assertIn("不是可重新激活的历史底图", body["msg"], status)
            self.assertIn(status, body["msg"])

    def test_archived_project_cannot_change_basemap(self):
        project_id = self._seed_project(status="archived")
        old = self._add_basemap(project_id, version=1, status="retained")
        self._add_basemap(project_id, version=2, status="active")

        response, body = self._reactivate(project_id, old.id)

        self.assertEqual(response.status_code, 400)
        self.assertIn("归档", body["msg"])

    def test_retained_list_says_what_can_be_reactivated(self):
        project_id = self._seed_project()
        usable = self._add_basemap(project_id, version=1, status="retained")
        partial = self._add_basemap(project_id, version=2, status="retained", levels=[8, 9])
        cancelled = self._add_basemap(
            project_id, version=3, status="retained", job_status="failed"
        )
        self._add_basemap(project_id, version=4, status="active")

        response = self.client.get(f"/api/projects/{project_id}/spatial/basemaps/retained")
        self.assertEqual(response.status_code, 200)
        items = {item["id"]: item for item in self._json(response)["data"]["items"]}

        self.assertEqual(set(items), {usable.id, partial.id, cancelled.id})
        self.assertTrue(items[usable.id]["reactivable"])
        self.assertIsNone(items[usable.id]["reactivate_blocked_reason"])
        self.assertFalse(items[partial.id]["reactivable"])
        self.assertIn("10–15", items[partial.id]["reactivate_blocked_reason"])
        self.assertFalse(items[cancelled.id]["reactivable"])
        self.assertIn("failed", items[cancelled.id]["reactivate_blocked_reason"])

    # ------------------------------------------------------------ 切换与回滚顺序

    def test_full_levels_switches_active_and_markers_in_both_directions(self):
        project_id = self._seed_project()
        first = self._add_basemap(project_id, version=1, status="retained")
        second = self._add_basemap(project_id, version=2, status="active")

        response, body = self._reactivate(project_id, first.id)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(body["data"]["status"], "active")
        db.session.expire_all()
        self.assertEqual(ProjectSpatialResource.query.get(first.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(second.id).status, "retained")
        self.assertTrue(self._marker(project_id, first.id).is_file())
        self.assertFalse(self._marker(project_id, second.id).exists())

        # 再切回去：被切换的一方天然无标记，仍可重新激活
        response, _body = self._reactivate(project_id, second.id)
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        self.assertEqual(ProjectSpatialResource.query.get(second.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(first.id).status, "retained")
        self.assertTrue(self._marker(project_id, second.id).is_file())
        self.assertFalse(self._marker(project_id, first.id).exists())

    def test_commit_failure_keeps_previous_active_marker_and_status(self):
        """审计复现：提交失败后旧底图仍是激活态，它的标记不得已被删除。"""
        project_id = self._seed_project()
        old = self._add_basemap(project_id, version=1, status="retained")
        current = self._add_basemap(project_id, version=2, status="active")

        with patch(
            "applications.project_hub.spatial_service.db.session.commit",
            side_effect=RuntimeError("commit failed"),
        ):
            response, _body = self._reactivate(project_id, old.id)
        self.assertEqual(response.status_code, 500)

        db.session.rollback()
        db.session.expire_all()
        self.assertEqual(ProjectSpatialResource.query.get(current.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(old.id).status, "retained")
        # 文件标记与库状态一致：新标记已回滚删除，旧（当前激活者）标记保留
        self.assertFalse(self._marker(project_id, old.id).exists())
        self.assertTrue(self._marker(project_id, current.id).is_file())

    def test_tile_dir_without_any_png_is_rejected(self):
        project_id = self._seed_project()
        empty = self._add_basemap(project_id, version=1, status="retained", levels=[])
        self._add_basemap(project_id, version=2, status="active")

        response, body = self._reactivate(project_id, empty.id)

        self.assertEqual(response.status_code, 400)
        self.assertIn("无法重新激活", body["msg"])

    # ------------------------------------------------------------ 根因：取消不再落 retained

    def _tiling_resource(self, project_id, job_status="running", cancel_requested=False):
        source = self.storage_root / "projects" / str(project_id) / "basemaps" / "source.tif"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"II*\x00")
        resource = ProjectSpatialResource(
            project_id=project_id, resource_type="basemap", version=9, status="processing",
            source_path=f"projects/{project_id}/basemaps/source.tif",
            source_format="tif", bounds_json="[98.8, 24.6, 101.1, 26.8]", min_zoom=8, max_zoom=15,
        )
        db.session.add(resource)
        db.session.flush()
        resource.tile_path = f"projects/{project_id}/tiles/{resource.id}"
        job = ProjectSpatialJob(
            id=f"job-cancel-{resource.id}", project_id=project_id, resource_id=resource.id,
            job_type="basemap_tiling", status=job_status, stage="tiling",
            cancel_requested=cancel_requested,
        )
        db.session.add(job)
        db.session.commit()
        return resource, job

    def test_cancelled_tiling_lands_in_failed_not_in_the_reactivatable_list(self):
        """根因修复：取消的切片不是"切换下来的历史底图"，不得出现在 retained 列表。"""
        project_id = self._seed_project()
        resource, job = self._tiling_resource(project_id)

        def cancel_during_tiling(*_args, **_kwargs):
            (self.storage_root / resource.tile_path / "8" / "207").mkdir(parents=True, exist_ok=True)
            (self.storage_root / resource.tile_path / "8" / "207" / "107.png").write_bytes(b"png")
            raise InterruptedError("任务已取消")

        with patch.object(spatial_worker, "_run_gdal2tiles", side_effect=cancel_during_tiling):
            spatial_worker.process_job(job)

        db.session.expire_all()
        stored = ProjectSpatialResource.query.get(resource.id)
        self.assertEqual(stored.status, "failed")
        self.assertEqual(stored.error_message, CANCELLED_TILING_MESSAGE)
        self.assertEqual(ProjectSpatialJob.query.get(job.id).status, "cancelled")

        response = self.client.get(f"/api/projects/{project_id}/spatial/basemaps/retained")
        self.assertEqual([item["id"] for item in self._json(response)["data"]["items"]], [])

    def test_cancelled_job_recovered_at_restart_also_lands_in_failed(self):
        project_id = self._seed_project()
        resource, job = self._tiling_resource(project_id, cancel_requested=True)

        self.assertEqual(spatial_worker.recover_running_jobs(), 1)

        db.session.expire_all()
        self.assertEqual(ProjectSpatialJob.query.get(job.id).status, "cancelled")
        stored = ProjectSpatialResource.query.get(resource.id)
        self.assertEqual(stored.status, "failed")
        self.assertEqual(stored.error_message, CANCELLED_TILING_MESSAGE)
