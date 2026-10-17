"""登录抽象接口。

业务代码（路由 / 用户服务）只依赖本接口，不 import 具体实现类。
token 签发与建用户属于与登录方式无关的公共逻辑，放在 app.utils.security
与 app.services.user_service，不放进 Provider。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UserInfo:
    """Provider 校验通过后返回的身份信息。"""

    external_id: str          # 设备标识 / 微信 openid / CAS 账号，落到 users.openid
    auth_provider: str        # device / wechat / cas
    nickname: str | None = None
    phone_tail: str | None = None


class AuthProvider(ABC):
    """登录方式抽象。"""

    name: str = "unknown"

    @abstractmethod
    def verify(self, credentials: dict[str, Any]) -> UserInfo:
        """校验客户端提交的凭据并返回身份信息；失败抛 BusinessError。"""
