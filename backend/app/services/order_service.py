"""订单核心业务逻辑：下单、查询、取消、模拟支付。

铁律：订单号与取件码由服务端生成；状态只能由服务端推进；金额一律用「分」。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_order_rules, settings
from app.models.order import Order
from app.models.payment_log import PaymentLog
from app.models.queue_session import QueueSession
from app.models.user import User
from app.services.notifier import get_notifier
from app.services.order_state import (
    STATUS_CANCELLED,
    STATUS_PENDING_PAY,
    STUDENT_CANCELLABLE,
    change_status,
    record_log,
    status_label,
)
from app.services.payment import get_payment_provider
from app.services.pricing import calc_amount_cents
from app.services.queue_service import batch_wait_info, new_order_wait_info, order_wait_info
from app.services.session_service import current_session_info, get_or_create_session
from app.services.storage import get_storage
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

def _next_daily_seq(db: Session, day: date) -> int:
    """取当日的下一个序号，订单号与取件码共用它。

    `INSERT ... ON DUPLICATE KEY UPDATE` 在 MySQL 上是一条语句完成的原子自增，
    行锁持有到事务结束，同一刻的并发请求会排队，不会读到同一个数，也不需要失败重试。
    """
    db.execute(
        text(
            "INSERT INTO order_daily_seq (day, last_seq) VALUES (:day, 1) "
            "ON DUPLICATE KEY UPDATE last_seq = last_seq + 1"
        ),
        {"day": day},
    )
    return int(
        db.execute(
            text("SELECT last_seq FROM order_daily_seq WHERE day = :day"), {"day": day}
        ).scalar_one()
    )


def build_pickup_code(seq: int, day: date) -> str:
    """取件码：日期前缀加当日序号，如 261017-0-01。

    日期必须进码。不带日期的话每天从头开始，跳天就撞；只带月日则每年同日还会撞，
    唯一索引会在明年那天直接挡住下单。

    短码是三十六进制（数字加大小写不敏感的字母），三位给到 46656 个每天每站点，
    中间插一个短横只是为了让老师念得清，不参与进制。
    """
    rules = get_order_rules()["pickup_code"]
    alphabet = str(rules["alphabet"])
    length = int(rules["length"])
    separator_after = int(rules.get("separator_after", 1))
    base = len(alphabet)

    capacity = base**length
    if seq < 1 or seq > capacity:
        raise BusinessError(
            f"今日取件码 {capacity} 个已用完，请明天再来", code=40051
        )

    digits = []
    value = seq - 1
    for _ in range(length):
        value, remainder = divmod(value, base)
        digits.append(alphabet[remainder])
    short = "".join(reversed(digits))

    if 0 < separator_after < length:
        short = f"{short[:separator_after]}-{short[separator_after:]}"
    return f"{day:%y%m%d}-{short}"


def _validate_create(payload, limits: dict) -> None:
    if not payload.file_key or not str(payload.file_key).startswith("files/"):
        raise BusinessError("文件标识无效，请重新上传", code=40041)
    if payload.print_side not in (1, 2):
        raise BusinessError("打印方式只能是单面或双面", code=40042)
    if payload.color_mode not in (0, 1):
        raise BusinessError("颜色模式只能是黑白或彩色", code=40043)
    if not 1 <= int(payload.copies) <= int(limits["max_copies"]):
        raise BusinessError(f"份数必须在 1 到 {limits['max_copies']} 之间", code=40044)
    if not 1 <= int(payload.page_count) <= int(limits["max_page_count"]):
        raise BusinessError(f"页数必须在 1 到 {limits['max_page_count']} 之间", code=40045)
    if payload.paper_size not in limits["allowed_paper_sizes"]:
        raise BusinessError(
            f"纸张规格不支持：{payload.paper_size}，可选 {'、'.join(limits['allowed_paper_sizes'])}",
            code=40046,
        )
    if payload.remark and len(payload.remark) > int(limits["max_remark_length"]):
        raise BusinessError(f"备注最多 {limits['max_remark_length']} 个字符", code=40047)


def _validate_file_freshness(file_key: str) -> None:
    """下单时校验文件还在、且没超过下单宽限期。

    文件在上传后一段时间会被清理，学生"晚上传、隔几天再下单"就会引用到已删对象。
    与其等到打印时才发现，不如在下单那一刻说清楚。
    """
    grace_hours = int(settings.order_file_grace_hours)
    modified = get_storage().stat_modified(file_key)
    if modified is None:
        raise BusinessError("文件已不存在，请重新上传", code=40053)

    reference = modified.astimezone().replace(tzinfo=None) if modified.tzinfo else modified
    uploaded_ago = datetime.now() - reference
    if uploaded_ago > timedelta(hours=grace_hours):
        raise BusinessError(
            f"文件已过期（上传超过 {grace_hours} 小时），请重新上传后再下单",
            code=40052,
        )


def create_order(db: Session, user: User, payload) -> Order:
    """下单：状态置 pending_pay，返回带订单号与取件码的订单。"""
    rules = get_order_rules()
    _validate_create(payload, rules["limits"])
    _validate_file_freshness(payload.file_key)

    # 截单不拒绝下单：仅把订单排到下一个打印时段（首页会给出提示）
    session_info = current_session_info(db)

    amount_cents = calc_amount_cents(
        payload.page_count, payload.print_side, payload.color_mode, payload.copies
    )

    now = datetime.now()  # 服务端时间
    day = now.date()
    seq = _next_daily_seq(db, day)
    order = Order(
        order_no=f"{day:%y%m%d}-{seq:03d}",
        pickup_code=build_pickup_code(seq, day),
        user_id=user.id,
        status=STATUS_PENDING_PAY,
        file_key=payload.file_key,
        file_name=payload.file_name,
        page_count=payload.page_count,
        print_side=payload.print_side,
        color_mode=payload.color_mode,
        copies=payload.copies,
        paper_size=payload.paper_size,
        amount_cents=amount_cents,
        remark=payload.remark,
        site_id=1,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError as exc:
        # 序号表与订单表不同步（手工改过数据）才会走到这里，不再盲重试
        db.rollback()
        raise BusinessError("订单号生成失败，请稍后重试", code=50020) from exc

    record_log(db, order, "state_change", None, STATUS_PENDING_PAY, "学生创建订单", "student")
    # 仅在本时段仍收单时占用容量；截单后的订单算下一个时段
    if session_info["session"] and session_info["is_open"]:
        session_row = get_or_create_session(db, day, session_info["session"])
        # 带条件的原子自增：判定与加一在同一条语句里，满员不再加，不会超卖
        db.execute(
            update(QueueSession)
            .where(
                QueueSession.id == session_row.id,
                QueueSession.used < QueueSession.capacity,
            )
            .values(used=QueueSession.used + 1)
        )

    get_notifier(db).send(
        user_id=user.id,
        event="order_created",
        payload={
            "order_no": order.order_no,
            "pickup_code": order.pickup_code,
            "status": order.status,
            "status_label": status_label(order.status),
        },
        order_id=order.id,
    )

    db.commit()
    db.refresh(order)
    logger.info("创建订单成功：%s 取件码 %s 金额 %s 分", order.order_no, order.pickup_code,
                order.amount_cents)
    return order


def get_owned_order(db: Session, user: User, order_id: int) -> Order:
    """取订单并校验归属，避免越权查看他人订单。"""
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise BusinessError("订单不存在", code=40400)
    return order


def list_orders(db: Session, user: User, status: str | None = None,
                page: int = 1, page_size: int = 20) -> dict:
    """我的订单列表。"""
    condition = [Order.user_id == user.id]
    if status:
        condition.append(Order.status == status)

    total = db.execute(select(func.count()).select_from(Order).where(*condition)).scalar_one()
    rows = list(
        db.execute(
            select(Order)
            .where(*condition)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    waits = batch_wait_info(db, rows)
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [order_to_dict(db, order, waits.get(order.id)) for order in rows],
    }


def order_to_dict(db: Session, order: Order, wait: dict | None = None) -> dict:
    """订单对外字段（不含任何敏感信息）。wait 可由调用方批量算好后传入。"""
    wait = wait or order_wait_info(db, order)
    return {
        "id": order.id,
        "order_no": order.order_no,
        "pickup_code": order.pickup_code,
        "status": order.status,
        "status_label": status_label(order.status),
        "file_key": order.file_key,
        "file_name": order.file_name,
        "page_count": order.page_count,
        "print_side": order.print_side,
        "color_mode": order.color_mode,
        "copies": order.copies,
        "paper_size": order.paper_size,
        "amount_cents": order.amount_cents,
        "paid": order.paid,
        "pay_method": order.pay_method,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "printed_at": order.printed_at.isoformat() if order.printed_at else None,
        "picked_at": order.picked_at.isoformat() if order.picked_at else None,
        "remark": order.remark,
        "cancel_reason": order.cancel_reason,
        "queue_position": wait["queue_position"],
        "estimated_minutes": wait["estimated_minutes"],
    }


def cancel_order(db: Session, user: User, order_id: int, reason: str | None = None) -> Order:
    """取消订单：仅 pending_pay / queuing 允许。"""
    order = get_owned_order(db, user, order_id)
    if order.status not in STUDENT_CANCELLABLE:
        raise BusinessError(
            f"订单当前状态为「{status_label(order.status)}」，不能取消", code=40901
        )
    order.cancel_reason = reason
    change_status(db, order, STATUS_CANCELLED, operator="student",
                  note=reason or "学生取消订单")
    db.commit()
    db.refresh(order)
    return order


def pay_order(db: Session, user: User, order_id: int) -> dict:
    """模拟支付：调用支付 Provider，成功后转 queuing（这段逻辑与支付方式无关）。"""
    order = get_owned_order(db, user, order_id)
    if order.status != STATUS_PENDING_PAY:
        raise BusinessError(
            f"订单当前状态为「{status_label(order.status)}」，不能支付", code=40902
        )

    provider = get_payment_provider()
    params = provider.create(order)
    if not provider.confirm(str(order.id)):
        raise BusinessError("支付未完成，请重试", code=40060)

    # ↓ 与具体支付方式无关的公共逻辑
    order.paid = 1
    order.pay_method = str(params.get("pay_channel") or provider.name)
    order.pay_channel = order.pay_method
    order.paid_at = datetime.now()  # 服务端时间
    db.add(
        PaymentLog(
            order_id=order.id,
            amount_cents=order.amount_cents,
            method=order.pay_method,
            operator="student",
        )
    )
    change_status(db, order, "queuing", operator="student", note="支付成功，进入队列")
    db.commit()
    db.refresh(order)

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "status_label": status_label(order.status),
        "paid": order.paid,
        "payment": params,
        "queue": order_wait_info(db, order),
    }


def home_summary(db: Session, user: User) -> dict:
    """学生端首页数据。"""
    wait = new_order_wait_info(db)
    mine = {"pending_pay": 0, "queuing": 0, "printing": 0, "ready": 0}
    rows = db.execute(
        select(Order.status, func.count())
        .where(Order.user_id == user.id)
        .group_by(Order.status)
    ).all()
    for status, count in rows:
        if status in mine:
            mine[status] = int(count)

    return {
        "waiting_count": wait["queuing_count"],
        "estimated_minutes": wait["estimated_minutes"],
        "ready_count": mine["ready"],
        "mine": mine,
        "order_session": current_session_info(db),
    }
