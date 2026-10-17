"""老师端账号密码登录（阶段 4）。

账号密码来自环境变量 ADMIN_USERNAME / ADMIN_PASSWORD，V1 为单账号最简实现；
V2 可换成学校统一认证或后台用户表，只需新增实现类 + 改配置。
"""

from __future__ import annotations

import hmac
from typing import Any

from app.config import settings
from app.services.auth.base import AuthProvider, UserInfo
from app.utils.exceptions import BusinessError


class AdminAuthProvider(AuthProvider):
    name = "admin"

    def verify(self, credentials: dict[str, Any]) -> UserInfo:
        username = str(credentials.get("username") or "").strip()
        password = str(credentials.get("password") or "")
        if not username or not password:
            raise BusinessError("请输入管理员账号和密码", code=40001)

        username_ok = hmac.compare_digest(username, settings.admin_username)
        password_ok = hmac.compare_digest(password, settings.admin_password)
        if not (username_ok and password_ok):
            raise BusinessError("账号或密码不正确", code=40102)

        return UserInfo(external_id=username, auth_provider=self.name)
