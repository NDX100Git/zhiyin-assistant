"""登录相关路由。

只调用当前配置的 AuthProvider，不直接依赖任何具体实现类。
本接口是免 token 的（登录后才能拿到 token）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.auth import LoginData, LoginRequest, ProfileRequest
from app.models.user import User
from app.utils.exceptions import BusinessError
from app.utils.security import get_current_user
from app.services.auth import get_auth_provider
from app.services.user_service import get_or_create_user
from app.utils.response import ok
from app.utils.security import ROLE_STUDENT, create_token

router = APIRouter()


@router.post("/login", summary="学生端登录", response_model=None)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    """按配置的 Provider 校验凭据，首次登录自动建用户，返回 JWT。"""
    provider = get_auth_provider()
    info = provider.verify(payload.model_dump())
    user = get_or_create_user(db, info)
    token, expires_in = create_token(str(user.id), ROLE_STUDENT)

    data = LoginData(
        token=token,
        expires_in=expires_in,
        user={"id": user.id, "nickname": user.nickname, "auth_provider": user.auth_provider},
    )
    return ok(data.model_dump())


@router.post("/profile", summary="更新昵称（需登录）", response_model=None)
def update_profile(
    payload: ProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """只允许设置昵称，不采集学号、年级等敏感信息。"""
    nickname = payload.nickname.strip()
    if not nickname:
        raise BusinessError("昵称不能为空", code=40001)
    if len(nickname) > 64:
        raise BusinessError("昵称最多 64 个字符", code=40002)
    user.nickname = nickname
    db.commit()
    db.refresh(user)
    return ok({"id": user.id, "nickname": user.nickname, "auth_provider": user.auth_provider})
