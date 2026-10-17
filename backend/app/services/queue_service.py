"""排队位置与预计等待时间。

队列不单独建表：直接用 orders 表按 created_at ASC、status IN ('queuing','printing') 过滤。
预计等待 = 近 20 单「按页数加权」的平均耗时 × 前序订单页数；
没有历史数据时按 config/order_rules.json 里的 default_seconds_per_order 估算。
"""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import func, select
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


def _estimate_with_ratio(ratio: float | None, orders: list[Order]) -> int:
    """按前序订单估算等待分钟数；ratio 由调用方传入，避免一行一次查询。"""
    if not orders:
        return 0

    rules = _wait_rules()
    default_seconds = int(rules.get("default_seconds_per_order", 60))
    pages = [int(order.page_count) for order in orders if order.page_count]

    if ratio is not None and len(pages) == len(orders):
        seconds = ratio * sum(pages)
    else:
        seconds = default_seconds * len(orders)
    return max(1, math.ceil(seconds / 60))


def estimate_minutes(db: Session, orders: list[Order]) -> int:
    """按前序订单估算等待分钟数。"""
    return _estimate_with_ratio(per_page_seconds(db), orders)


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


def batch_wait_info(db: Session, orders: list[Order]) -> dict[int, dict]:
    """一次算好一批订单的排队位置与等待时间，返回 {订单 id: 排队信息}。

    列表接口必须走这个函数：原先每行调一次 order_wait_info，每次都要把整个队列
    读成 ORM 对象，一页 50 行就是 50 遍全队列扫描。实测一万单排队时老师端队列
    一页要 7.7 秒，学生列表 3.1 秒。
    """
    if not orders:
        return {}

    # 只取三列，不把整个队列读成 ORM 对象：一万单时读对象要一百多毫秒，
    # 读三列是十几毫秒，而列表接口的并发恰恰卡在这段时间上
    active_rows = db.execute(
        select(Order.id, Order.page_count)
        .where(Order.status.in_(ACTIVE_STATUSES))
        .order_by(Order.created_at.asc(), Order.id.asc())
    ).all()
    ratio = per_page_seconds(db)
    index = {row.id: position for position, row in enumerate(active_rows)}

    # 前缀和：前面每个位置的累计页数，避免逐行切片累加
    prefix_pages = [0] * (len(active_rows) + 1)
    for position, row in enumerate(active_rows):
        prefix_pages[position + 1] = prefix_pages[position] + int(row.page_count or 0)

    total = len(active_rows)
    result: dict[int, dict] = {}
    for order in orders:
        position = index.get(order.id)
        if position is None:
            result[order.id] = {
                "queue_position": 0,
                "estimated_minutes": 0,
                "queuing_count": total,
            }
            continue
        ahead_pages = prefix_pages[position]
        own_pages = int(order.page_count or 0)
        minutes = _minutes_for(ratio, ahead_pages + own_pages, position + 1)
        result[order.id] = {
            "queue_position": position,
            "estimated_minutes": minutes,
            "queuing_count": total,
        }
    return result


def _minutes_for(ratio: float | None, pages: int, order_count: int) -> int:
    rules = _wait_rules()
    default_seconds = int(rules.get("default_seconds_per_order", 60))
    if ratio is not None:
        seconds = ratio * pages
    else:
        seconds = default_seconds * order_count
    return max(1, math.ceil(seconds / 60))


def order_wait_info(db: Session, order: Order) -> dict:
    """订单的排队位置与预计等待时间（含自身耗时）。

    单条走聚合查询，不把队列读成对象：一万单排队时读全队列要一百多毫秒，
    而 COUNT 与 SUM 在同一份数据上只要几毫秒。
    """
    active_condition = Order.status.in_(ACTIVE_STATUSES)
    total = int(db.execute(select(func.count()).select_from(Order).where(active_condition)).scalar_one())
    if order.status not in ACTIVE_STATUSES:
        return {"queue_position": 0, "estimated_minutes": 0, "queuing_count": total}

    ahead_condition = active_condition & (
        (Order.created_at < order.created_at)
        | ((Order.created_at == order.created_at) & (Order.id < order.id))
    )
    ahead_count, ahead_pages = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Order.page_count), 0),
        ).where(ahead_condition)
    ).one()

    ratio = per_page_seconds(db)
    minutes = _minutes_for(
        ratio, int(ahead_pages) + int(order.page_count or 0), int(ahead_count) + 1
    )
    return {
        "queue_position": int(ahead_count),
        "estimated_minutes": minutes,
        "queuing_count": total,
    }


def new_order_wait_info(db: Session) -> dict:
    """新下单的预计等待（排在整个队列之后）。"""
    active = list_active_orders(db)
    return {
        "queuing_count": len(active),
        "estimated_minutes": estimate_minutes(db, active),
    }
