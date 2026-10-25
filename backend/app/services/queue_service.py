"""排队位置与预计等待时间。

队列不单独建表：直接用 orders 表按「有效排队时间」升序过滤，
有效排队时间 = COALESCE(requeue_at, created_at)。换文件后 requeue_at 被写成换文件那一刻，
订单自然排到队尾，同时不改订单号与创建时间。
预计等待 = 近 20 单「按页数加权」的平均耗时 × 前序订单页数；
没有历史数据时按 config/order_rules.json 里的 default_seconds_per_order 估算。

页数口径：排队等待按「实际打印张数」算，即 `orders.print_page_count`
（Σ 每份文件页数 × 该份份数）；老库回填前该列为 0/NULL 时回落 `orders.page_count`。
`orders.page_count` 仍是文档页数，对外字段不变。
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime
from functools import lru_cache

from sqlalchemy import case, func, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from app.config import get_order_rules
from app.models.order import Order
from app.services import queue_snapshot
from app.services.order_state import STATUS_COMPLETED, STATUS_PRINTING, STATUS_QUEUING, STATUS_READY
from app.services.queue_snapshot import QueueSnapshot

ACTIVE_STATUSES = (STATUS_QUEUING, STATUS_PRINTING)
HISTORY_STATUSES = (STATUS_READY, STATUS_COMPLETED)

# 重建快照的互斥锁：同一刻只让一个请求扫全队
_build_lock = threading.Lock()


def _wait_rules() -> dict:
    return get_order_rules()["wait"]


def queue_time_expr():
    """有效排队时间：换过文件的单按重新排队时间，其余按下单时间。"""
    return func.coalesce(Order.requeue_at, Order.created_at)


def queue_tiebreak_expr():
    """同一秒内的先后：换过文件的单排在未换过的单之后。

    数据库 DATETIME 只到秒，换文件与另一单下单落在同一秒时，仅靠时间比不出先后；
    用「是否重新排队」做第二排序键，保证刚换完文件的单确实在队尾。
    """
    return case((Order.requeue_at.is_not(None), 1), else_=0)


def queue_order_by():
    """队列的完整排序键，列表与位置计算共用，避免两处口径漂移。"""
    return (
        queue_time_expr().asc(),
        queue_tiebreak_expr().asc(),
        Order.id.asc(),
    )


def order_queue_time(order: Order) -> datetime:
    """Python 侧的同一口径，供单条订单比较用。"""
    return order.requeue_at or order.created_at


def print_pages_expr():
    """SQL 侧的排队页数：整单打印页数，为 0/NULL 时回落文档页数。"""
    return func.coalesce(func.nullif(Order.print_page_count, 0), Order.page_count, 0)


def order_print_pages(order: Order) -> int:
    """Python 侧的同一口径，供单条订单使用。"""
    return int(order.print_page_count or 0) or int(order.page_count or 0)


def _ahead_condition(order: Order):
    """排在 order 前面的条件，与 queue_order_by 的排序键一一对应。"""
    own_time = order_queue_time(order)
    own_flag = 1 if order.requeue_at else 0
    queue_time = queue_time_expr()
    tiebreak = queue_tiebreak_expr()
    return (
        (queue_time < own_time)
        | ((queue_time == own_time) & (tiebreak < own_flag))
        | (
            (queue_time == own_time)
            & (tiebreak == own_flag)
            & (Order.id < order.id)
        )
    )


def list_active_orders(db: Session) -> list[Order]:
    """当前队列（含打印中），按有效排队时间排序。"""
    return list(
        db.execute(
            select(Order)
            .where(Order.status.in_(ACTIVE_STATUSES))
            .order_by(*queue_order_by())
        ).scalars()
    )


def per_page_seconds(db: Session) -> float | None:
    """近 N 单的「秒/打印页」，用有效排队时间 -> printed_at 作为单均耗时。"""
    limit = int(_wait_rules().get("recent_orders", 20))
    start = queue_time_expr()
    pages = print_pages_expr()
    rows = list(
        db.execute(
            select(pages, start, Order.printed_at)
            .where(Order.status.in_(HISTORY_STATUSES))
            .where(Order.printed_at.is_not(None))
            .where(pages > 0)
            .order_by(Order.printed_at.desc())
            .limit(limit)
        )
    )

    total_seconds = 0.0
    total_pages = 0
    for page_count, started_at, printed_at in rows:
        if not isinstance(started_at, datetime) or not isinstance(printed_at, datetime):
            continue
        seconds = (printed_at - started_at).total_seconds()
        if seconds <= 0:
            continue
        total_seconds += seconds
        total_pages += int(page_count or 0)
    if total_pages == 0:
        return None
    return total_seconds / total_pages


def _estimate_with_ratio(ratio: float | None, orders: list[Order]) -> int:
    """按前序订单估算等待分钟数；ratio 由调用方传入，避免一行一次查询。"""
    if not orders:
        return 0

    rules = _wait_rules()
    default_seconds = int(rules.get("default_seconds_per_order", 60))
    pages = [order_print_pages(order) for order in orders]

    if ratio is not None and all(pages):
        seconds = ratio * sum(pages)
    else:
        seconds = default_seconds * len(orders)
    return max(1, math.ceil(seconds / 60))


def estimate_minutes(db: Session, orders: list[Order]) -> int:
    """按前序订单估算等待分钟数。"""
    return _estimate_with_ratio(per_page_seconds(db), orders)


def queue_position(db: Session, order: Order) -> int:
    """当前订单前面还有多少单（不含自己）。

    走数据库聚合计数，不把前序订单的 id 读进 Python 再 len()：一万单排队时
    前者是一次 COUNT（毫秒级），后者要传回并物化一万行。口径与同文件的
    `order_wait_info`、`_build_snapshot` 一致，都按 `_ahead_condition` 算。
    """
    if order.status not in ACTIVE_STATUSES:
        return 0
    return int(
        db.execute(
            select(func.count())
            .select_from(Order)
            .where(Order.status.in_(ACTIVE_STATUSES))
            .where(Order.id != order.id)
            .where(_ahead_condition(order))
        ).scalar_one()
    )


def _read_active_rows(db: Session) -> list:
    """读整队的订单 id 与打印页数，按队列顺序。

     只取两列，不把整个队列读成 ORM 对象：一万单时读对象要一百多毫秒，
    读两列是十几毫秒。SQL 由下面的 `_queue_rows_sql()` 编译一次后固定，
    每行少一层 SQLAlchemy 类型转换（实测单独一次重建 10.9 ms -> 9.9 ms CPU）。
    """
    return db.execute(text(_queue_rows_sql())).all()


@lru_cache(maxsize=1)
def _queue_rows_sql() -> str:
    """把「读整队两列」的 Core 查询编译成一条无参 SQL 文本。

    排序键仍然来自 `queue_order_by()`，队列口径只有一处，编译结果不会漂。
    """
    statement = (
        select(Order.id, print_pages_expr())
        .where(Order.status.in_(ACTIVE_STATUSES))
        .order_by(*queue_order_by())
    )
    return str(
        statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _build_snapshot(db: Session) -> QueueSnapshot:
    """读一次整队，算出位置、前序页数前缀和与单均耗时。"""
    started_at = time.monotonic()  # 有效期从读库那一刻算起，不含后续计算
    active_rows = _read_active_rows(db)

    positions: dict[int, int] = {}
    # 前缀和：前面每个位置的累计页数，避免逐行切片累加
    prefix_pages = [0] * (len(active_rows) + 1)
    for position, (order_id, print_pages) in enumerate(active_rows):
        positions[order_id] = position
        prefix_pages[position + 1] = prefix_pages[position] + int(print_pages or 0)

    return QueueSnapshot(
        positions=positions,
        prefix_pages=tuple(prefix_pages),
        total=len(active_rows),
        seconds_per_page=per_page_seconds(db),
        built_at=started_at,
    )


def queue_state(db: Session) -> QueueSnapshot:
    """取队列快照：有效期内直接复用，过期或刚失效时重新读一次库。

    重建时只放一个请求进去：一万单的一份快照要几十毫秒，有效期一到，
    十几个并发请求同时冲进来重建会把数据库打成一堆重复的全队扫描，
    后面排队的等它建好直接复用即可。
    """
    snapshot = queue_snapshot.current()
    if snapshot is not None:
        return snapshot

    with _build_lock:
        snapshot = queue_snapshot.current()
        if snapshot is not None:
            return snapshot
        built_generation = queue_snapshot.generation()
        snapshot = _build_snapshot(db)
        queue_snapshot.store(snapshot, built_generation)
        return snapshot


def batch_wait_info(db: Session, orders: list[Order]) -> dict[int, dict]:
    """一次算好一批订单的排队位置与等待时间，返回 {订单 id: 排队信息}。

    列表接口必须走这个函数：原先每行调一次 order_wait_info，每行都要把整个队列
    扫一遍，一页 50 行就是 50 遍全队列扫描。现在整队位置来自共享快照，一页只查表。
    """
    if not orders:
        return {}

    snapshot = queue_state(db)
    rules = _wait_rules()  # 一次读取，避免每行读一遍配置文件
    total = snapshot.total
    result: dict[int, dict] = {}
    for order in orders:
        position = snapshot.position_of(order.id)
        if position is None:
            result[order.id] = {
                "queue_position": 0,
                "estimated_minutes": 0,
                "queuing_count": total,
            }
            continue
        ahead_pages = snapshot.prefix_pages[position]
        own_pages = order_print_pages(order)
        minutes = _minutes_for(
            snapshot.seconds_per_page, ahead_pages + own_pages, position + 1, rules
        )
        result[order.id] = {
            "queue_position": position,
            "estimated_minutes": minutes,
            "queuing_count": total,
        }
    return result


def _minutes_for(
    ratio: float | None, pages: int, order_count: int, rules: dict | None = None
) -> int:
    """预计分钟数：ratio 是「秒/打印页」，pages 必须是打印页数，两边口径一致。"""
    rules = rules if rules is not None else _wait_rules()
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

    ahead_condition = active_condition & _ahead_condition(order)
    ahead_count, ahead_pages = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(print_pages_expr()), 0),
        ).where(ahead_condition)
    ).one()

    ratio = per_page_seconds(db)
    minutes = _minutes_for(
        ratio, int(ahead_pages) + order_print_pages(order), int(ahead_count) + 1
    )
    return {
        "queue_position": int(ahead_count),
        "estimated_minutes": minutes,
        "queuing_count": total,
    }


def new_order_wait_info(db: Session) -> dict:
    """新下单的预计等待（排在整个队列之后）。

    首页每次刷新都会问一次，所以同样走快照，不再把整队读成对象。
    """
    snapshot = queue_state(db)
    if snapshot.total == 0:
        return {"queuing_count": 0, "estimated_minutes": 0}
    return {
        "queuing_count": snapshot.total,
        "estimated_minutes": _minutes_for(
            snapshot.seconds_per_page, snapshot.total_pages, snapshot.total
        ),
    }
