"""老师端业务逻辑：接单队列、打印推进、核销、收单开关、今日统计、公告。

所有状态推进都走 order_state.change_status，保证写入 print_logs 与通知事件。
"""

from __future__ import annotations

import logging
import re
from datetime import date as date_type
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
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
from app.services.order_file_service import (
    batch_files_view,
    ensure_files,
    file_to_dict,
    files_view,
    is_file_unconfirmed,
    system_total_cents,
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
from app.services.pricing import ensure_amount_allowed
from app.services.printer import get_printer_provider
from app.services.queue_service import batch_wait_info, order_wait_info, queue_order_by
from app.services.session_service import get_or_create_session, release_session_slot
from app.services.storage import get_storage
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

# 区分「调用方没传」与「明确没有待确认请求」：队列接口批量查到 None 时要传 None
_UNSET = object()

# 老师手输取件码的归一化表：手机输入法常给出全角数字与全角/中文破折号，
# 不归成半角就查不到库里的值。只作用于人工输入这一条路径，库里的存储值不动。
_PICKUP_INPUT_TRANSLATION: dict[int, str] = {
    # 全角数字 ０ 至 ９ → 半角
    **{0xFF10 + i: chr(ord("0") + i) for i in range(10)},
    # 全角字母 → 半角（字母随后统一大写，与库里的取件码一致）
    **{0xFF21 + i: chr(ord("A") + i) for i in range(26)},
    **{0xFF41 + i: chr(ord("a") + i) for i in range(26)},
    # 全角连字符与常见破折号变体，一律归成半角短横
    **{ord(ch): "-" for ch in "－—–−‒―‐‑﹘﹣"},
}


def _normalize_pickup_input(raw: str) -> str:
    """人工输入的取件码归一化：全角转半角、破折号归短横、去空白、字母大写。"""
    text = (raw or "").translate(_PICKUP_INPUT_TRANSLATION)
    # isspace() 覆盖全角空格 U+3000、制表符与首尾空白
    return "".join(ch for ch in text if not ch.isspace()).upper()


def _pickup_input_display(raw: str, fallback: str) -> str:
    """错误提示里回显老师输入的那一份，不做归一化，只去首尾空白。"""
    return (raw or "").strip() or fallback


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
                     file_change: dict | object | None = _UNSET,
                     files: list[dict] | object | None = _UNSET) -> dict:
    """老师端订单视图：含打印参数、文件清单与排队信息。

    wait / file_change / files 可由调用方批量算好后传入；不传时单独查一次。
    """
    wait = wait or order_wait_info(db, order)
    if file_change is _UNSET:
        file_change = pending_change_view(db, order.id)
    if files is _UNSET:
        files = files_view(db, order)
    return {
        "id": order.id,
        "order_no": order.order_no,
        "pickup_code": order.pickup_code,
        "status": order.status,
        "status_label": status_label(order.status),
        "file_key": order.file_key,
        "file_name": order.file_name,
        "page_count": order.page_count,
        "print_page_count": int(order.print_page_count or 0),
        "print_side": order.print_side,
        "color_mode": order.color_mode,
        "copies": order.copies,
        "paper_size": order.paper_size,
        "amount_cents": order.amount_cents,
        "paid": order.paid,
        "pay_method": order.pay_method,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "requeue_at": order.requeue_at.isoformat() if order.requeue_at else None,
        "file_changed_at": order.file_changed_at.isoformat() if order.file_changed_at else None,
        "files_seen_at": order.files_seen_at.isoformat() if order.files_seen_at else None,
        "files_unconfirmed": is_file_unconfirmed(order),
        "printed_at": order.printed_at.isoformat() if order.printed_at else None,
        "picked_at": order.picked_at.isoformat() if order.picked_at else None,
        "remark": order.remark,
        "cancel_reason": order.cancel_reason,
        "queue_position": wait["queue_position"],
        "estimated_minutes": wait["estimated_minutes"],
        "files": files,
        "file_change": file_change,
    }


def _escape_like(raw: str) -> str:
    """转义 LIKE 通配符。关键字里的 _ 与 % 不是通配需求，不转义会退化成全表匹配。"""
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def queue_list(db: Session, status: str | None = None, keyword: str | None = None,
               page: int = 1, page_size: int = 50) -> dict:
    """接单队列：按订单号排序，支持状态筛选与关键字搜索。"""
    condition = []
    if status:
        condition.append(Order.status == status)
    if keyword:
        like = f"%{_escape_like(keyword.strip())}%"
        condition.append(
            or_(
                Order.order_no.like(like, escape="\\"),
                Order.pickup_code.like(like, escape="\\"),
                Order.file_name.like(like, escape="\\"),
            )
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
    files = batch_files_view(db, rows)
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [
            order_admin_view(
                db, order, waits.get(order.id), changes.get(order.id), files.get(order.id)
            )
            for order in rows
        ],
    }


def download_url(db: Session, order: Order, file_index: int | None = None) -> dict:
    """给老师发限时签名下载链接（私有桶，禁止公开读）。

    不传 file_index 时返回全部文件链接，同时保留顶层的首份文件链接作为旧接口兼容。
    """
    rows = ensure_files(db, order)
    if not rows:
        raise BusinessError("该订单没有关联文件", code=40402)
    target = rows[0]
    if file_index is not None:
        target = next(
            (row for row in rows if int(row.file_index) == int(file_index)), None
        )
        if target is None:
            raise BusinessError(
                f"订单里没有第 {file_index} 份文件，请刷新后重试", code=40405
            )
    storage = get_storage()
    items = [
        {**file_to_dict(row, order), "signed_url": storage.signed_url(row.file_key)}
        for row in rows
    ]
    if not items and order.file_key:
        # 老订单迁移未回填时，兜底给第 1 份文件的链接
        for view in files_view(db, order):
            items.append(
                {**view, "signed_url": storage.signed_url(view["file_key"])}
            )
    primary = next(
        (item for item in items if item["file_index"] == int(target.file_index)),
        items[0] if items else None,
    )
    return {
        "order_id": order.id,
        "file_index": primary["file_index"] if primary else None,
        "file_key": primary["file_key"] if primary else None,
        "file_name": primary["file_name"] if primary else None,
        "signed_url": primary["signed_url"] if primary else "",
        "files": items,
    }


def start_print(db: Session, order: Order, operator: str) -> dict:
    """开始打印：queuing -> printing，并交给当前 PrinterProvider。

    订单一旦进入打印中就不能再换文件，所以先把待确认请求置为失效，
    避免老师在打印后才看到一个无法执行的确认请求。
    """
    expire_pending_changes(db, order.id, "订单已开始打印，换文件请求失效", operator)
    change_status(db, order, STATUS_PRINTING, operator=operator, note="老师开始打印")
    storage = get_storage()
    rows = files_view(db, order)
    file_links = [
        {**row, "signed_url": storage.signed_url(row["file_key"])} for row in rows
    ]
    file_url = file_links[0]["signed_url"] if file_links else ""
    job = get_printer_provider().print(order, file_url)
    job["files"] = file_links
    db.commit()
    db.refresh(order)
    return {"order": order_admin_view(db, order), "print_job": job}


def confirm_files_seen(db: Session, order: Order, operator: str) -> dict:
    """老师重新下载后确认已看到当前文件，清掉「文件已替换」标记。"""
    order.files_seen_at = datetime.now()  # 服务端时间
    record_log(
        db, order, "note",
        note="老师已重新下载并确认当前文件",
        operator=operator,
    )
    db.commit()
    db.refresh(order)
    return order_admin_view(db, order)


def complete_print(db: Session, order: Order, operator: str,
                   amount_cents: int | None = None) -> dict:
    """打印完成：printing -> ready，老师可改价（改价写日志留痕）。

    不传金额时**沿用订单已有的成交价**：下单时算的价、老师改过的价、换文件流程里
    老师确认的价都落在 order.amount_cents 上，它们是已经跟学生达成的价格。
    只有订单还没有成交价（0）时才用系统价兜底。

    早期实现用系统重算价直接覆盖，会把换文件确认价无声抹掉：学生已付 500、
    老师确认改到 800，批量完成时不传金额就被改回系统价，差额两头对不上。
    """
    system_amount = system_total_cents(db, order)
    current_amount = int(order.amount_cents or 0)
    final_amount = (current_amount or system_amount) if amount_cents is None else int(amount_cents)
    # 显式改价过完整护栏（含相对系统价的倍数上限）；沿用已有成交价只查绝对上限，
    # 那是既成事实，不该反过来卡住完成流程
    final_amount = ensure_amount_allowed(
        final_amount, system_amount if amount_cents is not None else 0
    )

    if amount_cents is None:
        if final_amount == system_amount:
            note = f"打印完成，系统计价 {final_amount} 分"
        else:
            note = f"打印完成，沿用成交价 {final_amount} 分（系统计价 {system_amount} 分）"
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
    """批量标记完成：逐单处理，返回成功与失败明细。

    单笔失败不能拖垮整批：只捕 BusinessError 时，一次 DB 断连或锁超时就会
    冒泡成 500，前面成功的单已提交、后面的单没处理，调用方还拿不到明细。
    """
    succeeded: list[dict] = []
    failed: list[dict] = []
    # 去重：同一单传两次会被处理两遍，第二次撞状态机报错，明细里出现假失败
    for order_id in dict.fromkeys(order_ids):
        try:
            order = get_order(db, order_id)
            # 显式沿用当前成交价：不传就会走系统价，把换文件确认价和改价抹掉
            result = complete_print(db, order, operator, int(order.amount_cents or 0) or None)
            succeeded.append(
                {"order_id": order_id, "order_no": result["order"]["order_no"],
                 "amount_cents": result["amount_cents"]}
            )
        except BusinessError as exc:
            db.rollback()
            failed.append({"order_id": order_id, "message": exc.message})
        except Exception:  # 非业务异常（断连/锁超时）只记这一单，不中断整批
            db.rollback()
            logger.exception("批量完成订单 %s 失败", order_id)
            failed.append({"order_id": order_id, "message": "处理失败，请稍后重试该单"})
    return {"succeeded": succeeded, "failed": failed}


def _paid_total(db: Session, order_id: int) -> int:
    """该单已收到的净金额：收款流水与退款流水（负数）求和。"""
    return int(
        db.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_cents), 0)).where(
                PaymentLog.order_id == order_id
            )
        ).scalar_one()
    )


def verify_pickup(db: Session, order: Order, operator: str) -> dict:
    """核销取件：ready -> completed；未付或改价差额在此现场收款。"""
    change_status(db, order, "completed", operator=operator, note="核销取件")

    paid_total = _paid_total(db, order.id)
    owed = max(0, order.amount_cents - paid_total)
    collected = owed > 0
    if collected:
        db.add(
            PaymentLog(
                order_id=order.id,
                amount_cents=owed,
                method="onsite",
                operator=operator,
                idempotency_key=f"onsite:{order.id}",
            )
        )
        record_log(db, order, "note", note=f"取件时现场收款 {owed} 分", operator=operator)

    order.paid = 1
    order.pay_method = "onsite" if collected else order.pay_method
    order.pay_channel = order.pay_method
    # 只在首次支付时记录时间。线上已付过的单被老师核销时若无条件覆盖，
    # 支付时间会变成核销时间，对账口径与客诉追溯全部错位。
    if order.paid_at is None:
        order.paid_at = datetime.now()  # 服务端时间

    try:
        db.commit()
    except IntegrityError as exc:  # 幂等键撞车：同一单重复现场收款
        db.rollback()
        raise BusinessError("该订单已登记过现场收款，请刷新后重试", code=40906) from exc
    db.refresh(order)
    return {
        "order": order_admin_view(db, order),
        "collected_onsite": collected,
        "collected_cents": owed,
    }


def _normalize_pickup_code(raw: str, now: datetime | None = None) -> str:
    """规整取件码（只用于人工输入路径）。

    老师可只输当天的短码（A-01），这里按服务器当天补成完整码（261017-A-01）；
    扫码与学生端展示的都是完整码，原样匹配。跨天的遗留单只能用完整码。
    手机输入法打出的全角数字、全角短横与中文破折号先归一化成半角，否则查不到库。
    """
    code = _normalize_pickup_input(raw)
    if not code:
        return ""
    if PICKUP_SHORT_CODE_RE.fullmatch(code):
        today = (now or datetime.now()).date()
        return f"{today:%y%m%d}-{code}"
    return code


def verify_by_code(db: Session, pickup_code: str, operator: str) -> dict:
    """按取件码核销（老师扫码或手动输入）。"""
    raw_input = pickup_code or ""
    code = _normalize_pickup_code(raw_input)
    if not code:
        raise BusinessError("请输入取件码", code=40001)
    # 错误提示回显老师输入的那一份，不展示归一化结果，避免老师对不上自己打的码
    shown = _pickup_input_display(raw_input, code)

    orders = list(
        db.execute(
            select(Order)
            .where(Order.pickup_code == code, Order.status == STATUS_READY)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(2)
        ).scalars()
    )
    if not orders:
        raise BusinessError(f"没有找到取件码 {shown} 对应的待取件订单", code=40401)
    if len(orders) > 1:
        # 唯一索引下不会发生；只有历史重码数据才会走到，宁可拒绝也不能核销错单
        raise BusinessError(
            f"取件码 {shown} 命中多张待取件单，请用完整取件码或订单号核销", code=40902
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
    """异常处理完成：已打印过的单回到待取件，未打印的回到排队。

    两者都回 queuing 会形成 ready→abnormal→queuing→printing→ready 的回路：
    已经出纸的订单重新进入打印队列，老师再点一次「开始打印」就白打一遍，
    printed_at 也会被覆盖，对账与耗时统计全失真。
    """
    if order.printed_at is not None:
        change_status(
            db, order, STATUS_READY, operator=operator,
            note="异常处理完成，文件已打印，恢复待取件",
        )
    else:
        change_status(
            db, order, STATUS_QUEUING, operator=operator, note="异常处理完成，恢复排队"
        )
    db.commit()
    db.refresh(order)
    return order_admin_view(db, order)


def cancel_abnormal(db: Session, order: Order, reason: str | None, operator: str,
                    refund_cents: int | None = None) -> dict:
    """老师关闭异常单（异常单的唯一取消入口），已收过钱的单同时登记退款。

    退款口径（与前端约定一致）：用一条**负数流水**表示退款，
    method=refund、幂等键 refund:<订单 id>，不另建退款表，也不改学生的收款行。
    这样当日收入、收款流水、订单对账全部是同一种算法：把流水加起来。
    同一单最多退一次，重复调用会被 (order_id, idempotency_key) 唯一索引拦住。

    refund_cents 不传时按订单成交价全额退，但不超过实际已收金额：
    老师改价上调后学生只付了原价时，退的是真收到的钱，不是账面上的价。
    """
    if order.status not in ADMIN_CANCELLABLE:
        raise BusinessError(
            f"只有异常订单可以由老师关闭，当前状态为「{status_label(order.status)}」",
            code=40903,
        )

    paid_total = _paid_total(db, order.id)
    if refund_cents is None:
        refund = min(paid_total, int(order.amount_cents or 0))
    else:
        refund = int(refund_cents)
        if refund < 0:
            raise BusinessError("退款金额不能为负数", code=40061)
        if refund > paid_total:
            raise BusinessError(
                f"退款金额不能超过已收的 {paid_total} 分", code=40062
            )

    note = (reason or "").strip() or "老师关闭异常单"
    order.cancel_reason = note
    expire_pending_changes(db, order.id, "异常单已关闭，换文件请求失效", operator)
    if refund > 0:
        db.add(
            PaymentLog(
                order_id=order.id,
                amount_cents=-refund,  # 负数行：退款从收入里冲掉
                method="refund",
                operator=operator,
                idempotency_key=f"refund:{order.id}",
            )
        )
        note = f"{note}；退款 {refund} 分（已收 {paid_total} 分）"
        order.cancel_reason = note
    change_status(db, order, "cancelled", operator=operator, note=note)
    if refund > 0:
        record_log(
            db, order, "refund",
            note=f"老师关闭订单并登记退款 {refund} 分", operator=operator,
        )
    # 异常单作废也不再消耗本时段产能，容量要还回去
    release_session_slot(db, order.created_at or datetime.now())
    try:
        db.commit()
    except IntegrityError as exc:  # 幂等键撞车：同一单重复退
        db.rollback()
        raise BusinessError("该订单已登记过退款，请勿重复操作", code=40906) from exc
    db.refresh(order)
    return {**order_admin_view(db, order), "refund_cents": refund,
            "paid_total_cents": paid_total}


def today_summary(db: Session) -> dict:
    """今日数据：接单数、收款额（分）、待取件数、异常单数。

    收款额是当日**流水求和**：正向收款加、退款负数行减，所以已退款的订单
    自动从当日收入里排除，不需要单独再减一遍。
    """
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


def quote_amount(db: Session, order: Order) -> dict:
    """打印完成前的系统报价（老师端弹窗预填用）：按逐份参数整单累加。"""
    system_amount = system_total_cents(db, order)
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
