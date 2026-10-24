# -*- coding: utf-8 -*-
"""/api/health 容器深层探针（2026-10-24 审查 P2-12）：DB SELECT 1 + 存储根可写，
免登录；任一失败 503，正文不含路径/SQL/连接串。"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from test_project_api import TestProjectAPI


class TestHealthEndpoint(TestProjectAPI):
    def test_healthy_without_login(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["checks"], {"database": "ok", "storage": "ok"})
        # 探针文件不留残迹
        self.assertEqual(list(self.storage_root.glob(".health_probe")), [])

    def test_database_failure_returns_503_with_type_only(self):
        with patch("applications.api.system.db") as fake_db:
            fake_db.session.execute.side_effect = RuntimeError("db down")
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        body = response.get_json()
        self.assertFalse(body["success"])
        # 只暴露异常类型名，不携带原始报错（可能含 DSN/路径）
        self.assertEqual(body["checks"]["database"], "error:RuntimeError")
        self.assertEqual(body["checks"]["storage"], "ok")
        self.assertNotIn("db down", response.get_data(as_text=True))

    def test_storage_failure_returns_503(self):
        with patch(
            "applications.project_hub.spatial_storage.get_storage_root",
            side_effect=OSError("disk full"),
        ):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        body = response.get_json()
        self.assertEqual(body["checks"]["storage"], "error:OSError")
        self.assertEqual(body["checks"]["database"], "ok")
        self.assertNotIn("disk full", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
