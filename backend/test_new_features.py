import json
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db


class TestNewFeatures(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

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

    def test_model_list_requires_login(self):
        response = self.client.get("/api/model/list/registration")
        self.assertEqual(response.status_code, 401)
        body = self._json(response)
        self.assertEqual(body["code"], 401)

    def test_supported_model_lists(self):
        self.login_as_admin()
        cases = {
            "registration": "register",
            "tracking": "tracker",
            "object_detection": "detector",
        }

        for model_type, expected_model_type in cases.items():
            response = self.client.get(f"/api/model/list/{model_type}")
            self.assertEqual(response.status_code, 200)
            body = self._json(response)
            self.assertEqual(body["code"], 0)
            items = body["data"]
            self.assertTrue(items)
            self.assertTrue(all(item["model_type"] == expected_model_type for item in items))

        object_detection = self._json(self.client.get("/api/model/list/object_detection"))["data"]
        self.assertTrue(
            any(str(item.get("model_path", "")).startswith("mmrotate:") for item in object_detection)
        )

    def test_invalid_model_type(self):
        self.login_as_admin()
        response = self.client.get("/api/model/list/not_real")
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], 1)


if __name__ == "__main__":
    unittest.main()
