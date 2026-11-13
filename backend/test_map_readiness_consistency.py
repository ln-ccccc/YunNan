# -*- coding: utf-8 -*-
"""问题24 回归（2026-11-14 审查）：地图就绪状态的两套判据必须一致。

被测行为：激活底图的 active 记录存在但磁盘瓦片缺失时——
1. map/manifest（地图打开入口）的 map_ready 必须为 False 且 missing_resources
   含 basemap，与 tile_url=None 自洽（此前 map_ready=True 与 tile_url=None 分裂）；
2. /spatial（空间资源面板）的 map_ready 必须为 False（此前仅看数据库记录）；
3. overview（readiness 权威口径，含 R3 磁盘巡检）与上述入口结论一致；
4. 项目列表的三键保持记录级轻量兼容口径（不扫磁盘，权威判据以 readiness 为准）；
5. 未配置项目的 missing_resources 词汇（mine_vector/basemap）保持兼容不变。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.project_map import get_project_map_manifest
from applications.project_hub.service import get_project_overview, list_projects
from applications.project_hub.spatial_service import get_project_spatial


class TestMapReadinessConsistency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="map-readiness-")
        self.previous_env = {
            key: os.environ.get(key)
            for key in ("PROJECT_STORAGE_ROOT", "ADMIN_USERNAME", "ADMIN_PASSWORD")
        }
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self._restore_environment)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "Secret123!"

        self.app = create_app("testing")
        self.assertEqual(self.app.config["SQLALCHEMY_DATABASE_URI"], "sqlite:///:memory:")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

    def _restore_environment(self):
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _seed_active_project(self):
        db.session.add(Project(id=1, name="地图判据项目", region="云南省", status="active"))
        db.session.add(
            ProjectSpatialResource(
                project_id=1,
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path="incoming/mine.geojson",
                normalized_path="projects/1/inputs/mine.geojson",
                source_format="geojson",
                bounds_json="[100.0, 25.0, 100.1, 25.1]",
            )
        )
        basemap = ProjectSpatialResource(
            project_id=1,
            resource_type="basemap",
            version=1,
            status="active",
            source_path="incoming/base.tif",
            source_format="tif",
            tile_path="projects/1/tiles/2",
            bounds_json="[100.0, 25.0, 100.1, 25.1]",
        )
        db.session.add(basemap)
        db.session.commit()
        return basemap.id

    def _write_tiles(self, resource_id):
        tile_dir = self.storage_root / "projects" / "1" / "tiles" / str(resource_id)
        (tile_dir / "0" / "0").mkdir(parents=True)
        (tile_dir / ".active").write_text("", encoding="utf-8")
        (tile_dir / "0" / "0" / "0.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def test_manifest_reports_not_ready_when_tiles_missing(self):
        self._seed_active_project()  # 记录 active，磁盘瓦片目录不存在

        manifest = get_project_map_manifest(1)
        self.assertIsNone(manifest["tile_url"])
        self.assertFalse(manifest["map_ready"])
        self.assertIn("basemap", manifest["missing_resources"])

    def test_spatial_endpoint_agrees_when_tiles_missing(self):
        self._seed_active_project()

        state = get_project_spatial(1)
        self.assertFalse(state["map_ready"])
        self.assertIn("basemap", state["missing_resources"])

    def test_overview_and_manifest_agree_on_tiles_missing(self):
        self._seed_active_project()

        overview = get_project_overview(1)
        manifest = get_project_map_manifest(1)
        self.assertFalse(overview["summary"]["map_ready"])
        self.assertFalse(overview["capabilities"]["can_open_map"])
        self.assertEqual(manifest["map_ready"], overview["summary"]["map_ready"])
        basemap_check = next(
            check
            for check in overview["readiness"]["checks"]
            if check["code"] == "ACTIVE_BASEMAP"
        )
        self.assertEqual(basemap_check["reason_code"], "ACTIVE_BASEMAP_TILES_MISSING")

    def test_all_exits_ready_when_tiles_present(self):
        resource_id = self._seed_active_project()
        self._write_tiles(resource_id)

        manifest = get_project_map_manifest(1)
        state = get_project_spatial(1)
        overview = get_project_overview(1)
        self.assertIsNotNone(manifest["tile_url"])
        self.assertTrue(manifest["map_ready"])
        self.assertEqual(manifest["missing_resources"], [])
        self.assertTrue(state["map_ready"])
        self.assertTrue(overview["summary"]["map_ready"])
        self.assertTrue(overview["capabilities"]["can_open_map"])

    def test_project_list_state_stays_record_level(self):
        """列表三键是记录级兼容快照：不扫磁盘（N 项目×磁盘探针违反性能底线），
        权威可操作性判定以 overview/readiness（含 R3 巡检）为准——契约固化。"""
        self._seed_active_project()  # 瓦片缺失，但记录级口径不感知

        payload = list_projects()
        item = next(row for row in payload["items"] if row["id"] == 1)
        self.assertTrue(item["map_ready"])

    def test_unconfigured_vocabulary_unchanged(self):
        db.session.add(Project(id=2, name="未配置项目", region="云南省", status="draft"))
        db.session.commit()

        state = get_project_spatial(2)
        self.assertEqual(state["missing_resources"], ["mine_vector", "basemap"])
        manifest = get_project_map_manifest(2)
        self.assertEqual(manifest["missing_resources"], ["mine_vector", "basemap"])


if __name__ == "__main__":
    unittest.main()
