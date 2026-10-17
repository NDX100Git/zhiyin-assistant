"""通知工厂：业务代码只调用 get_notifier(db)。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.services.notifier.base import Notifier


def get_notifier(db: Session) -> Notifier:
    """按 NOTIFIER_PROVIDER 返回通知实现。"""
    provider = settings.notifier_provider.lower()
    if provider == "poll":
        from app.services.notifier.poll import PollNotifier

        return PollNotifier(db)
    if provider in {"wechat", "push"}:
        raise ValueError(f"{provider} 通知将在 V2 提供，当前请使用 NOTIFIER_PROVIDER=poll")
    raise ValueError(f"未知的通知方式: {settings.notifier_provider}")


__all__ = ["Notifier", "get_notifier"]
