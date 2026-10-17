"""JWT 签发与鉴权依赖。

铁律：身份标识由服务端签发，客户端只能携带；不采集学号等敏感信息。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.utils.exceptions import BusinessError

ALGORITHM = "HS256"
ROLE_STUDENT = "student"
ROLE_ADMIN = "admin"

bearer_scheme = HTTPBearer(auto_error=False)


def create_token(subject: str, role: str = ROLE_STUDENT,
                 expire_minutes: int | None = None) -> tuple[str, int]:
    """签发 token，返回 (token, 有效期秒数)。"""
    minutes = expire_minutes or settings.jwt_expire_minutes
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, minutes * 60


def decode_token(token: str) -> dict[str, Any]:
    """解析 token；非法或过期抛业务异常。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise BusinessError("登录已过期，请重新登录", code=40101) from exc
    except jwt.PyJWTError as exc:
        raise BusinessError("登录状态无效，请重新登录", code=40101) from exc


def _require_credentials(
    credentials: HTTPAuthorizationCredentials | None,
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise BusinessError("请先登录", code=40100)
    return decode_token(credentials.credentials)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """学生端鉴权依赖：返回当前用户。"""
    payload = _require_credentials(credentials)
    user = db.get(User, int(payload.get("sub", 0) or 0))
    if user is None:
        raise BusinessError("账号不存在，请重新登录", code=40101)
    return user


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """老师端鉴权依赖（阶段 4 使用）：返回管理员账号名。"""
    payload = _require_credentials(credentials)
    if payload.get("role") != ROLE_ADMIN:
        raise BusinessError("需要管理员权限", code=40300)
    return str(payload.get("sub", ""))
