"""登录工厂：业务代码只调用 get_auth_provider()。"""

from __future__ import annotations

from app.config import settings
from app.services.auth.base import AuthProvider, UserInfo


def get_auth_provider() -> AuthProvider:
    """按 AUTH_PROVIDER 返回登录实现。V2 换实现时只改这一处分支 + 配置。"""
    provider = settings.auth_provider.lower()
    if provider == "device":
        from app.services.auth.device import DeviceAuthProvider

        return DeviceAuthProvider()
    if provider == "wechat":
        raise ValueError("微信登录将在 V2 提供，当前请使用 AUTH_PROVIDER=device")
    if provider == "cas":
        raise ValueError("学校统一认证将在 V2 提供，当前请使用 AUTH_PROVIDER=device")
    raise ValueError(f"未知的登录方式: {settings.auth_provider}")


def get_admin_auth_provider() -> AuthProvider:
    """老师端登录实现。V2 换统一认证时只改这里。"""
    from app.services.auth.admin import AdminAuthProvider

    return AdminAuthProvider()


__all__ = ["AuthProvider", "UserInfo", "get_admin_auth_provider", "get_auth_provider"]
