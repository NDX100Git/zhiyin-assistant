"""学生端首页数据。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.order_service import home_summary
from app.utils.response import ok
from app.utils.security import get_current_user

router = APIRouter()


@router.get("/summary", summary="首页汇总（需登录）", response_model=None)
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """等待中单数与预计时间、待取件数、当前是否可排单与截单提示。"""
    return ok(home_summary(db, user))
