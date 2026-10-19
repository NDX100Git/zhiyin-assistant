"""学生端订单相关路由。

客户端只能读取状态；订单号、取件码、状态推进一律由服务端完成。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.order import OrderCancelRequest, OrderCreateRequest, OrderFileReplaceRequest
from app.services import file_change_service, order_service
from app.services.session_service import order_notice
from app.utils.response import ok, ok_json
from app.utils.security import get_current_user

router = APIRouter()


@router.post("/create", summary="创建订单（需登录）", response_model=None)
def create_order(
    payload: OrderCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """下单后状态为待支付，返回服务端生成的订单号与取件码。"""
    order = order_service.create_order(db, user, payload)
    data = order_service.order_to_dict(db, order)
    # 截单时段下单时，前端据此提示「会排在下一个打印时段」
    data["session_notice"] = order_notice(db)
    return ok(data)


@router.get("/list", summary="我的订单列表", response_model=None)
def list_orders(
    status: str | None = Query(default=None, description="按状态筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # 30 秒轮询的最高频读接口：返回值已是纯 JSON 类型，直出省掉一层通用编码
    return ok_json(order_service.list_orders(db, user, status, page, page_size))


@router.get("/{order_id}", summary="订单详情（含排队位置）", response_model=None)
def order_detail(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    order = order_service.get_owned_order(db, user, order_id)
    return ok(order_service.order_to_dict(db, order))


@router.post("/{order_id}/cancel", summary="取消订单", response_model=None)
def cancel_order(
    order_id: int,
    payload: OrderCancelRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """仅待支付 / 排队中允许取消。"""
    reason = payload.reason if payload else None
    order = order_service.cancel_order(db, user, order_id, reason)
    return ok(order_service.order_to_dict(db, order))


@router.post("/{order_id}/pay", summary="模拟支付", response_model=None)
def pay_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """调用当前配置的 PaymentProvider，成功后状态转排队中。"""
    return ok(order_service.pay_order(db, user, order_id))


@router.post("/{order_id}/replace-file", summary="换文件（需登录）", response_model=None)
def replace_file(
    order_id: int,
    payload: OrderFileReplaceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """待支付直接生效；排队中与异常单转「待老师确认」；其余状态拒绝。

    返回订单对象本身，另加 `applied` 标识是立即生效还是等待确认；
    待确认时订单里的 `file_change` 字段给出新文件与系统重算价。
    """
    order = order_service.get_owned_order(db, user, order_id)
    result = file_change_service.replace_order_file(db, order, payload)
    data = order_service.order_to_dict(db, order)
    data["applied"] = bool(result["applied"])
    return ok(data)
