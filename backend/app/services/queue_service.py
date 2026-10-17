"""排队位置与预计等待时间。

队列不单独建表：直接用 orders 表按 created_at ASC、status IN ('queuing','printing') 过滤。
预计等待 = 近 20 单「按页数加权」的平均耗时 × 前序订单页数；
没有历史数据时按 config/order_rules.json 里的 default_seconds_per_order 估算。
"""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_order_rules
from app.models.order import Order
from app.services.order_state import STATUS_COMPLETED, STATUS_PRINTING, STATUS_QUEUING, STATUS_READY

ACTIVE_STATUSES = (STATUS_QUEUING, STATUS_PRINTING)
HISTORY_STATUSES = (STATUS_READY, STATUS_COMPLETED)


def _wait_rules() -> dict:
    return get_order_rules()["wait"]


def list_active_orders(db: Session) -> list[Order]:
    """当前队列（含打印中），按订单号排序。"""
    return list(
        db.execute(
            select(Order)
            .where(Order.status.in_(ACTIVE_STATUSES))
            .order_by(Order.created_at.asc(), Order.id.asc())
        ).scalars()
    )


def per_page_seconds(db: Session) -> float | None:
    """近 N 单的「秒/页」，用 created_at -> printed_at 作为单均耗时。"""
    limit = int(_wait_rules().get("recent_orders", 20))
    rows = list(
        db.execute(
            select(Order.page_count, Order.created_at, Order.printed_at)
            .where(Order.status.in_(HISTORY_STATUSES))
            .where(Order.printed_at.is_not(None))
            .where(Order.page_count.is_not(None))
            .where(Order.page_count > 0)
            .order_by(Order.printed_at.desc())
            .limit(limit)
        )
    )

    total_seconds = 0.0
    total_pages = 0
    for page_count, created_at, printed_at in rows:
        if not isinstance(created_at, datetime) or not isinstance(printed_at, datetime):
            continue
        seconds = (printed_at - created_at).total_seconds()
        if seconds <= 0:
            continue
        total_seconds += seconds
        total_pages += int(page_count)
    if total_pages == 0:
        return None
    return total_seconds / total_pages


def estimate_minutes(db: Session, orders: list[Order]) -> int:
    """按前序订单估算等待分钟数。"""
    if not orders:
        return 0

    rules = _wait_rules()
    default_seconds = int(rules.get("default_seconds_per_order", 60))
    ratio = per_page_seconds(db)
    pages = [int(order.page_count) for order in orders if order.page_count]

    if ratio is not None and len(pages) == len(orders):
        seconds = ratio * sum(pages)
    else:
        seconds = default_seconds * len(orders)
    return max(1, math.ceil(seconds / 60))


def queue_position(db: Session, order: Order) -> int:
    """当前订单前面还有多少单（不含自己）。"""
    if order.status not in ACTIVE_STATUSES:
        return 0
    ahead = db.execute(
        select(Order.id)
        .where(Order.status.in_(ACTIVE_STATUSES))
        .where(Order.id != order.id)
        .where(
            (Order.created_at < order.created_at)
            | ((Order.created_at == order.created_at) & (Order.id < order.id))
        )
    ).scalars()
    return len(list(ahead))


def order_wait_info(db: Session, order: Order) -> dict:
    """订单的排队位置与预计等待时间（含自身耗时）。"""
    if order.status not in ACTIVE_STATUSES:
        return {"queue_position": 0, "estimated_minutes": 0, "queuing_count": len(list_active_orders(db))}

    active = list_active_orders(db)
    ahead = [item for item in active if item.id != order.id and (
        item.created_at < order.created_at
        or (item.created_at == order.created_at and item.id < order.id)
    )]
    estimated = estimate_minutes(db, [*ahead, order])
    return {
        "queue_position": len(ahead),
        "estimated_minutes": estimated,
        "queuing_count": len(active),
    }


def new_order_wait_info(db: Session) -> dict:
    """新下单的预计等待（排在整个队列之后）。"""
    active = list_active_orders(db)
    return {
        "queuing_count": len(active),
        "estimated_minutes": estimate_minutes(db, active),
    }
