# -*- coding: utf-8 -*-
"""物理路径泄露出口收口（2026-11-14 审查问题21）。

原生 OS 异常消息（含物理路径）、旧项目详情的数据集路径与活动 payload、
快照 manifest 中根外绝对路径，都不得出现在浏览器响应或下载清单中。
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectDataset

from test_project_api import TestProjectAPI


class TestErrorPathLeak(TestProjectAPI):
    def test_failure_response_masks_native_os_exception_paths(self):
        from applications.api.error_responses import business_or_server_failure

        with self.app.test_request_context():
            native = business_or_server_failure(
                FileNotFoundError(2, "No such file or directory", "/project_storage/projects/1/x.tif"),
                "文件处理失败",
            )
            native_body, native_status = native
            self.assertEqual(native_status, 500)
            self.assertNotIn("/project_storage", json.dumps(native_body.get_json(), ensure_ascii=False))
            self.assertNotIn("[Errno", json.dumps(native_body.get_json(), ensure_ascii=False))

            winerror = business_or_server_failure(
                ValueError("[WinError 5] 拒绝访问。: 'D:\\project_storage\\incoming\\a.tif'"),
                "文件处理失败",
            )
            winerror_text = json.dumps(winerror[0].get_json(), ensure_ascii=False)
            self.assertNotIn("D:\\", winerror_text)
            self.assertNotIn("project_storage", winerror_text)

            plain = business_or_server_failure(ValueError("项目不存在"), "操作失败")
            self.assertEqual(plain.get_json().get("msg"), "项目不存在")

            legacy = business_or_server_failure(FileNotFoundError("文件不存在: a.tif"), "操作失败")
            self.assertEqual(legacy.get_json().get("msg"), "文件不存在: a.tif")

    def test_assemble_session_error_message_has_no_paths(self):
        from applications.common.utils import upload_sessions
        from applications.common.utils.upload_sessions import UploadSessionError

        dest = Path(self.temp_dir.name) / "staging"
        dest.mkdir(parents=True, exist_ok=True)
        self.app.config["UPLOADED_PHOTOS_DEST"] = str(dest)
        sid = "ab" * 8
        sdir = dest / ".chunks" / sid
        sdir.mkdir(parents=True)
        (sdir / "session.json").write_text(
            json.dumps(
                {
                    "upload_key": sid,
                    "filename": "a.tif",
                    "mime": "image/tiff",
                    "total_size": 8,
                    "chunk_size": 4,
                    "total_chunks": 2,
                }
            ),
            encoding="utf-8",
        )
        (sdir / "chunk_0.bin").write_bytes(b"abcd")
        (sdir / "chunk_1.bin").write_bytes(b"efgh")

        with self.app.test_request_context():
            with patch(
                "shutil.copyfileobj",
                side_effect=PermissionError(13, "Permission denied", "/project_storage/incoming/a.tif"),
            ):
                with self.assertRaises(UploadSessionError) as ctx:
                    upload_sessions.assemble_session(sid, dest / "out.tif")
        message = str(ctx.exception)
        self.assertNotIn("/project_storage", message)
        self.assertNotIn("Permission denied", message)

    def test_project_detail_masks_absolute_dataset_path_and_activity_payload(self):
        project = Project(name="泄露检查", status="active")
        db.session.add(project)
        db.session.flush()
        db.session.add(
            ProjectDataset(
                project_id=project.id,
                dataset_kind="imagery",
                display_name="根外影像",
                file_path="/elsewhere/outside/x.tif",
                source_format="tif",
            )
        )
        db.session.add(
            ProjectActivityLog(
                project_id=project.id,
                event_type="project_created",
                actor="admin",
                payload_json=json.dumps({"internal_path": "/elsewhere/y.tif"}),
            )
        )
        db.session.commit()
        self.login_as_admin()

        resp = self.client.get(f"/api/projects/{project.id}")
        self.assertEqual(resp.status_code, 200)
        body = json.dumps(resp.get_json(), ensure_ascii=False)
        self.assertNotIn("/elsewhere", body)

    def test_snapshot_manifest_masks_foreign_absolute_paths(self):
        project = Project(name="快照泄露检查", status="active")
        db.session.add(project)
        db.session.flush()
        db.session.add(
            ProjectDataset(
                project_id=project.id,
                dataset_kind="imagery",
                display_name="根外影像",
                file_path="/elsewhere/outside/x.tif",
                source_format="tif",
            )
        )
        db.session.add(
            ProjectDataset(
                project_id=project.id,
                dataset_kind="imagery",
                display_name="正常影像",
                file_path=f"projects/{project.id}/inputs/imagery/ok.tif",
                source_format="tif",
            )
        )
        db.session.commit()

        from applications.project_hub.service import create_backup

        backup = create_backup(project.id, {}, actor="admin")
        backup_id = backup["id"]

        from applications.project_hub.project_storage import resolve_project_record_file

        manifest_path = resolve_project_record_file(
            project.id, "snapshots", backup_id, "manifest.json", require_exists=True
        )
        content = manifest_path.read_text(encoding="utf-8")
        self.assertNotIn("/elsewhere", content)
        manifest = json.loads(content)
        paths = [d["file_path"] for d in manifest["datasets"]]
        self.assertIn(f"projects/{project.id}/inputs/imagery/ok.tif", paths)
        self.assertIn(None, paths)

    def test_restore_skips_unavailable_datasets(self):
        project = Project(name="恢复跳过", status="active")
        db.session.add(project)
        db.session.flush()
        for name, path in (
            ("正常影像", f"projects/{project.id}/inputs/imagery/ok.tif"),
            ("根外影像", "/elsewhere/outside/x.tif"),
        ):
            db.session.add(
                ProjectDataset(
                    project_id=project.id,
                    dataset_kind="imagery",
                    display_name=name,
                    file_path=path,
                    source_format="tif",
                )
            )
        db.session.commit()

        from applications.project_hub.service import create_backup, restore_backup

        backup = create_backup(project.id, {}, actor="admin")
        restore_backup(project.id, backup["id"], actor="admin")
        remaining = ProjectDataset.query.filter_by(project_id=project.id).all()

        self.assertEqual(
            sorted(d.display_name for d in remaining),
            ["正常影像"],
        )


if __name__ == "__main__":
    unittest.main()
