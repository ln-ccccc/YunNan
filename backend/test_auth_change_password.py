"""管理员自助改密测试：旧口令验证、新口令长度、未登录拒绝、成功路径。"""

from applications.extensions import db
from applications.models.admin_user import AdminUser

from test_project_api import TestProjectAPI


class TestChangePassword(TestProjectAPI):
    def _current_user(self):
        return AdminUser.query.filter_by(username="admin").one()

    def test_change_password_requires_login(self):
        response = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "x", "new_password": "abcdefgh"},
        )
        self.assertEqual(response.status_code, 401)

    def test_change_password_rejects_wrong_old_password(self):
        self.login_as_admin()
        response = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "wrong-old", "new_password": "newpass123"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("原口令不正确", self._json(response)["msg"])

    def test_change_password_rejects_short_new_password(self):
        self.login_as_admin()
        old_hash = self._current_user().password_hash
        response = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "Secret123!", "new_password": "short"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("至少 8 位", self._json(response)["msg"])
        self.assertEqual(self._current_user().password_hash, old_hash)

    def test_change_password_roundtrip(self):
        self.login_as_admin()
        response = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "Secret123!", "new_password": "newpass123"},
        )
        self.assertEqual(response.status_code, 200)
        user = self._current_user()
        from werkzeug.security import check_password_hash

        self.assertTrue(check_password_hash(user.password_hash, "newpass123"))
        # 旧口令已失效
        self.assertFalse(check_password_hash(user.password_hash, "Secret123!"))
