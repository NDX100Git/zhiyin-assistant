"""V1 实现：模拟支付。

不接任何真实支付渠道，直接返回成功；取件时老师核销是兜底收款路径。
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.order import Order
from app.services.payment.base import PaymentProvider

logger = logging.getLogger(__name__)


class MockPaymentProvider(PaymentProvider):
    name = "mock"

    def create(self, order: Order) -> dict[str, Any]:
        logger.info("模拟支付下单：order_no=%s amount_cents=%s", order.order_no, order.amount_cents)
        return {
            "provider": self.name,
            "pay_channel": "mock",
            "amount_cents": order.amount_cents,
            "mock": True,
        }

    def confirm(self, order_id: str) -> bool:
        logger.info("模拟支付确认：order_id=%s", order_id)
        return True
