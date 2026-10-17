"""V1 实现：设备标识登录。

不做真实身份认证，仅用设备标识（或微信小程序 code）作为身份标识，
符合「不收集学号等敏感信息、仅用取件码作为取件凭据」的约束。
"""

from __future__ import annotations

from typing import Any

from app.services.auth.base import AuthProvider, UserInfo
from app.utils.exceptions import BusinessError

MAX_EXTERNAL_ID_LEN = 64


class DeviceAuthProvider(AuthProvider):
    name = "device"

    def verify(self, credentials: dict[str, Any]) -> UserInfo:
        raw = str(credentials.get("code") or credentials.get("device_id") or "").strip()
        if not raw:
            raise BusinessError("缺少设备标识，无法登录", code=40001)
        if len(raw) > MAX_EXTERNAL_ID_LEN:
            raise BusinessError("设备标识过长", code=40002)

        nickname = str(credentials.get("nickname") or "").strip() or None
        if nickname and len(nickname) > 64:
            nickname = nickname[:64]

        return UserInfo(
            external_id=raw,
            auth_provider=self.name,
            nickname=nickname,
        )
