"""V1 实现：不真正推送，只把事件写入 notifications 表等前端轮询。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.services.notifier.base import Notifier

logger = logging.getLogger(__name__)


class PollNotifier(Notifier):
    name = "poll"

    def __init__(self, db: Session) -> None:
        self._db = db

    def send(self, user_id: int | None, event: str, payload: dict[str, Any] | None = None,
             order_id: int | None = None) -> None:
        record = Notification(
            user_id=user_id,
            order_id=order_id,
            event=event,
            payload=payload,
            status="pending",
        )
        self._db.add(record)
        self._db.flush()
        logger.info("通知事件已记录（轮询模式）：user=%s event=%s", user_id, event)
