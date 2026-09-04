import json
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db


class TestAuthAPI(unittest.TestCase):
    def setUp(self):
        self.previous_env = {
            "ADMIN_USERNAME": os.environ.get("ADMIN_USERNAME"),
            "ADMIN_PASSWORD": os.environ.get("ADMIN_PASSWORD"),
        }
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
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _json(self, response):
        return json.loads(response.data.decode("utf-8"))

    def sync_admin(self, password="Secret123!"):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = password
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = password
        from applications.auth.service import sync_admin_from_env

        return sync_admin_from_env()

    def test_bootstrap_admin_from_environment(self):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "Secret123!"
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"

        from applications.auth.service import sync_admin_from_env, verify_admin_password

        user = sync_admin_from_env()

        self.assertEqual(user.username, "admin")
        self.assertTrue(verify_admin_password("admin", "Secret123!"))

    def test_login_logout_and_session_guard(self):
        self.sync_admin("Secret123!")

        unauthorized = self.client.get("/api/projects")
        self.assertEqual(unauthorized.status_code, 401)

        login = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Secret123!"},
        )
        self.assertEqual(login.status_code, 200)

        session_state = self.client.get("/api/auth/session")
        self.assertTrue(self._json(session_state)["data"]["authenticated"])

        logout = self.client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)

        after_logout = self.client.get("/api/projects")
        self.assertEqual(after_logout.status_code, 401)

    def test_legacy_blueprints_require_login(self):
        self.sync_admin("Secret123!")

        checks = [
            ("GET", "/api/model/list/image_restoration", None),
            ("GET", "/api/history/list", None),
            ("POST", "/api/analysis/spectral_indices", {}),
            ("POST", "/api/file/upload", {}),
        ]
        for method, path, payload in checks:
            response = self.client.open(path, method=method, json=payload)
            self.assertEqual(response.status_code, 401, msg=path)

        login = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Secret123!"},
        )
        self.assertEqual(login.status_code, 200)

        authorized = self.client.get("/api/model/list/image_restoration")
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(self._json(authorized)["code"], 0)

    def test_unknown_origin_does_not_receive_cors_headers(self):
        response = self.client.get(
            "/api/auth/session",
            headers={"Origin": "http://evil.local"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_allowed_local_origin_receives_cors_headers(self):
        response = self.client.get(
            "/api/auth/session",
            headers={"Origin": "http://localhost:3000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "http://localhost:3000")
        self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")

    def test_protected_preflight_bypasses_session_guard_but_post_does_not(self):
        headers = {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        }
        preflight = self.client.open(
            "/api/analysis/kml_roi_inference",
            method="OPTIONS",
            headers=headers,
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(
            preflight.headers.get("Access-Control-Allow-Origin"),
            "http://localhost:3000",
        )

        actual = self.client.post("/api/analysis/kml_roi_inference", json={})
        self.assertEqual(actual.status_code, 401)


if __name__ == "__main__":
    unittest.main()
