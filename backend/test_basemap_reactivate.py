"""底图重新激活测试：retained 列表、互斥激活、.active 标记切换、缺瓦片拒绝。"""

import json

from applications.extensions import db
from applications.models.project import ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource

from test_project_api import TestProjectAPI


class TestBasemapReactivate(TestProjectAPI):
    def _add_basemap(self, project_id, version, status, with_tiles=True):
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
        if with_tiles:
            # 真实状态（问题11 fixture 勘误）：retained 历史底图的 .active 标记
            # 在被替换激活时已删除，瓦片文件仍在；只有当前激活者持有标记
            tile_dir = (
                self.storage_root / "projects" / str(project_id) / "tiles" / str(resource.id)
            )
            (tile_dir / "9" / "500").mkdir(parents=True, exist_ok=True)
            (tile_dir / "9" / "500" / "300.png").write_bytes(b"png")
            if status == "active":
                (tile_dir / ".active").write_text("1", encoding="utf-8")
        db.session.commit()
        return resource

    def _seed_project_with_mine(self, name="底图重激活项目"):
        self.login_as_admin()
        project_id = self._create_project(name)
        db.session.add(ProjectMineBinding(project_id=project_id, mine_fid=501, sort_order=0))
        db.session.commit()
        return project_id

    def test_list_retained_basemaps(self):
        project_id = self._seed_project_with_mine()
        self._add_basemap(project_id, version=1, status="retained", with_tiles=False)
        self._add_basemap(project_id, version=2, status="active")

        response = self.client.get(f"/api/projects/{project_id}/spatial/basemaps/retained")
        self.assertEqual(response.status_code, 200)
        items = self._json(response)["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["version"], 1)
        self.assertEqual(items[0]["status"], "retained")

    def test_reactivate_switches_active_and_markers(self):
        project_id = self._seed_project_with_mine()
        old = self._add_basemap(project_id, version=1, status="retained")
        current = self._add_basemap(project_id, version=2, status="active")

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{old.id}/reactivate", json={}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["data"]["status"], "active")
        db.session.expire_all()
        self.assertEqual(
            ProjectSpatialResource.query.get(old.id).status, "active"
        )
        self.assertEqual(
            ProjectSpatialResource.query.get(current.id).status, "retained"
        )
        # .active 标记随状态切换
        old_marker = (
            self.storage_root / "projects" / str(project_id) / "tiles" / str(old.id) / ".active"
        )
        current_marker = (
            self.storage_root / "projects" / str(project_id) / "tiles" / str(current.id) / ".active"
        )
        self.assertTrue(old_marker.is_file())
        self.assertFalse(current_marker.exists())

    def test_reactivate_rejects_already_active(self):
        project_id = self._seed_project_with_mine()
        current = self._add_basemap(project_id, version=1, status="active")

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{current.id}/reactivate", json={}
        )
        body = self._json(response)
        self.assertEqual(body["code"], 1)
        self.assertIn("已是激活状态", body["msg"])

    def test_reactivate_rejects_missing_tiles(self):
        project_id = self._seed_project_with_mine()
        self._add_basemap(project_id, version=1, status="retained", with_tiles=False)
        self._add_basemap(project_id, version=2, status="active")
        retained = ProjectSpatialResource.query.filter_by(
            project_id=project_id, resource_type="basemap", status="retained"
        ).one()

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{retained.id}/reactivate", json={}
        )
        body = self._json(response)
        self.assertEqual(body["code"], 1)
        self.assertIn("无法重新激活", body["msg"])

    def test_reactivate_rejects_tile_dir_without_png_tiles(self):
        project_id = self._seed_project_with_mine()
        # 目录存在但没有任何 PNG 瓦片（如切片被外部清理）：不可用
        empty = self._add_basemap(project_id, version=1, status="retained", with_tiles=False)
        tile_dir = (
            self.storage_root / "projects" / str(project_id) / "tiles" / str(empty.id)
        )
        tile_dir.mkdir(parents=True, exist_ok=True)
        self._add_basemap(project_id, version=2, status="active")

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{empty.id}/reactivate", json={}
        )
        body = self._json(response)
        self.assertEqual(body["code"], 1)
        self.assertIn("无法重新激活", body["msg"])

    def test_full_reactivation_chain_a_b_a_b(self):
        project_id = self._seed_project_with_mine()
        first = self._add_basemap(project_id, version=1, status="retained")
        second = self._add_basemap(project_id, version=2, status="active")

        def marker(resource_id):
            return (
                self.storage_root / "projects" / str(project_id) / "tiles" / str(resource_id) / ".active"
            )

        # A 激活：旧代码在激活 B 时已删 A 的标记；A 必须仍可重新激活（问题11）
        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{first.id}/reactivate", json={}
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        self.assertEqual(ProjectSpatialResource.query.get(first.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(second.id).status, "retained")
        self.assertTrue(marker(first.id).is_file())
        self.assertFalse(marker(second.id).exists())

        # B 重新激活：A 的标记随互斥切换被移除，B 标记重建
        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{second.id}/reactivate", json={}
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        self.assertEqual(ProjectSpatialResource.query.get(second.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(first.id).status, "retained")
        self.assertTrue(marker(second.id).is_file())
        self.assertFalse(marker(first.id).exists())

    def test_reactivate_restores_markers_on_db_commit_failure(self):
        from unittest.mock import patch

        project_id = self._seed_project_with_mine()
        old = self._add_basemap(project_id, version=1, status="retained")
        current = self._add_basemap(project_id, version=2, status="active")

        def marker(resource_id):
            return (
                self.storage_root / "projects" / str(project_id) / "tiles" / str(resource_id) / ".active"
            )

        with patch(
            "applications.project_hub.spatial_service.db.session.commit",
            side_effect=RuntimeError("commit failed"),
        ):
            response = self.client.post(
                f"/api/projects/{project_id}/spatial/basemaps/{old.id}/reactivate", json={}
            )
        self.assertEqual(response.status_code, 500)

        db.session.rollback()
        db.session.expire_all()
        # DB 回滚：current 仍 active、old 仍 retained
        self.assertEqual(ProjectSpatialResource.query.get(current.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(old.id).status, "retained")
        # 文件标记与库状态一致：新标记已回滚删除，旧（当前激活者）标记保留
        self.assertFalse(marker(old.id).exists())
        self.assertTrue(marker(current.id).is_file())
