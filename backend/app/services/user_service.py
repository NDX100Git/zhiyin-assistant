"""用户相关业务逻辑（与登录方式无关）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.auth.base import UserInfo


def get_or_create_user(db: Session, info: UserInfo) -> User:
    """按外部标识取用户，不存在则创建（首次登录自动注册）。"""
    user = db.execute(select(User).where(User.openid == info.external_id)).scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        openid=info.external_id,
        nickname=info.nickname,
        phone_tail=info.phone_tail,
        auth_provider=info.auth_provider,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 并发首次登录：另一个请求已创建，回滚后重新查询
        db.rollback()
        user = db.execute(
            select(User).where(User.openid == info.external_id)
        ).scalar_one()
    else:
        db.refresh(user)
    return user
