"""状态机与操作日志测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.order import Order
from app.models.print_log import PrintLog
from app.models.user import User
from app.services.order_state import (
    STATUS_QUEUING,
    assert_transition,
    can_transition,
    change_status,
    status_label,
)
from app.utils.exceptions import BusinessError


def _make_order(db_session, status: str = "pending_pay") -> Order:
    user = User(openid=f"state-{status}", auth_provider="device")
    db_session.add(user)
    db_session.flush()
    order = Order(
        order_no=f"0101-{abs(hash(status)) % 900 + 100}",
        pickup_code="A-01",
        user_id=user.id,
        status=status,
        amount_cents=150,
        page_count=10,
    )
    db_session.add(order)
    db_session.commit()
    return order


def test_合法流转():
    assert can_transition("pending_pay", "queuing")
    assert can_transition("queuing", "printing")
    assert can_transition("printing", "ready")
    assert can_transition("ready", "completed")
    assert can_transition("queuing", "cancelled")


def test_非法流转抛中文业务异常():
    assert not can_transition("completed", "queuing")
    with pytest.raises(BusinessError) as exc:
        assert_transition("completed", "queuing")
    assert "不能变更" in exc.value.message
    assert exc.value.code == 40900


def test_状态中文标签():
    assert status_label("pending_pay") == "待支付"
    assert status_label("ready") == "待取件"


def test_状态变更写入日志并记录通知事件(db_session):
    order = _make_order(db_session)

    change_status(db_session, order, STATUS_QUEUING, operator="student", note="支付成功")
    db_session.commit()

    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order.id)).scalars()
    )
    assert len(logs) == 1
    assert logs[0].from_status == "pending_pay"
    assert logs[0].to_status == STATUS_QUEUING
    assert logs[0].operator == "student"

    events = list(
        db_session.execute(
            select(Notification).where(Notification.order_id == order.id)
        ).scalars()
    )
    assert len(events) == 1
    assert events[0].event == "order_queuing"
    assert events[0].status == "pending"
