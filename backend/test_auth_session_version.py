# -*- coding: utf-8 -*-
"""会话版本与会话失效（2026-11-14 审查问题20）。

启动初始化不再覆盖已有账号密码（环境变量只用于首次建号——密码
权威在数据库，页面改密在重启后仍有效）；用户表 session_version
变化（改密递增）或账号停用后，既有 cookie 会话必须立即失效。
"""
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.admin_user import AdminUser

from test_project_api import TestProjectAPI


class TestAuthSessionVersion(TestProjectAPI):
    def test_password_change_survives_restart_resync(self):
        # 改密后"重启"（启动初始化再执行）不得把密码回退到环境变量旧值
        self.login_as_admin()
        change = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "Secret123!", "new_password": "newpass123"},
        )
        self.assertEqual(change.status_code, 200)

        from applications.auth.service import sync_admin_from_env, verify_admin_password

        sync_admin_from_env()
        self.assertFalse(verify_admin_password("admin", "Secret123!"))
        self.assertTrue(verify_admin_password("admin", "newpass123"))

    def test_old_session_invalid_after_password_change(self):
        self.login_as_admin()
        self.assertEqual(self.client.get("/api/projects").status_code, 200)

        change = self.client.post(
            "/api/auth/change-password",
            json={"old_password": "Secret123!", "new_password": "newpass123"},
        )
        self.assertEqual(change.status_code, 200)

        # 旧 cookie 会话已失效；用新口令重新登录后恢复
        stale = self.client.get("/api/projects")
        self.assertEqual(stale.status_code, 401)

        relogin = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "newpass123"},
        )
        self.assertEqual(relogin.status_code, 200)
        self.assertEqual(self.client.get("/api/projects").status_code, 200)

    def test_deactivated_account_session_invalid(self):
        self.login_as_admin()
        self.assertEqual(self.client.get("/api/projects").status_code, 200)

        user = AdminUser.query.filter_by(username="admin").one()
        user.is_active = False
        db.session.commit()

        self.assertEqual(self.client.get("/api/projects").status_code, 401)

    def test_env_password_seeds_first_creation_only(self):
        # 首次建号仍以环境变量为准（部署语义钉）
        from applications.auth.service import verify_admin_password

        self.login_as_admin()
        self.assertTrue(verify_admin_password("admin", "Secret123!"))


if __name__ == "__main__":
    unittest.main()
