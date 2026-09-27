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
            tile_dir = (
                self.storage_root / "projects" / str(project_id) / "tiles" / str(resource.id)
            )
            tile_dir.mkdir(parents=True, exist_ok=True)
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
