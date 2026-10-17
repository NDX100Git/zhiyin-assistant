"""支付抽象接口。

业务代码只依赖本接口：支付成功后「标记已付 + 写 payment_logs + 状态转 queuing」
这一段与支付方式无关，写在 order_service 里，不放进 Provider 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.order import Order


class PaymentProvider(ABC):
    """支付方式抽象。"""

    name: str = "unknown"

    @abstractmethod
    def create(self, order: Order) -> dict[str, Any]:
        """发起支付，返回前端所需的支付参数。"""

    @abstractmethod
    def confirm(self, order_id: str) -> bool:
        """确认支付结果（V2 由支付回调触发）。"""
