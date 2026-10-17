"""收单时段管理（上午 / 下午）。

规则：截单（老师手动关闭 / 当前时段容量已满 / 不在时段内）都不拒绝新订单，
学生仍可下单，但会提示「排在下一个打印时段取件」；已在队列中的订单不受任何影响。
时段划分、容量与提示文案全部来自 config/order_rules.json。
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_order_rules
from app.models.queue_session import QueueSession


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def resolve_session_name(now: datetime) -> str | None:
    """按服务端当前时间判断处于哪个时段；不在任何时段内返回 None。"""
    rules = get_order_rules()["session"]
    current = now.time()
    for name in ("am", "pm"):
        window = rules.get(name, {}).get("window")
        if not window:
            continue
        if _parse_hhmm(window[0]) <= current < _parse_hhmm(window[1]):
            return name
    return None


def get_or_create_session(db: Session, day: date_type, name: str) -> QueueSession:
    """取当天某时段的设置行，不存在则按默认容量创建（默认开放）。

    每天首批并发下单会同时发现“行不存在”，各自插同一行；(日期, 时段) 唯一键只允许一个
    成功。用 `INSERT ... ON DUPLICATE KEY UPDATE` 变成幂等写入，没有异常可抛。

    随后的读取必须加 `FOR UPDATE`：MySQL 默认 REPEATABLE READ，本请求早先查用户时已经把
    读视图定住，普通 SELECT 看不到并发事务刚提交的那一行，会读成空。锁定读走的是最新数据。
    """
    default_capacity = int(get_order_rules()["session"].get("default_capacity", 30))
    db.execute(
        text(
            "INSERT INTO queue_sessions (date, session, is_open, capacity, used) "
            "VALUES (:date, :session, 1, :capacity, 0) "
            "ON DUPLICATE KEY UPDATE date = date"
        ),
        {"date": day, "session": name, "capacity": default_capacity},
    )
    return db.execute(
        select(QueueSession)
        .where(QueueSession.date == day, QueueSession.session == name)
        .with_for_update()
    ).scalar_one()


def current_session_info(db: Session, now: datetime | None = None) -> dict:
    """当前收单状态，供首页与下单提示使用。

    - can_order：能否下单。V1 始终为 True（截单只影响取件时段，不影响下单）
    - deferred：本次下单是否会被排到下一个打印时段
    """
    now = now or datetime.now()  # 服务端时间
    rules = get_order_rules()["session"]
    name = resolve_session_name(now)

    if name is None:
        return {
            "date": now.date().isoformat(),
            "session": None,
            "label": "",
            "is_open": False,
            "switch_open": False,
            "capacity": 0,
            "used": 0,
            "can_order": True,
            "deferred": True,
            "hint": rules.get("outside_hint", "当前不在收单时段"),
        }

    row = get_or_create_session(db, now.date(), name)
    is_open = bool(row.is_open) and row.used < row.capacity
    session_rules = rules.get(name, {})
    hint = "" if is_open else session_rules.get("closed_hint", "当前时段已截单，订单将排在下一个打印时段")

    return {
        "date": now.date().isoformat(),
        "session": name,
        "label": session_rules.get("label", name),
        "is_open": is_open,
        "switch_open": bool(row.is_open),
        "capacity": row.capacity,
        "used": row.used,
        "can_order": True,
        "deferred": not is_open,
        "hint": hint,
    }


def order_notice(db: Session, now: datetime | None = None) -> dict:
    """下单时给学生的提示：本次订单是否排到下一个打印时段。"""
    info = current_session_info(db, now)
    return {
        "deferred": info["deferred"],
        "hint": info["hint"],
        "session": info["session"],
        "session_label": info["label"],
    }
