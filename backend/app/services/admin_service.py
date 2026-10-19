"""老师端业务逻辑：接单队列、打印推进、核销、收单开关、今日统计、公告。

所有状态推进都走 order_state.change_status，保证写入 print_logs 与通知事件。
"""

from __future__ import annotations

import logging
import re
from datetime import date as date_type
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_order_rules
from app.models.notice import Notice
from app.models.order import Order
from app.models.payment_log import PaymentLog
from app.services.file_change_service import (
    batch_pending_changes,
    expire_pending_changes,
    pending_change_view,
)
from app.services.order_state import (
    ADMIN_CANCELLABLE,
    STATUS_ABNORMAL,
    STATUS_PRINTING,
    STATUS_QUEUING,
    STATUS_READY,
    change_status,
    record_log,
    status_label,
)
from app.services.pricing import calc_amount_cents
from app.services.printer import get_printer_provider
from app.services.queue_service import batch_wait_info, order_wait_info, queue_order_by
from app.services.session_service import get_or_create_session
from app.services.storage import get_storage
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

# 区分「调用方没传」与「明确没有待确认请求」：队列接口批量查到 None 时要传 None
_UNSET = object()

def _short_code_pattern() -> re.Pattern[str]:
    """老师手输的当天短码，如 0-01。按取件码配置生成，改进制与长度不用改这里。"""
    rules = get_order_rules()["pickup_code"]
    alphabet = re.escape(str(rules["alphabet"]))
    length = int(rules["length"])
    separator_after = int(rules.get("separator_after", 1))
    if 0 < separator_after < length:
        return re.compile(
            rf"^[{alphabet}]{{{separator_after}}}-[{alphabet}]{{{length - separator_after}}}$"
        )
    return re.compile(rf"^[{alphabet}]{{{length}}}$")


PICKUP_SHORT_CODE_RE = _short_code_pattern()


def get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise BusinessError("订单不存在", code=40400)
    return order


def order_admin_view(db: Session, order: Order, wait: dict | None = None,
                     file_change: dict | object | None = _UNSET) -> dict:
    """老师端订单视图：含打印参数与排队信息。

    wait 与 file_change 可由调用方批量算好后传入；file_change 不传时单独查一次。
    """
    wait = wait or order_wait_info(db, order)
    if file_change is _UNSET:
        file_change = pending_change_view(db, order.id)
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
        "requeue_at": order.requeue_at.isoformat() if order.requeue_at else None,
        "printed_at": order.printed_at.isoformat() if order.printed_at else None,
        "picked_at": order.picked_at.isoformat() if order.picked_at else None,
        "remark": order.remark,
        "cancel_reason": order.cancel_reason,
        "queue_position": wait["queue_position"],
        "estimated_minutes": wait["estimated_minutes"],
        "file_change": file_change,
    }


def queue_list(db: Session, status: str | None = None, keyword: str | None = None,
               page: int = 1, page_size: int = 50) -> dict:
    """接单队列：按订单号排序，支持状态筛选与关键字搜索。"""
    condition = []
    if status:
        condition.append(Order.status == status)
    if keyword:
        like = f"%{keyword.strip()}%"
        condition.append(
            or_(Order.order_no.like(like), Order.pickup_code.like(like), Order.file_name.like(like))
        )

    total = db.execute(select(func.count()).select_from(Order).where(*condition)).scalar_one()
    rows = list(
        db.execute(
            select(Order)
            .where(*condition)
            .order_by(*queue_order_by())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    waits = batch_wait_info(db, rows)
    changes = batch_pending_changes(db, [order.id for order in rows])
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [
            order_admin_view(db, order, waits.get(order.id), changes.get(order.id))
            for order in rows
        ],
    }


def download_url(order: Order) -> dict:
    """给老师发限时签名下载链接（私有桶，禁止公开读）。"""
    if not order.file_key:
        raise BusinessError("该订单没有关联文件", code=40402)
    return {
        "order_id": order.id,
        "file_key": order.file_key,
        "file_name": order.file_name,
        "signed_url": get_storage().signed_url(order.file_key),
    }


def start_print(db: Session, order: Order, operator: str) -> dict:
    """开始打印：queuing -> printing，并交给当前 PrinterProvider。

    订单一旦进入打印中就不能再换文件，所以先把待确认请求置为失效，
    避免老师在打印后才看到一个无法执行的确认请求。
    """
    expire_pending_changes(db, order.id, "订单已开始打印，换文件请求失效", operator)
    change_status(db, order, STATUS_PRINTING, operator=operator, note="老师开始打印")
    file_url = get_storage().signed_url(order.file_key) if order.file_key else ""
    job = get_printer_provider().print(order, file_url)
    db.commit()
    db.refresh(order)
    return {"order": order_admin_view(db, order), "print_job": job}


def complete_print(db: Session, order: Order, operator: str,
                   amount_cents: int | None = None) -> dict:
    """打印完成：printing -> ready，系统计价预填，老师可改（改价写日志留痕）。"""
    system_amount = calc_amount_cents(
        order.page_count or 1, order.print_side or 2, order.color_mode or 0, order.copies or 1
    )
    final_amount = system_amount if amount_cents is None else int(amount_cents)
    if final_amount < 0:
        raise BusinessError("金额不能为负数", code=40061)

    if amount_cents is None:
        note = f"打印完成，系统计价 {final_amount} 分"
    elif final_amount == system_amount:
        note = f"打印完成，老师确认金额 {final_amount} 分（与系统一致）"
    else:
        note = f"打印完成，老师改价：系统 {system_amount} 分 → 实收 {final_amount} 分"

    paid_total = int(
        db.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_cents), 0)).where(
                PaymentLog.order_id == order.id
            )
        ).scalar_one()
    )
    if order.paid and final_amount > paid_total:
        # 学生已在线支付，但老师改价上调：标记为待补款，核销时只收差额
        order.paid = 0
        note += f"；已付 {paid_total} 分，需补收 {final_amount - paid_total} 分"

    order.amount_cents = final_amount
    change_status(db, order, STATUS_READY, operator=operator, note=note)
    db.commit()
    db.refresh(order)
    return {
        "order": order_admin_view(db, order),
        "system_amount_cents": system_amount,
        "amount_cents": final_amount,
        "price_changed": final_amount != system_amount,
    }


def batch_complete(db: Session, order_ids: list[int], operator: str) -> dict:
    """批量标记完成：逐单处理，返回成功与失败明细。"""
    succeeded: list[dict] = []
    failed: list[dict] = []
    for order_id in order_ids:
        try:
            order = get_order(db, order_id)
            result = complete_print(db, order, operator)
            succeeded.append(
                {"order_id": order_id, "order_no": result["order"]["order_no"],
                 "amount_cents": result["amount_cents"]}
            )
        except BusinessError as exc:
            db.rollback()
            failed.append({"order_id": order_id, "message": exc.message})
    return {"succeeded": succeeded, "failed": failed}


def verify_pickup(db: Session, order: Order, operator: str) -> dict:
    """核销取件：ready -> completed；未付或改价差额在此现场收款。"""
    change_status(db, order, "completed", operator=operator, note="核销取件")

    paid_total = int(
        db.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_cents), 0)).where(
                PaymentLog.order_id == order.id
            )
        ).scalar_one()
    )
    owed = max(0, order.amount_cents - paid_total)
    collected = owed > 0
    if collected:
        db.add(
            PaymentLog(
                order_id=order.id,
                amount_cents=owed,
                method="onsite",
                operator=operator,
            )
        )
        record_log(db, order, "note", note=f"取件时现场收款 {owed} 分", operator=operator)

    order.paid = 1
    order.pay_method = "onsite" if collected else order.pay_method
    order.pay_channel = order.pay_method
    order.paid_at = datetime.now()  # 服务端时间

    db.commit()
    db.refresh(order)
    return {
        "order": order_admin_view(db, order),
        "collected_onsite": collected,
        "collected_cents": owed,
    }


def _normalize_pickup_code(raw: str, now: datetime | None = None) -> str:
    """规整取件码。

    老师可只输当天的短码（A-01），这里按服务器当天补成完整码（261017-A-01）；
    扫码与学生端展示的都是完整码，原样匹配。跨天的遗留单只能用完整码。
    """
    code = "".join((raw or "").split()).upper()
    if not code:
        return ""
    if PICKUP_SHORT_CODE_RE.fullmatch(code):
        today = (now or datetime.now()).date()
        return f"{today:%y%m%d}-{code}"
    return code


def verify_by_code(db: Session, pickup_code: str, operator: str) -> dict:
    """按取件码核销（老师扫码或手动输入）。"""
    code = _normalize_pickup_code(pickup_code)
    if not code:
        raise BusinessError("请输入取件码", code=40001)

    orders = list(
        db.execute(
            select(Order)
            .where(Order.pickup_code == code, Order.status == STATUS_READY)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(2)
        ).scalars()
    )
    if not orders:
        raise BusinessError(f"没有找到取件码 {code} 对应的待取件订单", code=40401)
    if len(orders) > 1:
        # 唯一索引下不会发生；只有历史重码数据才会走到，宁可拒绝也不能核销错单
        raise BusinessError(
            f"取件码 {code} 命中多张待取件单，请用完整取件码或订单号核销", code=40902
        )
    return verify_pickup(db, orders[0], operator)


def mark_abnormal(db: Session, order: Order, reason: str | None, operator: str) -> dict:
    """标记异常（卡纸、缺墨、文件损坏、学生有异议等）。"""
    note = (reason or "").strip() or "老师标记异常"
    change_status(db, order, STATUS_ABNORMAL, operator=operator, note=note)
    record_log(db, order, "abnormal", note=note, operator=operator)
    db.commit()
    db.refresh(order)
    return order_admin_view(db, order)


def recover_order(db: Session, order: Order, operator: str) -> dict:
    """异常处理完成，恢复排队。"""
    change_status(db, order, STATUS_QUEUING, operator=operator, note="异常处理完成，恢复排队")
    db.commit()
    db.refresh(order)
    return order_admin_view(db, order)


def cancel_abnormal(db: Session, order: Order, reason: str | None, operator: str) -> dict:
    """老师关闭异常单（异常单的唯一取消入口）。"""
    if order.status not in ADMIN_CANCELLABLE:
        raise BusinessError(
            f"只有异常订单可以由老师关闭，当前状态为「{status_label(order.status)}」",
            code=40903,
        )
    note = (reason or "").strip() or "老师关闭异常单"
    order.cancel_reason = note
    expire_pending_changes(db, order.id, "异常单已关闭，换文件请求失效", operator)
    change_status(db, order, "cancelled", operator=operator, note=note)
    db.commit()
    db.refresh(order)
    return order_admin_view(db, order)


def today_summary(db: Session) -> dict:
    """今日数据：接单数、收款额（分）、待取件数、异常单数。"""
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    order_count = db.execute(
        select(func.count()).select_from(Order).where(Order.created_at >= day_start)
    ).scalar_one()
    income = db.execute(
        select(func.coalesce(func.sum(PaymentLog.amount_cents), 0)).where(
            PaymentLog.created_at >= day_start
        )
    ).scalar_one()

    status_rows = db.execute(
        select(Order.status, func.count()).group_by(Order.status)
    ).all()
    counts = {status: int(count) for status, count in status_rows}

    return {
        "date": datetime.now().date().isoformat(),
        "order_count": int(order_count),
        "income_cents": int(income),
        "ready_count": counts.get(STATUS_READY, 0),
        "abnormal_count": counts.get(STATUS_ABNORMAL, 0),
        "queuing_count": counts.get(STATUS_QUEUING, 0),
        "printing_count": counts.get(STATUS_PRINTING, 0),
    }


def quote_amount(order: Order) -> dict:
    """打印完成前的系统报价（老师端弹窗预填用）。"""
    system_amount = calc_amount_cents(
        order.page_count or 1, order.print_side or 2, order.color_mode or 0, order.copies or 1
    )
    return {
        "order_id": order.id,
        "system_amount_cents": system_amount,
        "current_amount_cents": order.amount_cents,
    }


def session_overview(db: Session, day: date_type) -> list[dict]:
    """某天上午/下午两个时段的当前状态（不存在时按默认值创建）。"""
    labels = {"am": "上午", "pm": "下午"}
    result = []
    for name in ("am", "pm"):
        row = get_or_create_session(db, day, name)
        result.append(
            {
                "date": row.date.isoformat(),
                "session": row.session,
                "label": labels[name],
                "is_open": bool(row.is_open),
                "capacity": row.capacity,
                "used": row.used,
                "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            }
        )
    db.commit()
    return result


def transactions(db: Session, day: date_type, page: int = 1, page_size: int = 50) -> dict:
    """按日期查收款流水。"""
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    condition = [PaymentLog.created_at >= start, PaymentLog.created_at < end]

    total = db.execute(
        select(func.count()).select_from(PaymentLog).where(*condition)
    ).scalar_one()
    total_cents = db.execute(
        select(func.coalesce(func.sum(PaymentLog.amount_cents), 0)).where(*condition)
    ).scalar_one()

    rows = db.execute(
        select(PaymentLog, Order.order_no)
        .outerjoin(Order, Order.id == PaymentLog.order_id)
        .where(*condition)
        .order_by(PaymentLog.created_at.desc(), PaymentLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return {
        "date": day.isoformat(),
        "count": int(total),
        "total_cents": int(total_cents),
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": payment.id,
                "order_id": payment.order_id,
                "order_no": order_no,
                "amount_cents": payment.amount_cents,
                "method": payment.method,
                "operator": payment.operator,
                "created_at": payment.created_at.isoformat() if payment.created_at else None,
            }
            for payment, order_no in rows
        ],
    }


def switch_session(db: Session, day: date_type, session: str, is_open: bool,
                   operator: str) -> dict:
    """关闭 / 恢复某个时段的收单。

    关闭只影响新提交的订单：新订单会排到下一个打印时段，已在队列中的订单不受影响。
    """
    name = (session or "").strip().lower()
    if name not in {"am", "pm"}:
        raise BusinessError("时段只能是 am 或 pm", code=40001)

    row = get_or_create_session(db, day, name)
    row.is_open = 1 if is_open else 0
    row.closed_at = None if is_open else datetime.now()
    db.commit()
    logger.info("管理员 %s %s %s 时段收单", operator, "开启" if is_open else "关闭", name)
    return {
        "date": row.date.isoformat(),
        "session": row.session,
        "is_open": bool(row.is_open),
        "capacity": row.capacity,
        "used": row.used,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
    }


def publish_notice(db: Session, content: str, operator: str) -> dict:
    """发布公告。"""
    text = (content or "").strip()
    if not text:
        raise BusinessError("公告内容不能为空", code=40001)
    if len(text) > 1000:
        raise BusinessError("公告内容最多 1000 个字符", code=40002)

    notice = Notice(content=text, created_by=operator, active=1)
    db.add(notice)
    db.commit()
    db.refresh(notice)
    return {
        "id": notice.id,
        "content": notice.content,
        "created_by": notice.created_by,
        "created_at": notice.created_at.isoformat() if notice.created_at else None,
    }
