"""支付工厂：业务代码只调用 get_payment_provider()。"""

from __future__ import annotations

from app.config import settings
from app.services.payment.base import PaymentProvider


def get_payment_provider() -> PaymentProvider:
    """按 PAYMENT_PROVIDER 返回支付实现。"""
    provider = settings.payment_provider.lower()
    if provider == "mock":
        from app.services.payment.mock import MockPaymentProvider

        return MockPaymentProvider()
    if provider in {"wechat", "card"}:
        raise ValueError(f"{provider} 支付将在 V2 提供，当前请使用 PAYMENT_PROVIDER=mock")
    raise ValueError(f"未知的支付方式: {settings.payment_provider}")


__all__ = ["PaymentProvider", "get_payment_provider"]
