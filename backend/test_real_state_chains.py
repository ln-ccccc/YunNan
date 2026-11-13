# -*- coding: utf-8 -*-
"""真实状态链回归（2026-11-14 审查问题33）。

既有检查多为"手写状态 fixture + 单端点断言"：状态不是由状态机产生的，
构造出了真实流程不会出现的状态（如 retained 底图自带 .active——activate
状态机在 commit 成功后会删除旧标记），掩盖了 reactivate 前提校验与状态机
语义不一致的真实缺陷。本文件补审查列出的关键状态链：

- 底图经真实激活状态机反复激活后重新激活（reactivate 前提与状态机对齐）
- 激活链的保留/清理语义（RETAINED_BASEMAP_KEEP）
- 两个年份连续审校 + 空修订（修订号独立递增）
- 快照创建→恢复→导出→下载全链路
- 分片上传并发 complete 见 test_file_upload_chunks.py
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.project import (
    ProjectDataset,
    ProjectExportRecord,
    ProjectMineBinding,
)
from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource
from applications.models.classification_result import ClassificationRevision, ClassificationResult

from test_project_api import TestProjectAPI

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
ROI_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]
    ],
}
FEATURE_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [[100.01, 25.01], [100.05, 25.01], [100.05, 25.05], [100.01, 25.05], [100.01, 25.01]]
    ],
}


class TestRealBasemapActivationChain(TestProjectAPI):
    """底图状态全部经 _activate_basemap 状态机产生（job/瓦片目录为
    worker 切片产物的替身），杜绝手写 status/.active。"""

    def _activate_new_basemap(self, project_id, version):
        from applications.project_hub.spatial_worker import _activate_basemap

        resource = ProjectSpatialResource(
            project_id=project_id,
            resource_type="basemap",
            version=version,
            status="processing",
            source_path=f"projects/{project_id}/basemaps/v{version}.tif",
            source_format="tif",
            min_zoom=8,
            max_zoom=12,
        )
        db.session.add(resource)
        db.session.flush()
        tile_rel = os.path.join("projects", str(project_id), "tiles", str(resource.id))
        resource.tile_path = tile_rel.replace(os.sep, "/")
        job = ProjectSpatialJob(
            id=f"job-chain-{project_id}-{version}",
            project_id=project_id,
            resource_id=resource.id,
            job_type="basemap_tiles",
            status="running",
            stage="tiling",
        )
        db.session.add(job)
        db.session.commit()
        tile_dir = self.storage_root / "projects" / str(project_id) / "tiles" / str(resource.id)
        (tile_dir / "8" / "0" / "0.png").parent.mkdir(parents=True, exist_ok=True)
        (tile_dir / "8" / "0" / "0.png").write_bytes(PNG_BYTES)
        _activate_basemap(job, resource, self.storage_root)
        return resource

    def _tile_dir(self, project_id, resource_id):
        return self.storage_root / "projects" / str(project_id) / "tiles" / str(resource_id)

    def test_reactivate_after_state_machine_activation(self):
        """真实链：状态机激活 v1→v2 后（v1 的 .active 已被状态机删除、瓦片仍在），
        reactivate v1 必须成功——此前前提校验要求 .active 存在，与状态机
        "commit 后删旧标记"语义矛盾，真实流程下 retained 永远无法重新激活。"""
        self.login_as_admin()
        project_id = self._create_project("底图真实链项目")

        v1 = self._activate_new_basemap(project_id, version=1)
        self.assertEqual(v1.status, "active")
        self.assertTrue((self._tile_dir(project_id, v1.id) / ".active").is_file())

        v2 = self._activate_new_basemap(project_id, version=2)
        db.session.expire_all()
        self.assertEqual(
            ProjectSpatialResource.query.get(v2.id).status, "active"
        )
        self.assertEqual(
            ProjectSpatialResource.query.get(v1.id).status, "retained"
        )
        # 状态机语义：旧标记在 commit 后删除，瓦片保留
        self.assertFalse((self._tile_dir(project_id, v1.id) / ".active").exists())
        self.assertTrue((self._tile_dir(project_id, v1.id) / "8" / "0" / "0.png").is_file())

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemaps/{v1.id}/reactivate", json={}
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        db.session.expire_all()
        self.assertEqual(ProjectSpatialResource.query.get(v1.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(v2.id).status, "retained")
        self.assertTrue((self._tile_dir(project_id, v1.id) / ".active").is_file())
        self.assertFalse((self._tile_dir(project_id, v2.id) / ".active").exists())

    def test_fourth_activation_cleans_only_oldest_retained(self):
        """v1→v4 连续激活：保留最近 RETAINED_BASEMAP_KEEP(2) 个历史底图，
        第 4 次激活才清理最旧的 v1（连库删除 + 目录清理），v2/v3 完整保留。"""
        self.login_as_admin()
        project_id = self._create_project("底图保留链项目")

        v1 = self._activate_new_basemap(project_id, version=1)
        v2 = self._activate_new_basemap(project_id, version=2)
        v3 = self._activate_new_basemap(project_id, version=3)
        # 第 3 次激活时 retained=[v2, v1] 未超保留数，均不应被清理
        db.session.expire_all()
        self.assertIsNotNone(ProjectSpatialResource.query.get(v1.id))
        self.assertIsNotNone(ProjectSpatialResource.query.get(v2.id))

        v4 = self._activate_new_basemap(project_id, version=4)
        db.session.expire_all()

        self.assertEqual(ProjectSpatialResource.query.get(v4.id).status, "active")
        self.assertEqual(ProjectSpatialResource.query.get(v3.id).status, "retained")
        self.assertEqual(ProjectSpatialResource.query.get(v2.id).status, "retained")
        self.assertIsNone(
            ProjectSpatialResource.query.get(v1.id), "超出保留数的最旧底图应连库删除"
        )
        self.assertFalse(self._tile_dir(project_id, v1.id).exists())
        self.assertTrue(self._tile_dir(project_id, v2.id).is_dir())
        self.assertTrue(self._tile_dir(project_id, v3.id).is_dir())


class TestRealRevisionChain(TestProjectAPI):
    def _add_result(self, project_id, mine_fid, year):
        baseline_fc = json.dumps({"type": "FeatureCollection", "features": []})
        result = ClassificationResult(
            project_id=project_id,
            mine_fid=mine_fid,
            year=year,
            inference_job_id=f"job-{mine_fid}-{year}",
            model_id="cc-ln/CUGRS",
            mine_resource_id=1,
            vector_status="ready",
            current_revision_no=1,
            current_feature_collection_json=baseline_fc,
            roi_geometry_json=json.dumps(ROI_POLYGON),
        )
        db.session.add(result)
        db.session.flush()
        # 真实流程由发布自动生成 rev1 基线修订（source=auto）；保存校验
        # _base_feature_ids 依赖该记录存在，fixture 不造空缺状态
        snapshot_key = (
            Path("projects") / str(project_id) / "outputs"
            / "classification-results" / str(result.id) / "revisions" / "1.geojson"
        )
        snapshot_path = self.storage_root / snapshot_key
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(baseline_fc, encoding="utf-8")
        db.session.add(
            ClassificationRevision(
                result_id=result.id,
                revision_no=1,
                source="auto",
                author="system",
                feature_count=0,
                snapshot_path=snapshot_key.as_posix(),
                feature_collection_json=baseline_fc,
            )
        )
        db.session.commit()
        return result

    def _save_revision(self, project_id, result, base_revision_no, features):
        return self.client.post(
            f"/api/projects/{project_id}/classification-results/{result.id}/revisions",
            json={
                "base_revision_no": base_revision_no,
                "feature_collection": {"type": "FeatureCollection", "features": features},
            },
        )

    @staticmethod
    def _feature(feature_id):
        return {
            "type": "Feature",
            "properties": {"class_code": 1, "feature_id": feature_id},
            "geometry": FEATURE_GEOMETRY,
        }

    def test_two_year_consecutive_revisions_with_empty_save(self):
        """两个年份连续审校互不干扰；空修订合法且修订号继续递增。"""
        self.login_as_admin()
        project_id = self._create_project("连续审校项目")
        result_2023 = self._add_result(project_id, mine_fid=101, year=2023)
        result_2024 = self._add_result(project_id, mine_fid=102, year=2024)

        first = self._save_revision(project_id, result_2023, 1, [self._feature("client-a")])
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))

        second = self._save_revision(project_id, result_2024, 1, [self._feature("client-b")])
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))

        db.session.expire_all()
        r2023 = ClassificationResult.query.get(result_2023.id)
        r2024 = ClassificationResult.query.get(result_2024.id)
        self.assertEqual(r2023.current_revision_no, 2)
        self.assertEqual(r2024.current_revision_no, 2)
        # 各自 = rev1 自动基线 + rev2 手动保存，互不串号
        self.assertEqual(
            ClassificationRevision.query.filter_by(result_id=r2023.id).count(), 2
        )
        self.assertEqual(
            ClassificationRevision.query.filter_by(result_id=r2024.id).count(), 2
        )

        empty = self._save_revision(project_id, result_2023, 2, [])
        self.assertEqual(empty.status_code, 200, empty.get_data(as_text=True))
        db.session.expire_all()
        r2023 = ClassificationResult.query.get(result_2023.id)
        self.assertEqual(r2023.current_revision_no, 3)
        self.assertEqual(r2023.feature_count, 0)
        # 2024 的修订不受 2023 后续保存影响
        self.assertEqual(ClassificationResult.query.get(result_2024.id).current_revision_no, 2)

        # 历史修订仍可追溯：rev1 基线 + rev2 + rev3
        listing = self.client.get(
            f"/api/projects/{project_id}/classification-results/{result_2023.id}/revisions"
        )
        self.assertEqual(listing.status_code, 200)
        revisions = self._json(listing)["data"]["revisions"]
        self.assertEqual([r["revision_no"] for r in revisions], [1, 2, 3])


class TestSnapshotRestoreExportChain(TestProjectAPI):
    def test_backup_restore_then_export_download(self):
        """快照创建→恢复→导出→下载全链路，制品校验和与 manifest 一致。"""
        self.login_as_admin()
        project_id = self._create_project("快照恢复链项目")
        db.session.add(
            ProjectMineBinding(project_id=project_id, mine_fid=701, mine_name_snapshot="链矿", sort_order=0)
        )
        db.session.add(
            ProjectDataset(
                project_id=project_id,
                dataset_kind="imagery",
                display_name="链影像",
                file_path=f"projects/{project_id}/inputs/imagery/a.tif",
                source_format="tif",
                mine_fid=701,
                year_start=2024,
                year_end=2024,
                slice_config_json="{}",
            )
        )
        db.session.commit()

        backup = self.client.post(f"/api/projects/{project_id}/backups", json={})
        self.assertEqual(backup.status_code, 200, backup.get_data(as_text=True))
        backup_id = self._json(backup)["data"]["id"]

        restored = self.client.post(
            f"/api/projects/{project_id}/backups/{backup_id}/restore", json={}
        )
        self.assertEqual(restored.status_code, 200, restored.get_data(as_text=True))

        exported = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})
        self.assertEqual(exported.status_code, 200, exported.get_data(as_text=True))
        export_id = self._json(exported)["data"]["id"]

        artifact = self.client.get(f"/api/projects/{project_id}/exports/{export_id}/artifact")
        self.assertEqual(artifact.status_code, 200, artifact.get_data(as_text=True))
        body = artifact.data
        self.assertTrue(body)

        manifest_path = (
            self.storage_root / "projects" / str(project_id) / "exports" / str(export_id) / "manifest.json"
        )
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["sha256"], hashlib.sha256(body).hexdigest())
        record = ProjectExportRecord.query.get(export_id)
        self.assertEqual(record.status, "completed")


if __name__ == "__main__":
    unittest.main()
