# -*- coding: utf-8 -*-
"""M2 项目管理增强契约测试：删除（仅归档可删+目录入 trash）、批量归档/删除、
快照跨环境导入、鉴权与上限。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.inference_job import InferenceJob
from applications.models.project import (
    Project,
    ProjectActivityLog,
    ProjectDataset,
    ProjectExportRecord,
    ProjectMineBinding,
)
from applications.project_hub.service import create_backup, ProjectDeleteNotAllowed, delete_project
from test_project_api import TestProjectAPI


class ProjectManageBase(TestProjectAPI):
    def _archive(self, project_id):
        response = self.client.post(f"/api/projects/{project_id}/archive")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def _make_project_dir(self, project_id, sentinel="marker.txt"):
        root = self.storage_root / "projects" / str(project_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / sentinel).write_text("data")
        return root


class TestProjectDelete(ProjectManageBase):
    def test_active_project_cannot_be_deleted(self):
        self.login_as_admin()
        project_id = self._create_project("活跃项目不可删")
        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("归档", response.get_json()["msg"])
        # 项目仍在列表
        body = self.client.get("/api/projects").get_json()
        self.assertTrue(any(i["id"] == project_id for i in body["data"]["items"]))

    def test_archived_project_deletes_soft_and_moves_storage_to_trash(self):
        self.login_as_admin()
        project_id = self._create_project("可删项目")
        project_dir = self._make_project_dir(project_id)
        self._archive(project_id)

        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()["data"]
        self.assertTrue(body["deleted"])
        self.assertIsNotNone(body["trash_target"])
        # 目录已移入 trash 且数据保留（人工可恢复）
        self.assertFalse(project_dir.exists())
        trash_root = self.storage_root / "trash"
        moved = list(trash_root.glob(f"{project_id}_*"))
        self.assertEqual(len(moved), 1)
        self.assertTrue((moved[0] / "marker.txt").is_file())
        # DB 软删：列表不再可见，记录仍在
        listing = self.client.get("/api/projects").get_json()["data"]["items"]
        self.assertFalse(any(i["id"] == project_id for i in listing))
        self.assertIsNotNone(db.session.get(Project, project_id).deleted_at)

    def test_archived_project_without_storage_dir_deletes(self):
        self.login_as_admin()
        project_id = self._create_project("无目录项目")
        self._archive(project_id)
        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["data"]["trash_target"])

    def test_delete_requires_login(self):
        self.login_as_admin()
        project_id = self._create_project("鉴权项目")
        self._archive(project_id)
        fresh = self.app.test_client()
        response = fresh.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 401)


class TestProjectBatch(ProjectManageBase):
    def test_batch_archive_reports_per_item_results(self):
        self.login_as_admin()
        a = self._create_project("批归A")
        b = self._create_project("批归B")
        response = self.client.post(
            "/api/projects/batch/archive",
            json={"project_ids": [a, b, 99999]},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["succeeded"], 2)
        self.assertEqual(data["failed"], 1)
        by_id = {r["project_id"]: r for r in data["results"]}
        self.assertTrue(by_id[a]["ok"])
        self.assertFalse(by_id[99999]["ok"])

    def test_batch_delete_mixes_archive_gate_and_success(self):
        self.login_as_admin()
        active = self._create_project("活动勿删")
        archived = self._create_project("已归档可删")
        self._make_project_dir(archived)
        self._archive(archived)
        response = self.client.post(
            "/api/projects/batch/delete",
            json={"project_ids": [active, archived, "not-int"]},
        )
        data = response.get_json()["data"]
        by_id = {r["project_id"]: r for r in data["results"]}
        self.assertFalse(by_id[active]["ok"])
        self.assertIn("归档", by_id[active]["msg"])
        self.assertTrue(by_id[archived]["ok"])
        self.assertFalse(by_id["not-int"]["ok"])
        self.assertEqual(data["succeeded"], 1)

    def test_batch_rejects_invalid_payloads(self):
        self.login_as_admin()
        for payload in (None, {}, {"project_ids": []}, {"project_ids": "1,2"}, {"project_ids": [1] * 101}):
            response = self.client.post("/api/projects/batch/archive", json=payload)
            self.assertEqual(response.status_code, 422, payload)


class TestBackupImport(ProjectManageBase):
    def _build_manifest(self, source_project_id, name="迁移项目", dataset_path_prefix=None):
        prefix = dataset_path_prefix or f"projects/{source_project_id}/inputs/imagery"
        return {
            "snapshot_version": 1,
            "project_id": source_project_id,
            "backup_id": 7,
            "summary": {
                "name": name, "region": "大理", "manager": "张三", "remark": "迁移",
                "status": "active", "monitor_start_year": 2023, "monitor_end_year": 2025,
            },
            "mines": [
                {"mine_fid": 101, "mine_name_snapshot": "矿山一", "sort_order": 0},
                {"mine_fid": 102, "sort_order": 1},
            ],
            "datasets": [
                # file_path 必须是本项目根内的路径（收官审查 S1 收口后非法路径整批 422）
                {"dataset_kind": "imagery", "display_name": "影像A",
                 "file_path": f"{prefix}/a.tif"},
            ],
            "exports": [{"format": "geojson", "file_path": "out.geojson"}],
            "activities": [{"event_type": "project_created", "created_at": "2026-01-01T00:00:00"}],
        }

    def test_import_manifest_applies_summary_and_rebuilds_bindings(self):
        self.login_as_admin()
        target = self._create_project("导入目标")
        db.session.add(ProjectMineBinding(project_id=target, mine_fid=555))
        db.session.commit()

        response = self.client.post(
            f"/api/projects/{target}/backups/import",
            json={"manifest": self._build_manifest(
                999, name="迁移后的项目",
                dataset_path_prefix=f"projects/{target}/inputs/imagery",
            )},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        project = db.session.get(Project, target)
        self.assertEqual(project.name, "迁移后的项目")
        self.assertEqual(project.region, "大理")
        fids = sorted(b.mine_fid for b in project.mines)
        self.assertEqual(fids, [101, 102])
        kinds = [d.dataset_kind for d in project.datasets]
        self.assertIn("imagery", kinds)
        # 导入来源 manifest 已存档
        imports_dir = self.storage_root / "projects" / str(target) / "imports"
        self.assertTrue(list(imports_dir.glob("manifest_*.json")))
        # 活动只追加一条导入事件；源 manifest 里的活动不重放
        # （project_created 计数保持建项目时的 1，不因导入 +1）
        events = [a.event_type for a in project.activities]
        self.assertIn("project_imported", events)
        self.assertEqual(events.count("project_created"), 1)

    def test_import_rejects_dataset_path_outside_project_root(self):
        """收官审查 S1：manifest 直传任意路径曾被原样入库，间接读取服务器任意栅格。"""
        self.login_as_admin()
        target = self._create_project("越界导入目标")
        manifest = self._build_manifest(999, name="越界尝试")
        manifest["datasets"] = [
            {"dataset_kind": "imagery", "display_name": "evil", "file_path": "/etc/passwd"},
        ]
        response = self.client.post(
            f"/api/projects/{target}/backups/import",
            json={"manifest": manifest},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("不在本项目存储内", response.get_json()["msg"])

    def test_import_rebases_incoming_and_foreign_project_dataset_keys(self):
        """契约 B（2026-10-24 审查 P2-3）：incoming/ 与他项目键跨环境导入不再
        整批 422 死路，统一重定位到目标项目 inputs/imagery/ 相对键落库。"""
        self.login_as_admin()
        target = self._create_project("重定位目标")
        manifest = self._build_manifest(999)
        manifest["datasets"] = [
            {"dataset_kind": "imagery", "display_name": "上传件", "file_path": "incoming/upload.tif"},
            {"dataset_kind": "imagery", "display_name": "他项目裁剪", "file_path": "projects/999/inputs/imagery/clip_x.tif"},
        ]
        response = self.client.post(
            f"/api/projects/{target}/backups/import",
            json={"manifest": manifest},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        paths = sorted(d.file_path for d in db.session.get(Project, target).datasets)
        self.assertEqual(paths, [
            f"projects/{target}/inputs/imagery/clip_x.tif",
            f"projects/{target}/inputs/imagery/upload.tif",
        ])

    def test_import_rejects_malformed_manifest(self):
        self.login_as_admin()
        target = self._create_project("坏清单目标")
        for bad in (
            {},
            {"snapshot_version": 2, "summary": {}, "mines": [], "datasets": [], "exports": [], "activities": []},
            {"snapshot_version": 1, "summary": {}, "mines": "not-list", "datasets": [], "exports": [], "activities": []},
            {"snapshot_version": 1, "summary": {}, "mines": [{"no_fid": 1}], "datasets": [], "exports": [], "activities": []},
        ):
            response = self.client.post(
                f"/api/projects/{target}/backups/import",
                json={"manifest": bad},
            )
            self.assertEqual(response.status_code, 422, bad)

    def test_import_requires_login(self):
        fresh = self.app.test_client()
        response = fresh.post(
            "/api/projects/1/backups/import",
            json={"manifest": {"snapshot_version": 1}},
        )
        self.assertEqual(response.status_code, 401)

    def test_import_creates_new_identity_with_explicit_dataset_mapping(self):
        """问题3 跨环境规则：导入生成新身份并显式记录 源ID→新ID 映射，
        旧引用不得视为新环境有效引用（映射随事件与导入存档可查）。"""
        self.login_as_admin()
        target = self._create_project("映射目标")
        manifest = self._build_manifest(999, name="映射项目")
        manifest["datasets"] = [
            {"id": 501, "dataset_kind": "imagery", "display_name": "源影像A",
             "file_path": f"projects/{target}/inputs/imagery/map_a.tif"},
            {"id": 502, "dataset_kind": "imagery", "display_name": "源影像B",
             "file_path": f"projects/{target}/inputs/imagery/map_b.tif"},
        ]
        response = self.client.post(
            f"/api/projects/{target}/backups/import",
            json={"manifest": manifest},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        rows = {d.id for d in db.session.get(Project, target).datasets}
        self.assertEqual(len(rows), 2)
        event = (
            ProjectActivityLog.query.filter_by(project_id=target, event_type="project_imported")
            .order_by(ProjectActivityLog.id.desc())
            .first()
        )
        payload = json.loads(event.payload_json)
        mapping = payload["dataset_id_map"]
        self.assertEqual(sorted(mapping), ["501", "502"])
        for source_id, new_id in mapping.items():
            self.assertIn(new_id, rows)
            self.assertNotEqual(int(source_id), new_id, "跨环境导入必须生成新身份")
        # 导入存档携带映射，供追溯新旧引用
        archived = sorted(
            (self.storage_root / "projects" / str(target) / "imports").glob("manifest_*.json")
        )[-1]
        archived_data = json.loads(archived.read_text(encoding="utf-8"))
        self.assertEqual(archived_data["import_result"]["dataset_id_map"], mapping)

    def test_import_keeps_target_dataset_referenced_by_jobs(self):
        """问题3 引用保护同口径：目标项目被推理任务引用的登记不随导入删除。"""
        self.login_as_admin()
        target = self._create_project("引用保护目标")
        referenced = ProjectDataset(
            project_id=target,
            dataset_kind="imagery",
            display_name="目标既有影像",
            file_path=f"projects/{target}/inputs/imagery/keep.tif",
        )
        db.session.add(referenced)
        db.session.flush()
        db.session.add(InferenceJob(
            id="job-import-keep-1",
            project_id=target,
            request_payload_json=json.dumps(
                {"project_id": target, "dataset_id": referenced.id}
            ),
        ))
        db.session.commit()
        response = self.client.post(
            f"/api/projects/{target}/backups/import",
            json={"manifest": self._build_manifest(
                999, dataset_path_prefix=f"projects/{target}/inputs/imagery",
            )},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIsNotNone(
            db.session.get(ProjectDataset, referenced.id),
            "被任务引用的目标登记必须保留",
        )
        event = (
            ProjectActivityLog.query.filter_by(project_id=target, event_type="project_imported")
            .order_by(ProjectActivityLog.id.desc())
            .first()
        )
        payload = json.loads(event.payload_json)
        self.assertIn(referenced.id, payload["retained_dataset_ids"])


class TestBackupRestoreIdentity(ProjectManageBase):
    """问题3+4（2026-11 审查）：恢复按记录身份原位更新，不删表重建；
    审计只追加；旧快照缺身份时限制恢复范围。"""

    def _register_dataset(self, project_id, display_name, filename):
        return self._json(self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": display_name,
                "storage_key": self._create_incoming_tif(filename),
            },
        ))["data"]["id"]

    def _make_export_record(self, project_id, artifact="ledger.csv"):
        record = ProjectExportRecord(
            project_id=project_id,
            format="csv",
            file_path=f"projects/{project_id}/exports/pending/{artifact}",
            status="completed",
            request_params_json="{}",
        )
        db.session.add(record)
        db.session.flush()
        export_dir = (
            self.storage_root / "projects" / str(project_id) / "exports" / str(record.id)
        )
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / artifact).write_text("id,name\n1,demo\n", encoding="utf-8")
        db.session.commit()
        return record.id

    def _restore(self, project_id, backup_id):
        response = self.client.post(
            f"/api/projects/{project_id}/backups/{backup_id}/restore", json={}
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self._json(response)

    def _restore_event_payload(self, project_id):
        event = (
            ProjectActivityLog.query.filter_by(
                project_id=project_id, event_type="backup_restored"
            )
            .order_by(ProjectActivityLog.id.desc())
            .first()
        )
        self.assertIsNotNone(event)
        return json.loads(event.payload_json)

    def test_restore_preserves_identity_and_reference_targets(self):
        self.login_as_admin()
        project_id = self._create_project("身份保留项目")
        dataset_id = self._register_dataset(project_id, "快照时影像", "identity-a.tif")
        export_id = self._make_export_record(project_id)
        db.session.add(InferenceJob(
            id="job-restore-identity-1",
            project_id=project_id,
            request_payload_json=json.dumps(
                {"project_id": project_id, "dataset_id": dataset_id}
            ),
        ))
        db.session.commit()
        backup_id = self._json(self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        ))["data"]["id"]

        # 快照后的演化：改名、新增登记、新增导出
        db.session.get(ProjectDataset, dataset_id).display_name = "快照后改名"
        post_dataset_id = self._register_dataset(project_id, "快照后新增", "identity-b.tif")
        post_export_id = self._make_export_record(project_id, "post.csv")

        self._restore(project_id, backup_id)

        dataset = db.session.get(ProjectDataset, dataset_id)
        self.assertIsNotNone(dataset, "快照时数据集必须保留原身份（不删表重建）")
        self.assertEqual(dataset.display_name, "快照时影像", "快照时记录恢复到快照时刻字段值")
        self.assertIsNotNone(
            db.session.get(ProjectExportRecord, export_id),
            "快照时导出记录是不可变历史，不得删除",
        )
        self.assertIsNotNone(
            db.session.get(ProjectExportRecord, post_export_id),
            "快照后导出记录必须保留",
        )
        artifact = (
            self.storage_root / "projects" / str(project_id)
            / "exports" / str(export_id) / "ledger.csv"
        )
        self.assertTrue(artifact.is_file(), "原导出物理文件必须仍可按原记录 ID 定位下载")
        payload = json.loads(
            db.session.get(InferenceJob, "job-restore-identity-1").request_payload_json
        )
        self.assertIsNotNone(
            db.session.get(ProjectDataset, payload["dataset_id"]),
            "历史任务载荷 dataset_id 必须仍能定位输入",
        )
        self.assertIsNone(
            db.session.get(ProjectDataset, post_dataset_id),
            "快照后新增且未被引用的登记按恢复语义移除",
        )

    def test_restore_keeps_referenced_dataset_added_after_snapshot(self):
        self.login_as_admin()
        project_id = self._create_project("引用保留项目")
        self._register_dataset(project_id, "快照时影像", "keepref-a.tif")
        backup_id = self._json(self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        ))["data"]["id"]

        post_dataset_id = self._register_dataset(project_id, "快照后被引用影像", "keepref-b.tif")
        db.session.add(InferenceJob(
            id="job-restore-keepref-1",
            project_id=project_id,
            request_payload_json=json.dumps(
                {"project_id": project_id, "dataset_id": post_dataset_id}
            ),
        ))
        db.session.commit()

        self._restore(project_id, backup_id)

        self.assertIsNotNone(
            db.session.get(ProjectDataset, post_dataset_id),
            "快照后新增但被任务引用的登记不得删除（引用保护）",
        )
        payload = self._restore_event_payload(project_id)
        self.assertIn(post_dataset_id, payload["retained_referenced_datasets"])

    def test_restore_appends_audit_instead_of_truncating_history(self):
        self.login_as_admin()
        project_id = self._create_project("审计追加项目")
        self._register_dataset(project_id, "快照时影像", "audit-a.tif")
        backup_id = self._json(self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        ))["data"]["id"]

        self._register_dataset(project_id, "快照后新增", "audit-b.tif")
        events_before = [
            a.id for a in ProjectActivityLog.query.filter_by(project_id=project_id).all()
        ]

        self._restore(project_id, backup_id)

        events_after = [
            a.id for a in ProjectActivityLog.query.filter_by(project_id=project_id).all()
        ]
        for activity_id in events_before:
            self.assertIn(activity_id, events_after, "快照后活动不得随恢复消失（审计只追加）")
        payload = self._restore_event_payload(project_id)
        self.assertEqual(payload["backup_id"], backup_id)
        self.assertIn("restored", payload)

    def test_restore_legacy_manifest_limits_scope_without_guessing(self):
        self.login_as_admin()
        project_id = self._create_project("旧快照项目")
        dataset_id = self._register_dataset(project_id, "快照时影像", "legacy-a.tif")
        backup_id = self._json(self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        ))["data"]["id"]

        # 改写为旧版 manifest（序列化不含记录 id），模拟历史快照
        manifest_path = (
            self.storage_root / "projects" / str(project_id)
            / "snapshots" / str(backup_id) / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for dataset in manifest["datasets"]:
            dataset.pop("id", None)
        for mine in manifest["mines"]:
            mine.pop("id", None)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        db.session.get(Project, project_id).name = "旧快照项目-改名"
        db.session.get(ProjectDataset, dataset_id).display_name = "快照后改名"
        db.session.commit()

        self._restore(project_id, backup_id)

        dataset = db.session.get(ProjectDataset, dataset_id)
        self.assertIsNotNone(dataset)
        self.assertEqual(
            dataset.display_name, "快照后改名",
            "旧快照缺记录身份：不得猜测关联重建，数据集段跳过",
        )
        self.assertEqual(
            db.session.get(Project, project_id).name, "旧快照项目",
            "summary 段仍按快照恢复（限制范围≠拒绝恢复）",
        )
        payload = self._restore_event_payload(project_id)
        self.assertIn("datasets", payload["skipped_sections"])


class TestDeleteServiceDirect(ProjectManageBase):
    """服务层直接调用：ProjectDeleteNotAllowed 异常类型契约。"""

    def test_service_raises_typed_error_for_active_project(self):
        self.login_as_admin()
        project_id = self._create_project("服务层类型")
        with self.assertRaises(ProjectDeleteNotAllowed):
            delete_project(project_id)

    def test_storage_restored_when_commit_fails(self):
        """P2-2（2026-10-24 审查）：目录先移入 trash、DB 软删提交失败时必须把
        目录移回原位——否则项目在列表里活着、数据却进了回收站（半删除态）。"""
        from unittest.mock import patch

        self.login_as_admin()
        project_id = self._create_project("提交失败回滚")
        project_dir = self._make_project_dir(project_id)
        self._archive(project_id)

        with patch(
            "applications.project_hub.service.db.session.commit",
            side_effect=RuntimeError("db down"),
        ):
            response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 500)

        self.assertTrue(project_dir.exists(), "目录必须回到 projects/ 原位")
        self.assertTrue((project_dir / "marker.txt").is_file())
        self.assertEqual(
            list((self.storage_root / "trash").glob(f"{project_id}_*")),
            [], "trash 不得残留半删除目录",
        )
        self.assertIsNone(db.session.get(Project, project_id).deleted_at)


class TestTimestampSerialization(ProjectManageBase):
    """快照/导出/最新解译时间必须显式带 UTC 标记，与 timeline 口径一致。

    回归背景：DB 为 UTC，此前 backups/exports 经 marshmallow naive dump 丢失时区，
    前端按本地时间解析导致显示早 8 小时（docs/gui-full-audit-20260927.md #1）。
    """

    def test_backup_create_time_matches_timeline_instant(self):
        self.login_as_admin()
        project_id = self._create_project("时间口径回归")
        self._make_project_dir(project_id)
        response = self.client.post(
            f"/api/projects/{project_id}/backups",
            json={"scope": "metadata_index"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        backup_id = response.get_json()["data"]["id"]

        backups = self.client.get(f"/api/projects/{project_id}/backups").get_json()["data"]["items"]
        item = next(b for b in backups if b["id"] == backup_id)
        self.assertTrue(str(item["create_time"]).endswith("Z"), item["create_time"])

        timeline_body = self.client.get(f"/api/projects/{project_id}/timeline").get_json()["data"]
        timeline = timeline_body.get("items", timeline_body) if isinstance(timeline_body, dict) else timeline_body
        snapshot_event = next(a for a in timeline if a.get("action_code") == "SNAPSHOT_CREATED")
        from datetime import datetime

        backup_at = datetime.fromisoformat(str(item["create_time"]).replace("Z", "+00:00"))
        event_at = datetime.fromisoformat(str(snapshot_event["created_at"]).replace("Z", "+00:00"))
        # 两条记录分别取 utcnow()，允许毫秒级写入间隔，但不允许 8 小时口径漂移
        self.assertLess(abs((backup_at - event_at).total_seconds()), 5)

    def test_export_and_inference_schemas_append_z(self):
        from datetime import datetime

        from applications.schemas.project import (
            ProjectExportRecordSchema,
            ProjectLatestInferenceSchema,
        )

        class _ExportRow:
            id = 1
            format = "geojson"
            status = "succeeded"
            file_path = "projects/9/exports/1/artifact.geojson"
            create_time = datetime(2026, 9, 25, 14, 53, 19)
            update_time = datetime(2026, 9, 25, 14, 53, 20)

        dumped = ProjectExportRecordSchema().dump(_ExportRow())
        self.assertEqual(dumped["create_time"], "2026-09-25T14:53:19Z")
        self.assertEqual(dumped["update_time"], "2026-09-25T14:53:20Z")

        class _InferenceRow:
            job_id = "job-1"
            status = "succeeded"
            create_time = datetime(2026, 9, 25, 14, 53, 19)

        self.assertEqual(
            ProjectLatestInferenceSchema().dump(_InferenceRow())["create_time"],
            "2026-09-25T14:53:19Z",
        )

    def test_utc_datetime_keeps_aware_non_utc_offset_without_z(self):
        # 数据流遍 Low：感知型非 UTC 值须保留原偏移，不得叠加成非法 "...+08:00Z"
        from datetime import datetime, timezone, timedelta

        from applications.schemas.project import ProjectLatestInferenceSchema

        class _Row:
            job_id = "job-2"
            status = "running"
            create_time = datetime(2026, 9, 22, 20, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        dumped = ProjectLatestInferenceSchema().dump(_Row())["create_time"]
        self.assertEqual(dumped, "2026-09-22T20:00:00+08:00")
        self.assertFalse(dumped.endswith("Z"))


if __name__ == "__main__":
    unittest.main()
