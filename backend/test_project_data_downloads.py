"""数据管理下载闭环测试：导出制品与配置快照清单的受控下载。

覆盖：完成态下载回环、未知/跨项目记录 404、未完成态 409、
快照清单内容与 manifest_path 落盘一致。
"""

import json
from pathlib import Path

from applications.models.project import ProjectBackupRecord, ProjectExportRecord
from applications.extensions import db

from test_project_api import TestProjectAPI


class TestProjectDataDownloads(TestProjectAPI):
    # 注：geojson/shp 依赖项目空间资源（无矿山资源时返回"暂无项目数据"），
    # 下载机制与格式无关，统一用 xlsx（台账对任意项目可导出）覆盖。
    def _create_completed_export(self, project_id, fmt="xlsx"):
        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": fmt},
        )
        self.assertEqual(response.status_code, 200)
        record = ProjectExportRecord.query.filter_by(
            project_id=project_id, format=fmt
        ).one()
        self.assertEqual(record.status, "completed")
        return record

    def _add_pending_export(self, project_id, fmt="geojson"):
        record = ProjectExportRecord(
            project_id=project_id,
            format=fmt,
            status="pending",
        )
        db.session.add(record)
        db.session.commit()
        return record

    def test_export_artifact_download_roundtrip(self):
        self.login_as_admin()
        project_id = self._create_project("导出下载回环项目")
        record = self._create_completed_export(project_id)

        response = self.client.get(
            f"/api/projects/{project_id}/exports/{record.id}/artifact"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data) > 0)
        artifact_name = Path(record.file_path).name
        self.assertIn(
            f"attachment; filename={artifact_name}",
            response.headers.get("Content-Disposition", ""),
        )

    def test_export_artifact_download_rejects_unknown_record(self):
        self.login_as_admin()
        project_id = self._create_project("导出未知记录项目")
        response = self.client.get(
            f"/api/projects/{project_id}/exports/999999/artifact"
        )
        self.assertEqual(response.status_code, 404)

    def test_export_artifact_download_rejects_project_mismatch(self):
        self.login_as_admin()
        project_id = self._create_project("导出归属项目")
        other_project_id = self._create_project("导出越权项目")
        record = self._create_completed_export(project_id)

        response = self.client.get(
            f"/api/projects/{other_project_id}/exports/{record.id}/artifact"
        )
        self.assertEqual(response.status_code, 404)

    def test_export_artifact_download_rejects_pending_record(self):
        self.login_as_admin()
        project_id = self._create_project("导出未完成项目")
        record = self._add_pending_export(project_id)

        response = self.client.get(
            f"/api/projects/{project_id}/exports/{record.id}/artifact"
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("尚未完成", self._json(response)["msg"])

    def test_backup_manifest_download_roundtrip(self):
        self.login_as_admin()
        project_id = self._create_project("快照清单下载项目")
        backup = self._add_backup_with_manifest(
            project_id, status="completed", restorable=True, marker="download-roundtrip"
        )

        response = self.client.get(
            f"/api/projects/{project_id}/backups/{backup.id}/manifest"
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(payload["marker"], "download-roundtrip")
        self.assertIn(
            f"attachment; filename=snapshot-{backup.id}-manifest.json",
            response.headers.get("Content-Disposition", ""),
        )

    def test_backup_manifest_download_rejects_project_mismatch(self):
        self.login_as_admin()
        project_id = self._create_project("快照归属项目")
        other_project_id = self._create_project("快照越权项目")
        backup = self._add_backup_with_manifest(
            project_id, status="completed", restorable=True, marker="mismatch"
        )

        response = self.client.get(
            f"/api/projects/{other_project_id}/backups/{backup.id}/manifest"
        )
        self.assertEqual(response.status_code, 404)
