# -*- coding: utf-8 -*-
"""Project Hub 依赖边界回归（2026-11-14 审查问题28）：三条 import 环拆解后
固化边界，防止回潮。

- 环1 service↔snapshots：快照域只返回标识符，overview 装配归 API/装配层；
  snapshots 源码任何位置（含函数内延迟导入）不得 import service。
- 环2 classification_results→classification_stats→quality_review→
  classification_results：纯几何函数独立到 _geometry；classification_stats
  不得 import quality_review（quality_review 保留 re-export 兼容旧导入）。
- 环3 spatial_service→spatial_worker：底图激活状态机归空间领域服务
  （spatial_service.activate_basemap），worker 调用它；spatial_service 源码
  任何位置不得 import spatial_worker。

静态断言用 ast 扫描全部 Import/ImportFrom 节点（ast.walk 覆盖函数级延迟导入），
不依赖模块能否成功导入。
"""
import ast
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

PROJECT_HUB_DIR = Path(__file__).parent / "applications" / "project_hub"


def _collect_imports(module_filename):
    """收集模块内全部 import（含函数内延迟导入），返回 (module, names) 列表。"""
    source = (PROJECT_HUB_DIR / module_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    collected = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            collected.append((node.module or "", tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.Import):
            collected.append(("", tuple(alias.name for alias in node.names)))
    return collected


def _top_level_imports(module_filename):
    source = (PROJECT_HUB_DIR / module_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    collected = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            collected.append((node.module or "", tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.Import):
            collected.append(("", tuple(alias.name for alias in node.names)))
    return collected


def _imports_from(collected, target_module, name=None):
    for module, names in collected:
        if module == target_module:
            if name is None or name in names:
                return True
    return False


class TestStaticDependencyBoundary(unittest.TestCase):
    """环1：snapshots 不依赖 service（任何位置，含延迟导入）。"""

    def test_snapshots_never_imports_service(self):
        collected = _collect_imports("snapshots.py")
        self.assertFalse(
            _imports_from(collected, "applications.project_hub.service"),
            "snapshots.py 不得 import service（含函数内延迟导入），overview 装配归 API 层",
        )
        self.assertFalse(
            _imports_from(collected, "applications.project_hub", "service"),
            "snapshots.py 不得从 project_hub 包导入 service",
        )

    def test_classification_stats_does_not_import_quality_review(self):
        collected = _collect_imports("classification_stats.py")
        self.assertFalse(
            _imports_from(collected, "applications.project_hub.quality_review"),
            "classification_stats.py 不得 import quality_review，纯几何函数应取自 _geometry",
        )

    def test_spatial_service_never_imports_spatial_worker(self):
        collected = _collect_imports("spatial_service.py")
        self.assertFalse(
            _imports_from(collected, "applications.project_hub.spatial_worker"),
            "spatial_service.py 不得 import spatial_worker（含延迟导入），激活状态机归 service",
        )

    def test_spatial_worker_imports_activate_basemap_from_service_at_top_level(self):
        collected = _top_level_imports("spatial_worker.py")
        self.assertTrue(
            _imports_from(collected, "applications.project_hub.spatial_service", "activate_basemap"),
            "spatial_worker.py 顶层应从 spatial_service 导入 activate_basemap",
        )

    def test_service_reexports_snapshot_entrypoints(self):
        """兼容契约：service.py 继续从 snapshots re-export 快照入口。"""
        collected = _top_level_imports("service.py")
        for name in ("create_backup", "import_backup_manifest", "restore_backup"):
            self.assertTrue(
                _imports_from(collected, "applications.project_hub.snapshots", name),
                f"service.py 应继续 re-export snapshots.{name}",
            )

    def test_quality_review_reexports_geometry_helpers(self):
        """兼容契约：quality_review 继续暴露 geometry_area_m2/ring_area_m2。"""
        collected = _top_level_imports("quality_review.py")
        self.assertTrue(
            _imports_from(collected, "applications.project_hub._geometry", "geometry_area_m2"),
            "quality_review.py 应从 _geometry 导入并 re-export geometry_area_m2",
        )


class TestGeometryModuleBoundary(unittest.TestCase):
    def test_geometry_module_is_pure_and_single_source(self):
        from applications.project_hub import _geometry
        from applications.project_hub import quality_review

        self.assertIs(quality_review.geometry_area_m2, _geometry.geometry_area_m2)
        self.assertIs(quality_review.ring_area_m2, _geometry.ring_area_m2)

        square = {
            "type": "Polygon",
            "coordinates": [[
                [100.0, 25.0], [101.0, 25.0], [101.0, 26.0],
                [100.0, 26.0], [100.0, 25.0],
            ]],
        }
        # 1 平方度在纬度 25°~26° 的球面近似面积 ≈ 11159.7 km²（Chamberlain &
        # Duquette 公式对该方格的输出，作为回归基准钉住）
        self.assertAlmostEqual(
            _geometry.geometry_area_m2(square) / 1e6, 11159.7, delta=1.0
        )

    def test_geometry_module_has_no_project_dependencies(self):
        collected = _collect_imports("_geometry.py")
        for module, _names in collected:
            self.assertFalse(
                module.startswith("applications."),
                f"_geometry.py 必须零项目依赖（纯几何），发现 import: {module}",
            )


class TestSnapshotDomainContract(unittest.TestCase):
    """环1 行为面：快照域返回标识符，overview 由调用方装配。"""

    def setUp(self):
        from applications import create_app
        from applications.extensions import db

        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        from applications.extensions import db

        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    @staticmethod
    def _minimal_manifest():
        return {
            "snapshot_version": 1,
            "project_id": 1,
            "backup_id": 1,
            "summary": {"name": "来源项目", "status": "active"},
            "mines": [],
            "datasets": [],
            "exports": [],
            "activities": [],
        }

    def test_import_backup_manifest_returns_project_id_only(self):
        from applications.extensions import db
        from applications.models.project import ProjectMineBinding
        from applications.project_hub.service import create_project
        from applications.project_hub.snapshots import import_backup_manifest

        created = create_project({"name": "导入目标", "region": "昆明"})
        manifest = self._minimal_manifest()
        manifest["mines"] = [{"mine_fid": 5301}]

        result = import_backup_manifest(created["id"], manifest)

        self.assertEqual(result, {"project_id": created["id"]})
        self.assertEqual(
            db.session.query(ProjectMineBinding.mine_fid)
            .filter_by(project_id=created["id"])
            .scalar(),
            5301,
        )

    def test_restore_backup_returns_identifiers_only(self):
        from applications.project_hub.service import create_backup, create_project, restore_backup

        created = create_project({"name": "恢复目标", "region": "大理"})
        backup = create_backup(created["id"], {})

        result = restore_backup(created["id"], backup["id"])

        self.assertEqual(
            result, {"project_id": created["id"], "backup_id": backup["id"]}
        )


class TestActivateBasemapOwnership(unittest.TestCase):
    """环3 行为面：激活状态机由 spatial_service 持有且语义不变。"""

    def setUp(self):
        from applications import create_app
        from applications.extensions import db

        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        from applications.extensions import db

        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_activate_basemap_lives_in_spatial_service(self):
        import tempfile

        from applications.extensions import db
        from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource
        from applications.project_hub.service import create_project
        from applications.project_hub.spatial_service import activate_basemap

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage_root = Path(temp_dir.name)
        created = create_project({"name": "激活归属", "region": "洱源"})

        resource = ProjectSpatialResource(
            project_id=created["id"],
            resource_type="basemap",
            version=1,
            status="processing",
            source_path=f"projects/{created['id']}/basemaps/1/source.tif",
            tile_path=f"projects/{created['id']}/tiles/1",
            source_format="tif",
            min_zoom=8,
            max_zoom=15,
        )
        db.session.add(resource)
        db.session.flush()
        tile_dir = storage_root / resource.tile_path
        tile_dir.mkdir(parents=True, exist_ok=True)
        job = ProjectSpatialJob(
            id="job-activate-owner",
            project_id=created["id"],
            resource_id=resource.id,
            job_type="basemap_tiles",
            status="running",
            stage="tiling",
        )
        db.session.add(job)
        db.session.commit()

        activate_basemap(job, resource, storage_root)

        self.assertEqual(resource.status, "active")
        self.assertEqual(job.status, "succeeded")
        marker = tile_dir / ".active"
        self.assertTrue(marker.is_file(), "激活后必须写入 .active 标记")
        self.assertEqual(marker.read_text(encoding="ascii"), str(resource.id))


if __name__ == "__main__":
    unittest.main()
