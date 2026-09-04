import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db


class TestProjectAPI(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.temp_dir = tempfile.mkdtemp(prefix="project-api-")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _json(self, response):
        return json.loads(response.data.decode("utf-8"))

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
                "file_path": os.path.join(self.temp_dir, "imagery_2024.tif"),
                "source_format": "tif",
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
                "dataset_kind": "report",
                "display_name": "2025趋势报告",
                "file_path": os.path.join(self.temp_dir, "trend_report.csv"),
                "source_format": "csv",
                "mine_fid": 201,
                "year_start": 2024,
                "year_end": 2025,
                "slice_config_json": {},
            },
        )
        body = self._json(response)
        dataset_id = body["data"]["id"]

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]],
            },
            "properties": {
                "mine_fid": 201,
                "dataset_id": dataset_id,
                "result_type": "report",
                "year_start": 2024,
                "year_end": 2025,
            },
        }

        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={
                "format": "geojson",
                "output_dir": self.temp_dir,
                "features": [feature],
            },
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        geojson_path = body["data"]["file_path"]
        self.assertTrue(os.path.exists(geojson_path))

        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={
                "format": "csv",
                "output_dir": self.temp_dir,
            },
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        csv_path = body["data"]["file_path"]
        self.assertTrue(os.path.exists(csv_path))

        response = self.client.get(f"/api/projects/{project_id}/exports")
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        self.assertEqual(len(body["data"]["items"]), 2)

        response = self.client.post(
            f"/api/projects/{project_id}/backups",
            json={"scope": "metadata_index", "output_dir": self.temp_dir},
        )
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        backup_id = body["data"]["id"]
        manifest_path = body["data"]["manifest_path"]
        self.assertTrue(os.path.exists(manifest_path))

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
        self.assertEqual(restored["summary"]["name"], "曲靖项目")
        self.assertEqual(restored["summary"]["status"], "active")
        self.assertEqual(len(restored["datasets"]), 1)
        self.assertEqual(len(restored["exports"]), 2)


if __name__ == "__main__":
    unittest.main()
