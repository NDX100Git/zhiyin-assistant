"""老师端后台路由：接单队列、状态推进、核销、收单开关、统计与公告。

除登录外全部需要管理员 token（require_admin）。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.admin import (
    AbnormalRequest,
    AdminLoginRequest,
    BatchCompleteRequest,
    CompleteRequest,
    FileChangeConfirmRequest,
    NoticeCreateRequest,
    SessionSwitchRequest,
    VerifyCodeRequest,
)
from app.services import admin_service, file_change_service
from app.services.auth import get_admin_auth_provider
from app.utils.exceptions import BusinessError
from app.utils.response import ok
from app.utils.security import ROLE_ADMIN, create_token, require_admin

router = APIRouter()


@router.post("/login", summary="老师端登录（免 token）", response_model=None)
def admin_login(payload: AdminLoginRequest) -> dict:
    """账号密码来自环境变量 ADMIN_USERNAME / ADMIN_PASSWORD。"""
    info = get_admin_auth_provider().verify(payload.model_dump())
    token, expires_in = create_token(info.external_id, ROLE_ADMIN)
    return ok(
        {
            "token": token,
            "expires_in": expires_in,
            "admin": {"username": info.external_id, "role": ROLE_ADMIN},
        }
    )


@router.get("/queue", summary="接单队列", response_model=None)
def queue(
    status: str | None = Query(default=None, description="按状态筛选"),
    keyword: str | None = Query(default=None, description="订单号 / 取件码 / 文件名模糊搜索"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return ok(admin_service.queue_list(db, status, keyword, page, page_size))


@router.get("/order/{order_id}/download", summary="获取限时下载链接", response_model=None)
def download(
    order_id: int,
    file_index: int | None = Query(
        default=None, ge=1, description="只取第几份文件；不传则返回全部文件链接"
    ),
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    order = admin_service.get_order(db, order_id)
    return ok(admin_service.download_url(db, order, file_index))


@router.post(
    "/order/{order_id}/confirm-files-seen",
    summary="老师确认已看到替换后的文件",
    response_model=None,
)
def confirm_files_seen(
    order_id: int,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """老师重新下载后调用，清掉队列里的「文件已替换」标记。"""
    order = admin_service.get_order(db, order_id)
    return ok(admin_service.confirm_files_seen(db, order, operator))


@router.post("/order/{order_id}/start", summary="开始打印", response_model=None)
def start(
    order_id: int,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """queuing -> printing，并调用当前 PrinterProvider（V1 为手动打印）。"""
    order = admin_service.get_order(db, order_id)
    return ok(admin_service.start_print(db, order, operator))


@router.post(
    "/order/{order_id}/confirm-file-change",
    summary="确认或驳回换文件",
    response_model=None,
)
def confirm_file_change(
    order_id: int,
    payload: FileChangeConfirmRequest,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """approve=true 时替换文件、按确认价落库并重新排到队尾；false 时保留原文件。"""
    order = admin_service.get_order(db, order_id)
    result = file_change_service.confirm_file_change(
        db, order, payload.approve, payload.amount_cents, operator
    )
    view = admin_service.order_admin_view(db, order)
    return ok(
        {
            "order": view,
            "approved": bool(result["approved"]),
            "file_change": view["file_change"],
            "amount_cents": order.amount_cents,
            "supplement_cents": result.get("supplement_cents", 0),
            "paid_total_cents": result.get("paid_total_cents", 0),
        }
    )


@router.post("/order/{order_id}/complete", summary="打印完成（可改价）", response_model=None)
def complete(
    order_id: int,
    payload: CompleteRequest | None = None,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """printing -> ready，返回系统计价供前端预填；老师改价会写入操作日志。"""
    order = admin_service.get_order(db, order_id)
    amount = payload.amount_cents if payload else None
    return ok(admin_service.complete_print(db, order, operator, amount))


@router.get("/order/{order_id}/quote", summary="系统报价（弹窗预填）", response_model=None)
def quote(
    order_id: int,
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    order = admin_service.get_order(db, order_id)
    return ok(admin_service.quote_amount(db, order))


@router.post("/order/batch-complete", summary="批量标记完成", response_model=None)
def batch_complete(
    payload: BatchCompleteRequest,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return ok(admin_service.batch_complete(db, payload.order_ids, operator))


@router.post("/order/{order_id}/verify", summary="核销取件", response_model=None)
def verify(
    order_id: int,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """ready -> completed；未付款的订单在此现场收款并写 payment_logs。"""
    order = admin_service.get_order(db, order_id)
    return ok(admin_service.verify_pickup(db, order, operator))


@router.post("/order/verify-code", summary="按取件码核销（扫码 / 手输）", response_model=None)
def verify_code(
    payload: VerifyCodeRequest,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return ok(admin_service.verify_by_code(db, payload.pickup_code, operator))


@router.post("/order/{order_id}/abnormal", summary="标记异常", response_model=None)
def abnormal(
    order_id: int,
    payload: AbnormalRequest | None = None,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    order = admin_service.get_order(db, order_id)
    reason = payload.reason if payload else None
    return ok(admin_service.mark_abnormal(db, order, reason, operator))


@router.post("/order/{order_id}/recover", summary="异常恢复排队", response_model=None)
def recover(
    order_id: int,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    order = admin_service.get_order(db, order_id)
    return ok(admin_service.recover_order(db, order, operator))


@router.post("/order/{order_id}/cancel", summary="关闭异常单", response_model=None)
def cancel_abnormal(
    order_id: int,
    payload: AbnormalRequest | None = None,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """关闭异常单；payload.refund_cents 可指定部分退款，不传时按成交价全额退。"""
    order = admin_service.get_order(db, order_id)
    reason = payload.reason if payload else None
    refund_cents = payload.refund_cents if payload else None
    return ok(admin_service.cancel_abnormal(db, order, reason, operator, refund_cents))


def _parse_day(value: str):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError) as exc:
        raise BusinessError("日期格式应为 YYYY-MM-DD", code=40001) from exc


@router.post("/session/close", summary="关闭某时段收单", response_model=None)
def close_session(
    payload: SessionSwitchRequest,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """只影响新提交的订单：关闭后新订单排到下一个打印时段。"""
    return ok(
        admin_service.switch_session(db, _parse_day(payload.date), payload.session, False, operator)
    )


@router.post("/session/open", summary="恢复某时段收单", response_model=None)
def open_session(
    payload: SessionSwitchRequest,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return ok(
        admin_service.switch_session(db, _parse_day(payload.date), payload.session, True, operator)
    )


@router.get("/sessions", summary="某天收单时段状态", response_model=None)
def sessions(
    date: str | None = Query(default=None, description="YYYY-MM-DD，默认今天"),
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    day = _parse_day(date) if date else datetime.now().date()
    return ok({"items": admin_service.session_overview(db, day)})


@router.get("/transactions", summary="收款流水（按日期）", response_model=None)
def transactions(
    date: str | None = Query(default=None, description="YYYY-MM-DD，默认今天"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    day = _parse_day(date) if date else datetime.now().date()
    return ok(admin_service.transactions(db, day, page, page_size))


@router.get("/summary", summary="今日数据", response_model=None)
def summary(
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return ok(admin_service.today_summary(db))


@router.post("/notice", summary="发布公告", response_model=None)
def publish_notice(
    payload: NoticeCreateRequest,
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return ok(admin_service.publish_notice(db, payload.content, operator))
