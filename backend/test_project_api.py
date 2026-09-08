import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.exc import OperationalError

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectBackupRecord, ProjectDataset, ProjectExportRecord
from applications.models.project_spatial import ProjectSpatialResource


class TestProjectAPI(unittest.TestCase):
    def setUp(self):
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="project-api-")
        self.addCleanup(self.temp_dir.cleanup)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        self.addCleanup(self._restore_storage_root)
        self.app = create_app("testing")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

    def _restore_storage_root(self):
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root

    def _json(self, response):
        return json.loads(response.data.decode("utf-8"))

    def _timeline_item(self, project_id, event_type):
        response = self.client.get(f"/api/projects/{project_id}/timeline")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        items = self._json(response)["data"]["items"]
        return next(item for item in items if item["event_type"] == event_type)

    def _assert_machine_readable_timeline_item(
        self,
        project_id,
        event_type,
        action_code,
        target_type,
        target_id,
    ):
        item = self._timeline_item(project_id, event_type)
        self.assertEqual(item["action_code"], action_code)
        self.assertEqual(item["actor"], "admin")
        self.assertEqual(
            item["target"],
            {"type": target_type, "id": str(target_id)},
        )
        self.assertEqual(item["result"], "success")
        self.assertIsInstance(item["payload"], dict)
        self.assertEqual(item["created_at"], item["timestamp"])
        return item

    def _create_incoming_tif(self, filename):
        storage_key = f"incoming/{filename}"
        source_path = self.storage_root / storage_key
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"II*\x00")
        return storage_key

    def sync_admin(self, password="Secret123!"):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = password
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = password
        from applications.auth.service import sync_admin_from_env

        return sync_admin_from_env()

    def login_as_admin(self, password="Secret123!"):
        self.sync_admin(password)
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return response

    def _create_project(self, name="回归测试项目"):
        response = self.client.post(
            "/api/projects",
            json={
                "name": name,
                "region": "昆明市",
                "manager": "测试管理员",
                "monitor_start_year": 2024,
                "monitor_end_year": 2025,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        return body["data"]["id"]

    def _add_legacy_dataset(self, project_id, mine_fid=101, file_path=None):
        dataset = ProjectDataset(
            project_id=project_id,
            dataset_kind="imagery",
            display_name="历史绝对路径影像",
            file_path=file_path or r"C:\\legacy-private\\imagery.tif",
            source_format="tif",
            mine_fid=mine_fid,
            year_start=2024,
            year_end=2024,
            slice_config_json="{}",
        )
        db.session.add(dataset)
        db.session.commit()
        return dataset

    def _export_root(self, project_id, export_id):
        return self.storage_root / "projects" / str(project_id) / "exports" / str(export_id)

    def _read_export_manifest(self, project_id, export_id):
        with (self._export_root(project_id, export_id) / "manifest.json").open(
            "r", encoding="utf-8"
        ) as handle:
            return json.load(handle)

    def _create_project_sibling_link_or_skip(self, link_path, sibling_project_id, sentinel_name):
        sentinel_root = self.storage_root / "projects" / str(sibling_project_id) / sentinel_name
        sentinel_root.mkdir(parents=True, exist_ok=True)
        sentinel = sentinel_root / "sentinel.txt"
        sentinel.write_text("sibling sentinel", encoding="utf-8")
        link_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(sentinel_root, link_path, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"当前 Windows 权限不支持目录 symlink: {exc}")
        self.assertTrue(link_path.is_symlink())
        return sentinel

    def _create_project_sibling_file_link_or_skip(self, link_path, sibling_project_id, sentinel_name):
        sentinel_root = self.storage_root / "projects" / str(sibling_project_id) / sentinel_name
        sentinel_root.mkdir(parents=True, exist_ok=True)
        sentinel = sentinel_root / "sentinel.json"
        sentinel.write_bytes(b"sibling manifest sentinel")
        link_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(sentinel, link_path)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"当前 Windows 权限不支持文件 symlink: {exc}")
        self.assertTrue(link_path.is_symlink())
        return sentinel

    def _add_backup_with_manifest(self, project_id, status, restorable, marker):
        backup = ProjectBackupRecord(
            project_id=project_id,
            scope="metadata_index",
            manifest_path="pending",
            status=status,
            restorable=restorable,
        )
        db.session.add(backup)
        db.session.flush()
        backup.manifest_path = f"projects/{project_id}/snapshots/{backup.id}/manifest.json"
        manifest_path = self.storage_root / Path(backup.manifest_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "marker": marker,
                    "summary": {"name": "不应恢复此快照", "status": "archived"},
                    "mines": [],
                    "datasets": [],
                    "exports": [],
                    "activities": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        db.session.commit()
        return backup

    def test_dataset_registration_requires_safe_incoming_tiff_storage_key(self):
        self.login_as_admin()
        response = self.client.post(
            "/api/projects",
            json={
                "name": "安全数据集项目",
                "monitor_start_year": 2024,
                "monitor_end_year": 2025,
            },
        )
        project_id = self._json(response)["data"]["id"]

        legacy_file_path = self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": "旧路径影像",
                "file_path": "incoming/legacy.tif",
            },
        )
        self.assertEqual(legacy_file_path.status_code, 422)
        legacy_file_path_body = self._json(legacy_file_path)
        self.assertEqual(legacy_file_path_body["code"], 1)
        self.assertEqual(legacy_file_path_body["msg"], "请使用 storage_key 登记数据文件")

        invalid_payloads = [
            {"storage_key": str(self.storage_root / "incoming" / "absolute.tif")},
            {"storage_key": "../incoming/escape.tif"},
            {"storage_key": "uploads/not-incoming.tif"},
            {"storage_key": "incoming/not-supported.png"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/api/projects/{project_id}/datasets",
                    json={
                        "dataset_kind": "imagery",
                        "display_name": "非法影像",
                        **payload,
                    },
                )
                self.assertEqual(response.status_code, 422)

        storage_key = self._create_incoming_tif("valid-imagery.tif")
        response = self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": "合法影像",
                "storage_key": storage_key,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        registration = body["data"]
        self.assertEqual(registration["asset_id"], f"imagery:{registration['id']}")
        self.assertEqual(registration["status"], "registered")
        self.assertNotIn("file_path", registration)

    def test_dataset_registration_accepts_supported_raster_formats(self):
        self.login_as_admin()
        response = self.client.post(
            "/api/projects",
            json={"name": "多格式影像项目", "monitor_start_year": 2024, "monitor_end_year": 2025},
        )
        project_id = self._json(response)["data"]["id"]

        for filename in ("multi-format.img", "multi-format.jp2", "multi-format.TIFF"):
            with self.subTest(filename=filename):
                storage_key = self._create_incoming_tif(filename)
                response = self.client.post(
                    f"/api/projects/{project_id}/datasets",
                    json={
                        "dataset_kind": "imagery",
                        "display_name": f"多格式影像 {filename}",
                        "storage_key": storage_key,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._json(response)["code"], 0)

        rejected = self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": "不支持格式",
                "storage_key": self._create_incoming_tif("multi-format.png"),
            },
        )
        self.assertEqual(rejected.status_code, 422)

    def test_export_and_snapshot_reject_unsupported_request_fields(self):
        self.login_as_admin()
        project_id = self._create_project("请求字段白名单项目")

        cases = (
            (
                f"/api/projects/{project_id}/exports",
                {"format": "csv", "file_path": "D:/private/export.csv"},
                "导出请求包含不支持字段",
            ),
            (
                f"/api/projects/{project_id}/backups",
                {"scope": "metadata_index", "file_path": "D:/private/snapshot.json"},
                "项目配置快照请求包含不支持字段",
            ),
            (
                f"/api/projects/{project_id}/backups",
                {"scope": "full"},
                "当前仅支持 metadata_index 项目配置快照",
            ),
        )
        for path, payload, message in cases:
            with self.subTest(path=path, payload=payload):
                response = self.client.post(path, json=payload)
                body = self._json(response)
                self.assertEqual(response.status_code, 422)
                self.assertFalse(body["success"])
                self.assertEqual(body["msg"], message)

    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.login_as_admin()
        response = self.client.post(
            "/api/projects",
            json={
                "name": "大理一期监测",
                "region": "大理州",
                "manager": "张三",
                "remark": "一期项目",
                "monitor_start_year": 2024,
                "monitor_end_year": 2025,
            },
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        project_id = body["data"]["id"]

        response = self.client.get("/api/projects")
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(len(body["data"]["items"]), 1)
        self.assertEqual(body["data"]["items"][0]["mine_count"], 0)
        self.assertEqual(body["data"]["items"][0]["dataset_count"], 0)

        response = self.client.put(
            f"/api/projects/{project_id}/mines",
            json={
                "mines": [
                    {
                        "mine_fid": 101,
                        "mine_name_snapshot": "大理矿山A",
                        "city_snapshot": "大理市",
                        "area_snapshot": 12.5,
                        "status_snapshot": "待治理",
                        "sort_order": 1,
                    },
                    {
                        "mine_fid": 102,
                        "mine_name_snapshot": "大理矿山B",
                        "city_snapshot": "祥云县",
                        "area_snapshot": 9.8,
                        "status_snapshot": "已治理",
                        "sort_order": 2,
                    },
                ]
            },
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["mine_count"], 2)

        response = self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": "2024年春季影像",
                "storage_key": self._create_incoming_tif("workflow-imagery-2024.tif"),
                "mine_fid": 101,
                "year_start": 2024,
                "year_end": 2024,
                "slice_config_json": {"slice_size": 1024, "padding": 64},
            },
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        dataset_id = body["data"]["id"]

        response = self.client.get(f"/api/projects/{project_id}")
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        detail = body["data"]
        self.assertEqual(detail["summary"]["id"], project_id)
        self.assertEqual(detail["summary"]["mine_count"], 2)
        self.assertEqual(detail["summary"]["dataset_count"], 1)
        self.assertEqual(len(detail["mines"]), 2)
        self.assertEqual(len(detail["datasets"]), 1)
        self.assertEqual(detail["datasets"][0]["id"], dataset_id)
        self.assertIn("recent_activity", detail)
        self.assertGreaterEqual(len(detail["recent_activity"]), 3)

        response = self.client.get(f"/api/projects/{project_id}/timeline")
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        timeline = body["data"]["items"]
        self.assertGreaterEqual(len(timeline), 3)
        self.assertIn("dataset_created", {item["event_type"] for item in timeline})

        response = self.client.post(f"/api/projects/{project_id}/archive", json={})
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["status"], "archived")
        response = self.client.get(f"/api/projects/{project_id}")
        self.assertEqual(self._json(response)["data"]["summary"]["status"], "archived")

        response = self.client.post(f"/api/projects/{project_id}/restore", json={})
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["status"], "active")
        response = self.client.get(f"/api/projects/{project_id}")
        self.assertEqual(self._json(response)["data"]["summary"]["status"], "active")

    def test_timeline_normalizes_project_data_export_and_snapshot_activities(self):
        self.login_as_admin()
        project_id = self._create_project("审计活动项目")

        response = self.client.patch(
            f"/api/projects/{project_id}",
            json={"remark": "更新后的审计说明"},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": "审计影像",
                "storage_key": self._create_incoming_tif("audit-imagery.tif"),
            },
        )
        self.assertEqual(response.status_code, 200)
        dataset_id = self._json(response)["data"]["id"]

        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "csv"},
        )
        self.assertEqual(response.status_code, 200)
        export_id = self._json(response)["data"]["id"]

        response = self.client.post(
            f"/api/projects/{project_id}/backups",
            json={"scope": "metadata_index"},
        )
        self.assertEqual(response.status_code, 200)
        backup_id = self._json(response)["data"]["id"]

        for event_type, action_code, target_type, target_id in (
            ("project_created", "PROJECT_CREATED", "project", project_id),
            ("project_updated", "PROJECT_UPDATED", "project", project_id),
            ("dataset_created", "DATASET_REGISTERED", "dataset", dataset_id),
            ("export_created", "EXPORT_CREATED", "export", export_id),
            ("backup_created", "SNAPSHOT_CREATED", "snapshot", backup_id),
        ):
            with self.subTest(event_type=event_type):
                self._assert_machine_readable_timeline_item(
                    project_id,
                    event_type,
                    action_code,
                    target_type,
                    target_id,
                )

        response = self.client.post(
            f"/api/projects/{project_id}/backups/{backup_id}/restore",
            json={},
        )
        self.assertEqual(response.status_code, 200)
        self._assert_machine_readable_timeline_item(
            project_id,
            "backup_restored",
            "SNAPSHOT_RESTORED",
            "snapshot",
            backup_id,
        )

    def test_spatial_register_route_forwards_session_actor(self):
        self.login_as_admin()
        project_id = self._create_project("空间活动操作者项目")

        with patch(
            "applications.api.project.register_basemap",
            return_value={"resource": {"id": 1}, "job": {"id": "job-1"}},
        ) as register_basemap_mock:
            response = self.client.post(
                f"/api/projects/{project_id}/spatial/basemaps",
                json={"candidate": "incoming/audit-basemap.tif"},
            )

        self.assertEqual(response.status_code, 200)
        register_basemap_mock.assert_called_once_with(
            project_id,
            "incoming/audit-basemap.tif",
            8,
            15,
            actor="admin",
        )

    def test_spatial_route_hides_unexpected_storage_error(self):
        self.login_as_admin()
        project_id = self._create_project("空间异常脱敏项目")
        private_path = self.storage_root / "private" / "candidate.tif"

        with patch(
            "applications.api.project.list_basemap_candidates",
            side_effect=PermissionError(f"无法读取 {private_path}"),
        ):
            response = self.client.get(
                f"/api/projects/{project_id}/spatial/basemap-candidates"
            )

        body = self._json(response)
        self.assertEqual(response.status_code, 500)
        self.assertFalse(body["success"])
        self.assertEqual(body["msg"], "底图候选读取失败，请检查服务日志")
        self.assertNotIn(str(self.storage_root), body["msg"])

    def test_project_update_hides_unexpected_storage_error(self):
        self.login_as_admin()
        project_id = self._create_project("项目更新异常脱敏项目")
        private_path = self.storage_root / "private" / "project.json"

        with patch(
            "applications.api.project.update_project",
            side_effect=PermissionError(f"无法写入 {private_path}"),
        ):
            response = self.client.patch(
                f"/api/projects/{project_id}", json={"name": "不会保存"}
            )

        body = self._json(response)
        self.assertEqual(response.status_code, 500)
        self.assertFalse(body["success"])
        self.assertEqual(body["msg"], "项目更新失败，请检查服务日志")
        self.assertNotIn(str(self.storage_root), body["msg"])

    def test_project_export_and_backup_restore(self):
        self.login_as_admin()
        response = self.client.post(
            "/api/projects",
            json={
                "name": "曲靖项目",
                "region": "曲靖市",
                "manager": "李四",
                "monitor_start_year": 2023,
                "monitor_end_year": 2025,
            },
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        project_id = body["data"]["id"]

        self.client.put(
            f"/api/projects/{project_id}/mines",
            json={
                "mines": [
                    {
                        "mine_fid": 201,
                        "mine_name_snapshot": "曲靖矿山A",
                        "city_snapshot": "麒麟区",
                        "area_snapshot": 4.2,
                        "status_snapshot": "待治理",
                        "sort_order": 1,
                    }
                ]
            },
        )

        response = self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": "2025年遥感影像",
                "storage_key": self._create_incoming_tif("export-imagery-2025.tif"),
                "mine_fid": 201,
                "year_start": 2024,
                "year_end": 2025,
                "slice_config_json": {},
            },
        )
        body = self._json(response)
        dataset_id = body["data"]["id"]

        mine_resource_path = f"projects/{project_id}/mines/1/mines.geojson"
        normalized_mine_path = self.storage_root / mine_resource_path
        normalized_mine_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_mine_path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]],
                            },
                            "properties": {"OBJECTID": 201, "name": "曲靖矿山A"},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        db.session.add(
            ProjectSpatialResource(
                project_id=project_id,
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path=mine_resource_path,
                normalized_path=mine_resource_path,
                source_format="geojson",
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "geojson"},
        )
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        geojson_export = body["data"]
        geojson_export_id = geojson_export["id"]
        self.assertIn("artifact_name", geojson_export)
        self.assertNotIn("file_path", geojson_export)
        self.assertNotIn("output_dir", geojson_export)
        geojson_export_root = (
            self.storage_root
            / "projects"
            / str(project_id)
            / "exports"
            / str(geojson_export_id)
        )
        self.assertEqual(Path(geojson_export["artifact_name"]).name, geojson_export["artifact_name"])
        self.assertTrue((geojson_export_root / geojson_export["artifact_name"]).is_file())
        self.assertTrue((geojson_export_root / "manifest.json").is_file())
        with (geojson_export_root / geojson_export["artifact_name"]).open("r", encoding="utf-8") as handle:
            automatic_geojson = json.load(handle)
        automatic_properties = automatic_geojson["features"][0]["properties"]
        self.assertEqual(automatic_properties["project_id"], project_id)
        self.assertEqual(automatic_properties["mine_fid"], 201)
        self.assertEqual(automatic_properties["mine_name"], "曲靖矿山A")
        self.assertEqual(automatic_properties["dataset_id"], dataset_id)
        self.assertEqual(automatic_properties["result_type"], "imagery")
        self.assertEqual(automatic_properties["year_start"], 2024)
        self.assertEqual(automatic_properties["year_end"], 2025)

        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "csv"},
        )
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        csv_export = body["data"]
        csv_export_id = csv_export["id"]
        self.assertIn("artifact_name", csv_export)
        self.assertNotIn("file_path", csv_export)
        self.assertNotIn("output_dir", csv_export)
        csv_export_root = (
            self.storage_root
            / "projects"
            / str(project_id)
            / "exports"
            / str(csv_export_id)
        )
        self.assertEqual(Path(csv_export["artifact_name"]).name, csv_export["artifact_name"])
        self.assertTrue((csv_export_root / csv_export["artifact_name"]).is_file())
        self.assertTrue((csv_export_root / "manifest.json").is_file())

        rejected_export = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "csv", "output_dir": str(Path(self.temp_dir.name) / "caller-selected")},
        )
        self.assertEqual(rejected_export.status_code, 422)
        rejected_export_body = self._json(rejected_export)
        self.assertEqual(rejected_export_body["code"], 1)
        self.assertEqual(rejected_export_body["msg"], "不支持指定服务端输出目录，请移除 output_dir")

        response = self.client.get(f"/api/projects/{project_id}/exports")
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(len(body["data"]["items"]), 2)
        for item in body["data"]["items"]:
            self.assertIn("artifact_name", item)
            self.assertNotIn("file_path", item)
            self.assertNotIn("output_dir", item)

        response = self.client.post(
            f"/api/projects/{project_id}/backups",
            json={"scope": "metadata_index"},
        )
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        backup = body["data"]
        backup_id = backup["id"]
        self.assertEqual(backup["snapshot_name"], "项目配置快照")
        self.assertNotIn("manifest_path", backup)
        self.assertTrue(
            (
                self.storage_root
                / "projects"
                / str(project_id)
                / "snapshots"
                / str(backup_id)
                / "manifest.json"
            ).is_file()
        )

        rejected_backup = self.client.post(
            f"/api/projects/{project_id}/backups",
            json={"scope": "metadata_index", "output_dir": str(Path(self.temp_dir.name) / "caller-selected")},
        )
        self.assertEqual(rejected_backup.status_code, 422)
        rejected_backup_body = self._json(rejected_backup)
        self.assertEqual(rejected_backup_body["code"], 1)
        self.assertEqual(rejected_backup_body["msg"], "不支持指定服务端输出目录，请移除 output_dir")

        response = self.client.get(f"/api/projects/{project_id}/backups")
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(len(body["data"]["items"]), 1)
        self.assertEqual(body["data"]["items"][0]["snapshot_name"], "项目配置快照")
        self.assertNotIn("manifest_path", body["data"]["items"][0])

        response = self.client.patch(
            f"/api/projects/{project_id}",
            json={"name": "曲靖项目-已改名", "remark": "restore-target"},
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["name"], "曲靖项目-已改名")

        response = self.client.post(f"/api/projects/{project_id}/archive", json={})
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["status"], "archived")

        response = self.client.post(f"/api/projects/{project_id}/backups/{backup_id}/restore", json={})
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        restored = body["data"]
        self.assertEqual(restored["project_id"], project_id)
        self.assertEqual(restored["summary"]["name"], "曲靖项目")
        self.assertEqual(restored["lifecycle_status"], "active")
        self.assertNotIn("datasets", restored)
        self.assertNotIn("exports", restored)
        self.assertNotIn("backups", restored)
        self.assertNotIn("file_path", json.dumps(restored, ensure_ascii=False))
        self.assertNotIn(str(self.storage_root), json.dumps(restored, ensure_ascii=False))

    def test_dataset_registration_rejects_normalized_or_windows_escape_storage_key(self):
        self.login_as_admin()
        project_id = self._create_project("数据集路径校验项目")
        self._create_incoming_tif("valid.tif")

        for storage_key in (
            "incoming/sub/../valid.tif",
            r"incoming\sub\..\valid.tif",
        ):
            with self.subTest(storage_key=storage_key):
                response = self.client.post(
                    f"/api/projects/{project_id}/datasets",
                    json={
                        "dataset_kind": "imagery",
                        "display_name": "越界影像",
                        "storage_key": storage_key,
                    },
                )
                self.assertEqual(response.status_code, 422)
                body = self._json(response)
                self.assertFalse(body["success"])
                self.assertEqual(body["code"], 1)

    def test_xlsx_export_builds_project_ledger_workbook(self):
        self.login_as_admin()
        project_id = self._create_project("台账导出项目")

        response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["code"], 0)
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="xlsx").one()
        artifact_path = self.storage_root / Path(record.file_path)
        self.assertTrue(artifact_path.is_file())
        self.assertEqual(artifact_path.read_bytes()[:2], b"PK")
        manifest = self._read_export_manifest(project_id, record.id)
        self.assertEqual(manifest["format"], "xlsx")
        self.assertEqual(record.status, "completed")

        from openpyxl import load_workbook

        workbook = load_workbook(artifact_path)
        header = [cell.value for cell in workbook["推理成果台账"][1]]
        self.assertIn("推理耗时(秒)", header)
        self.assertIn("总耗时(秒)", header)

    def test_csv_export_never_contains_legacy_dataset_file_path(self):
        self.login_as_admin()
        project_id = self._create_project("CSV 隐私回归项目")
        self.client.put(
            f"/api/projects/{project_id}/mines",
            json={"mines": [{"mine_fid": 101, "mine_name_snapshot": "矿山 A", "sort_order": 0}]},
        )
        legacy_dataset = self._add_legacy_dataset(project_id)

        response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["code"], 0)
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="csv").one()
        csv_content = (self.storage_root / Path(record.file_path)).read_text(encoding="utf-8-sig")
        self.assertNotIn("file_path", csv_content)
        self.assertNotIn(legacy_dataset.file_path, csv_content)

    def test_csv_export_manifest_tracks_project_dataset_with_stable_asset_id(self):
        self.login_as_admin()
        project_id = self._create_project("CSV 追溯回归项目")
        dataset = self._add_legacy_dataset(project_id)

        response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["code"], 0)
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="csv").one()
        manifest = self._read_export_manifest(project_id, record.id)
        asset_id = f"imagery:{dataset.id}"
        self.assertEqual(manifest["source_asset_ids"], [asset_id])
        self.assertEqual(manifest["source_versions"], {asset_id: 1})

    def test_geojson_export_does_not_trace_foreign_project_dataset(self):
        self.login_as_admin()
        project_id = self._create_project("本项目")
        foreign_project_id = self._create_project("其他项目")
        foreign_dataset = self._add_legacy_dataset(foreign_project_id)
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]],
            },
            "properties": {
                "project_id": foreign_project_id,
                "dataset_id": foreign_dataset.id,
                "file_path": "D:/private/foreign.tif",
                "metadata": [[{"manifest_path": "D:/private/manifest.json", "label": "保留"}]],
                "custom_label": "保留的业务属性",
            },
        }

        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "geojson", "features": [feature]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["code"], 0)
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="geojson").one()
        manifest = self._read_export_manifest(project_id, record.id)
        self.assertEqual(manifest["source_asset_ids"], [])
        self.assertEqual(manifest["source_versions"], {})
        with (self._export_root(project_id, record.id) / "artifact.geojson").open("r", encoding="utf-8") as handle:
            artifact = json.load(handle)
        artifact_properties = artifact["features"][0]["properties"]
        self.assertEqual(artifact_properties["project_id"], project_id)
        self.assertIsNone(artifact_properties["dataset_id"])
        self.assertEqual(artifact_properties["custom_label"], "保留的业务属性")
        self.assertEqual(artifact_properties["metadata"], [[{"label": "保留"}]])
        self.assertNotIn("file_path", json.dumps(artifact, ensure_ascii=False))
        self.assertNotIn("manifest_path", json.dumps(artifact, ensure_ascii=False))

    def test_restore_rejects_nonrestorable_and_incomplete_backup_records(self):
        self.login_as_admin()
        project_id = self._create_project("不可恢复快照项目")
        backups = [
            self._add_backup_with_manifest(project_id, "completed", False, "not-restorable"),
            self._add_backup_with_manifest(project_id, "failed", True, "not-completed"),
        ]

        for backup in backups:
            with self.subTest(backup_id=backup.id):
                response = self.client.post(
                    f"/api/projects/{project_id}/backups/{backup.id}/restore", json={}
                )
                self.assertEqual(response.status_code, 422)
                body = self._json(response)
                self.assertFalse(body["success"])
                self.assertEqual(body["code"], 1)

    def test_restore_rejects_manifest_belonging_to_another_backup(self):
        self.login_as_admin()
        project_id = self._create_project("快照归属校验项目")
        first_response = self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        )
        second_response = self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        )
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        first_id = self._json(first_response)["data"]["id"]
        second_id = self._json(second_response)["data"]["id"]
        first = ProjectBackupRecord.query.get(first_id)
        second = ProjectBackupRecord.query.get(second_id)
        first.manifest_path = second.manifest_path
        db.session.commit()

        response = self.client.post(f"/api/projects/{project_id}/backups/{first_id}/restore", json={})
        self.assertEqual(response.status_code, 422)
        body = self._json(response)
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], 1)

    def test_export_artifact_write_failure_cleans_only_its_export_directory(self):
        self.login_as_admin()
        project_id = self._create_project("导出写入失败项目")

        with patch(
            "applications.project_hub.service._write_csv",
            side_effect=RuntimeError("模拟 CSV 写入失败"),
        ):
            response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})

        self.assertFalse(self._json(response)["success"])
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="csv").one()
        self.assertEqual(record.status, "failed")
        self.assertFalse(self._export_root(project_id, record.id).exists())

    def test_export_write_failure_does_not_expose_storage_path(self):
        self.login_as_admin()
        project_id = self._create_project("导出错误脱敏项目")
        private_path = self.storage_root / "private" / "artifact.csv"

        with patch(
            "applications.project_hub.service._write_csv",
            side_effect=PermissionError(f"无法写入 {private_path}"),
        ):
            response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})

        body = self._json(response)
        self.assertEqual(response.status_code, 500)
        self.assertFalse(body["success"])
        self.assertEqual(body["msg"], "项目导出失败，请检查服务日志")
        self.assertNotIn(str(self.storage_root), body["msg"])

    def test_export_manifest_write_failure_cleans_only_its_export_directory(self):
        self.login_as_admin()
        project_id = self._create_project("导出清单失败项目")

        with patch(
            "applications.project_hub.service.write_json_atomic",
            side_effect=RuntimeError("模拟 manifest 写入失败"),
        ):
            response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})

        self.assertFalse(self._json(response)["success"])
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="csv").one()
        self.assertEqual(record.status, "failed")
        self.assertFalse(self._export_root(project_id, record.id).exists())

    def test_snapshot_manifest_write_failure_cleans_only_its_snapshot_directory(self):
        self.login_as_admin()
        project_id = self._create_project("快照清单失败项目")

        with patch(
            "applications.project_hub.service.write_json_atomic",
            side_effect=RuntimeError("模拟 snapshot manifest 写入失败"),
        ):
            response = self.client.post(
                f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
            )

        self.assertFalse(self._json(response)["success"])
        record = ProjectBackupRecord.query.filter_by(project_id=project_id).one()
        self.assertEqual(record.status, "failed")
        self.assertFalse(record.restorable)
        snapshot_root = self.storage_root / "projects" / str(project_id) / "snapshots" / str(record.id)
        self.assertFalse(snapshot_root.exists())

    def test_export_rejects_project_sibling_symlink_without_touching_sentinel(self):
        self.login_as_admin()
        project_id = self._create_project("导出 symlink 项目")
        sibling_project_id = self._create_project("导出 symlink 兄弟项目")
        expected_export_id = 1
        sentinel = self._create_project_sibling_link_or_skip(
            self._export_root(project_id, expected_export_id),
            sibling_project_id,
            "export-sentinel",
        )

        response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})

        self.assertFalse(self._json(response)["success"])
        record = ProjectExportRecord.query.filter_by(project_id=project_id).one()
        self.assertEqual(record.id, expected_export_id)
        self.assertEqual(record.status, "failed")
        self.assertTrue(sentinel.is_file())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "sibling sentinel")
        self.assertEqual({item.name for item in sentinel.parent.iterdir()}, {"sentinel.txt"})

    def test_backup_rejects_project_sibling_symlink_without_touching_sentinel(self):
        self.login_as_admin()
        project_id = self._create_project("快照 symlink 项目")
        sibling_project_id = self._create_project("快照 symlink 兄弟项目")
        expected_backup_id = 1
        sentinel = self._create_project_sibling_link_or_skip(
            self.storage_root / "projects" / str(project_id) / "snapshots" / str(expected_backup_id),
            sibling_project_id,
            "snapshot-sentinel",
        )

        response = self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        )

        self.assertFalse(self._json(response)["success"])
        record = ProjectBackupRecord.query.filter_by(project_id=project_id).one()
        self.assertEqual(record.id, expected_backup_id)
        self.assertEqual(record.status, "failed")
        self.assertFalse(record.restorable)
        self.assertTrue(sentinel.is_file())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "sibling sentinel")
        self.assertEqual({item.name for item in sentinel.parent.iterdir()}, {"sentinel.txt"})

    def test_export_rejects_manifest_file_symlink_without_touching_sibling_file(self):
        self.login_as_admin()
        project_id = self._create_project("导出文件 symlink 项目")
        sibling_project_id = self._create_project("导出文件 symlink 兄弟项目")
        expected_export_id = 1
        export_root = self._export_root(project_id, expected_export_id)
        sentinel = self._create_project_sibling_file_link_or_skip(
            export_root / "manifest.json",
            sibling_project_id,
            "export-manifest-sentinel",
        )
        sentinel_bytes = sentinel.read_bytes()

        response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})

        self.assertFalse(self._json(response)["success"])
        record = ProjectExportRecord.query.filter_by(project_id=project_id).one()
        self.assertEqual(record.id, expected_export_id)
        self.assertEqual(record.status, "failed")
        self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
        self.assertFalse(export_root.exists())

    def test_restore_rejects_manifest_file_symlink_without_touching_sibling_file(self):
        self.login_as_admin()
        project_id = self._create_project("恢复文件 symlink 项目")
        sibling_project_id = self._create_project("恢复文件 symlink 兄弟项目")
        created = self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        )
        self.assertEqual(created.status_code, 200)
        backup_id = self._json(created)["data"]["id"]
        own_manifest = self.storage_root / "projects" / str(project_id) / "snapshots" / str(backup_id) / "manifest.json"
        sentinel_root = self.storage_root / "projects" / str(sibling_project_id) / "restore-manifest-sentinel"
        sentinel_root.mkdir(parents=True, exist_ok=True)
        sentinel = sentinel_root / "sentinel.json"
        sentinel.write_bytes(own_manifest.read_bytes())
        own_manifest.unlink()
        try:
            os.symlink(sentinel, own_manifest)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"当前 Windows 权限不支持文件 symlink: {exc}")
        sentinel_bytes = sentinel.read_bytes()
        self.client.patch(f"/api/projects/{project_id}", json={"name": "恢复前不变名称"})

        response = self.client.post(f"/api/projects/{project_id}/backups/{backup_id}/restore", json={})

        self.assertEqual(response.status_code, 422)
        self.assertFalse(self._json(response)["success"])
        self.assertEqual(sentinel.read_bytes(), sentinel_bytes)
        self.assertEqual(Project.query.get(project_id).name, "恢复前不变名称")

    def test_project_storage_rejects_windows_reparse_metadata_for_directory_and_file(self):
        from applications.project_hub import project_storage
        from applications.project_hub.project_storage import (
            ProjectStorageValidationError,
            resolve_project_record_file,
            resolve_project_record_root,
        )

        project_id = 7
        export_id = 11
        record_root = self.storage_root / "projects" / str(project_id) / "exports" / str(export_id)
        record_root.mkdir(parents=True)
        manifest_path = record_root / "manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        original_lstat = project_storage.os.lstat
        directory_metadata = original_lstat(record_root)
        file_metadata = original_lstat(manifest_path)

        def directory_reparse_lstat(path):
            if Path(path) == record_root:
                return SimpleNamespace(
                    st_mode=directory_metadata.st_mode,
                    st_file_attributes=0x0400,
                )
            return original_lstat(path)

        with patch(
            "applications.project_hub.project_storage.os.lstat",
            side_effect=directory_reparse_lstat,
        ):
            with self.assertRaises(ProjectStorageValidationError):
                resolve_project_record_root(project_id, "exports", export_id, create=False)

        def file_reparse_lstat(path):
            if Path(path) == manifest_path:
                return SimpleNamespace(
                    st_mode=file_metadata.st_mode,
                    st_file_attributes=0x0400,
                )
            return original_lstat(path)

        with patch(
            "applications.project_hub.project_storage.os.lstat",
            side_effect=file_reparse_lstat,
        ):
            with self.assertRaises(ProjectStorageValidationError):
                resolve_project_record_file(
                    project_id,
                    "exports",
                    export_id,
                    "manifest.json",
                    require_exists=True,
                )

    def test_restore_rejects_swapped_manifest_content_without_changing_project(self):
        self.login_as_admin()
        project_id = self._create_project("快照 A 原始项目")
        first_response = self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        )
        self.assertEqual(first_response.status_code, 200)
        first_id = self._json(first_response)["data"]["id"]
        self.client.patch(f"/api/projects/{project_id}", json={"name": "快照 B 内容"})
        second_response = self.client.post(
            f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
        )
        self.assertEqual(second_response.status_code, 200)
        second_id = self._json(second_response)["data"]["id"]
        self.client.patch(f"/api/projects/{project_id}", json={"name": "恢复前项目名称"})
        first_manifest = self.storage_root / "projects" / str(project_id) / "snapshots" / str(first_id) / "manifest.json"
        second_manifest = self.storage_root / "projects" / str(project_id) / "snapshots" / str(second_id) / "manifest.json"
        first_manifest.write_bytes(second_manifest.read_bytes())

        response = self.client.post(f"/api/projects/{project_id}/backups/{first_id}/restore", json={})

        self.assertEqual(response.status_code, 422)
        self.assertFalse(self._json(response)["success"])
        project = Project.query.get(project_id)
        self.assertEqual(project.name, "恢复前项目名称")

    def test_shp_export_uses_stable_artifact_member_names(self):
        self.login_as_admin()
        project_id = self._create_project("SHP 导出项目")
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]],
            },
            "properties": {},
        }

        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "shp", "features": [feature]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["code"], 0)
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="shp").one()
        with zipfile.ZipFile(self.storage_root / Path(record.file_path)) as archive:
            members = set(archive.namelist())
        self.assertEqual(members, {"artifact.shp", "artifact.shx", "artifact.dbf", "artifact.prj"})
        self.assertFalse(any(".tmp" in member or "uuid" in member for member in members))

    def test_initial_export_commit_failure_leaves_current_session_queryable(self):
        self.login_as_admin()
        project_id = self._create_project("初始提交失败项目")

        def fail_pending_commit(connection):
            raise OperationalError("COMMIT", {}, RuntimeError("模拟 pending commit 失败"))

        event.listen(db.engine, "commit", fail_pending_commit)
        try:
            response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})
        finally:
            event.remove(db.engine, "commit", fail_pending_commit)

        self.assertFalse(self._json(response)["success"])
        self.assertEqual(ProjectExportRecord.query.filter_by(project_id=project_id).count(), 0)
        self.assertFalse(self._export_root(project_id, 1).exists())
        self.assertEqual(Project.query.filter_by(id=project_id).one().id, project_id)

    def test_initial_backup_commit_failure_leaves_no_record_or_snapshot_and_session_queryable(self):
        self.login_as_admin()
        project_id = self._create_project("初始快照提交失败项目")

        def fail_pending_commit(connection):
            raise OperationalError("COMMIT", {}, RuntimeError("模拟 backup pending commit 失败"))

        event.listen(db.engine, "commit", fail_pending_commit)
        try:
            response = self.client.post(
                f"/api/projects/{project_id}/backups", json={"scope": "metadata_index"}
            )
        finally:
            event.remove(db.engine, "commit", fail_pending_commit)

        self.assertFalse(self._json(response)["success"])
        self.assertEqual(ProjectBackupRecord.query.filter_by(project_id=project_id).count(), 0)
        snapshot_root = self.storage_root / "projects" / str(project_id) / "snapshots" / "1"
        self.assertFalse(snapshot_root.exists())
        self.assertEqual(Project.query.filter_by(id=project_id).one().id, project_id)

    def test_final_export_commit_failure_marks_record_failed_and_keeps_session_queryable(self):
        self.login_as_admin()
        project_id = self._create_project("最终提交失败项目")
        commit_count = {"value": 0}

        def fail_final_commit(connection):
            commit_count["value"] += 1
            if commit_count["value"] == 2:
                raise OperationalError("COMMIT", {}, RuntimeError("模拟 final commit 失败"))

        event.listen(db.engine, "commit", fail_final_commit)
        try:
            response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "csv"})
        finally:
            event.remove(db.engine, "commit", fail_final_commit)

        self.assertFalse(self._json(response)["success"])
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="csv").one()
        self.assertEqual(record.status, "failed")
        self.assertFalse(self._export_root(project_id, record.id).exists())
        self.assertEqual(Project.query.filter_by(id=project_id).one().id, project_id)


if __name__ == "__main__":
    unittest.main()
