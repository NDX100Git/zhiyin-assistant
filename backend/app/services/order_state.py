"""订单状态机：合法流转校验、状态变更、操作日志与通知事件。

铁律：状态只能由服务端推进；每次变更必须写入 print_logs。
合法流转见 TRANSITIONS，非法流转直接抛业务异常（中文提示）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.print_log import PrintLog
from app.services.notifier import get_notifier
from app.utils.exceptions import BusinessError

STATUS_PENDING_PAY = "pending_pay"
STATUS_QUEUING = "queuing"
STATUS_PRINTING = "printing"
STATUS_READY = "ready"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_ABNORMAL = "abnormal"

STATUS_LABELS = {
    STATUS_PENDING_PAY: "待支付",
    STATUS_QUEUING: "排队中",
    STATUS_PRINTING: "打印中",
    STATUS_READY: "待取件",
    STATUS_COMPLETED: "已完成",
    STATUS_CANCELLED: "已取消",
    STATUS_ABNORMAL: "异常",
}

# 允许的状态流转。修改规则只改这一张表
TRANSITIONS: dict[str, set[str]] = {
    STATUS_PENDING_PAY: {STATUS_QUEUING, STATUS_CANCELLED},
    STATUS_QUEUING: {STATUS_PRINTING, STATUS_CANCELLED, STATUS_ABNORMAL},
    STATUS_PRINTING: {STATUS_READY, STATUS_ABNORMAL},
    STATUS_READY: {STATUS_COMPLETED, STATUS_ABNORMAL},
    STATUS_ABNORMAL: {STATUS_QUEUING, STATUS_CANCELLED},
    STATUS_COMPLETED: set(),
    STATUS_CANCELLED: set(),
}

# 学生可以自己取消的状态
STUDENT_CANCELLABLE = {STATUS_PENDING_PAY, STATUS_QUEUING}

# 老师可以关闭的状态（异常单只能由老师关闭）
ADMIN_CANCELLABLE = {STATUS_ABNORMAL}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, set())


def assert_transition(from_status: str, to_status: str) -> None:
    """校验流转合法性，非法时抛业务异常。"""
    if can_transition(from_status, to_status):
        return
    raise BusinessError(
        f"订单当前状态为「{status_label(from_status)}」，不能变更为「{status_label(to_status)}」",
        code=40900,
    )


def record_log(
    db: Session,
    order: Order,
    action: str,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    operator: str | None = None,
) -> PrintLog:
    """写入一条订单操作日志。"""
    log = PrintLog(
        order_id=order.id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        note=note,
        operator=operator,
    )
    db.add(log)
    return log


def change_status(
    db: Session,
    order: Order,
    to_status: str,
    operator: str | None = None,
    note: str | None = None,
) -> Order:
    """推进订单状态：校验 -> 更新 -> 写日志 -> 记录通知事件。"""
    from_status = order.status
    assert_transition(from_status, to_status)

    order.status = to_status
    now = datetime.now()  # 服务端时间
    if to_status == STATUS_READY:
        order.printed_at = now
    elif to_status == STATUS_COMPLETED:
        order.picked_at = now

    record_log(db, order, "state_change", from_status, to_status, note, operator)

    get_notifier(db).send(
        user_id=order.user_id,
        event=f"order_{to_status}",
        payload={
            "order_no": order.order_no,
            "pickup_code": order.pickup_code,
            "status": to_status,
            "status_label": status_label(to_status),
        },
        order_id=order.id,
    )
    db.flush()
    return order
