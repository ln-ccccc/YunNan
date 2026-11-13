# -*- coding: utf-8 -*-
"""项目创建/更新/矿山绑定请求校验统一（2026-11-14 审查问题22）。

非 JSON、损坏 JSON、数组、标量、布尔年份、字符串年份、超长名称、反向年份
均返回稳定 4xx，不产生部分写入；可缺字段的草稿与类型错误的输入区分开。
"""
import json
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.project import Project

from test_project_api import TestProjectAPI


class TestProjectPayloadValidation(TestProjectAPI):
    def setUp(self):
        super().setUp()
        self.login_as_admin()

    def test_create_project_rejects_non_object_payloads(self):
        cases = [
            ("array", "[1, 2]", "application/json", 422),
            ("scalar", "123", "application/json", 422),
            ("string", "\"abc\"", "application/json", 422),
            ("null", "null", "application/json", 422),
            ("broken", "{", "application/json", 400),
            ("non-json", "plain text", "text/plain", 415),
        ]
        for label, body, content_type, expected in cases:
            resp = self.client.post(
                "/api/projects", data=body, content_type=content_type
            )
            self.assertLess(resp.status_code, 500, label)
            self.assertEqual(resp.status_code, expected, label)
        self.assertEqual(Project.query.count(), 0)

    def test_create_project_field_validation(self):
        cases = [
            {"name": "P", "monitor_start_year": True},
            {"name": "P", "monitor_start_year": "2019"},
            {"name": "P", "monitor_start_year": 12345},
            {"name": "x" * 256},
            {"name": "P", "monitor_start_year": 2020, "monitor_end_year": 2019},
            {"name": "P", "region": "x" * 256},
            {"name": 12345},
        ]
        for payload in cases:
            resp = self.client.post("/api/projects", json=payload)
            self.assertLess(resp.status_code, 500, payload)
            self.assertGreaterEqual(resp.status_code, 400, payload)
        self.assertEqual(Project.query.count(), 0)

        ok = self.client.post(
            "/api/projects",
            json={"name": "合法项目", "monitor_start_year": 2019},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(Project.query.count(), 1)

    def test_update_project_rejects_non_object_payloads(self):
        resp = self.client.post("/api/projects", json={"name": "P"})
        project_id = self._json(resp)["data"]["id"]

        cases = [
            ("array", "[1]", 422),
            ("scalar", "42", 422),
            ("broken", "{", 400),
        ]
        for label, body, expected in cases:
            resp = self.client.patch(
                f"/api/projects/{project_id}", data=body, content_type="application/json"
            )
            self.assertEqual(resp.status_code, expected, label)

    def test_update_project_field_validation_symmetric(self):
        resp = self.client.post(
            "/api/projects",
            json={
                "name": "P",
                "region": "云南",
                "monitor_start_year": 2019,
                "monitor_end_year": 2020,
            },
        )
        project_id = self._json(resp)["data"]["id"]

        cases = [
            {"name": ""},
            {"name": "x" * 256},
            {"monitor_start_year": True},
            {"monitor_start_year": "2019"},
            {"monitor_start_year": 2030},  # 与库中 end=2020 反向
            {"region": 12345},
        ]
        for payload in cases:
            resp = self.client.patch(f"/api/projects/{project_id}", json=payload)
            self.assertLess(resp.status_code, 500, payload)
            self.assertGreaterEqual(resp.status_code, 400, payload)

        project = db.session.get(Project, project_id)
        self.assertEqual(project.name, "P")
        self.assertEqual(project.region, "云南")
        self.assertEqual(project.monitor_start_year, 2019)
        self.assertEqual(project.monitor_end_year, 2020)

        ok = self.client.patch(
            f"/api/projects/{project_id}", json={"region": "昆明"}
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(db.session.get(Project, project_id).region, "昆明")

    def test_replace_mines_validates_payload_and_elements(self):
        resp = self.client.post("/api/projects", json={"name": "P"})
        project_id = self._json(resp)["data"]["id"]
        url = f"/api/projects/{project_id}/mines"

        cases = [
            ("array-body", "[1]", 422),
            ("mines-not-list", json.dumps({"mines": "abc"}), 400),
            ("element-not-object", json.dumps({"mines": [123]}), 400),
            ("fid-bool", json.dumps({"mines": [{"mine_fid": True}]}), 400),
            ("fid-object", json.dumps({"mines": [{"mine_fid": {"a": 1}}]}), 400),
        ]
        for label, body, expected in cases:
            resp = self.client.put(url, data=body, content_type="application/json")
            self.assertLess(resp.status_code, 500, label)
            self.assertEqual(resp.status_code, expected, label)

        too_many = {"mines": [{"mine_fid": i} for i in range(1001)]}
        resp = self.client.put(url, json=too_many)
        self.assertLess(resp.status_code, 500)
        self.assertGreaterEqual(resp.status_code, 400)

        ok = self.client.put(
            url,
            json={
                "mines": [
                    {"mine_fid": 5, "mine_name_snapshot": "五号矿"},
                    {"mine_fid": "6"},
                ]
            },
        )
        self.assertEqual(ok.status_code, 200)
        data = self._json(ok)["data"]
        self.assertEqual(data["mine_count"], 2)


if __name__ == "__main__":
    unittest.main()
