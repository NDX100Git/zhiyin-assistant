"""通知抽象接口。

V1 通知靠前端轮询，但订单每次状态变更都要留一条待发消息记录，
V2 接入微信订阅消息 / APP 推送时可直接补发，无需改动业务代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Notifier(ABC):
    """通知方式抽象。"""

    name: str = "unknown"

    @abstractmethod
    def send(self, user_id: int | None, event: str, payload: dict[str, Any] | None = None,
             order_id: int | None = None) -> None:
        """发送（或记录）一条通知。"""
