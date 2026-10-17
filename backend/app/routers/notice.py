"""公告查询（学生端）。发布接口在老师端，属阶段 4。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.notice import Notice
from app.utils.response import ok
from app.utils.security import get_current_user

router = APIRouter()


@router.get("/list", summary="公告列表（需登录）", response_model=None)
def list_notices(
    limit: int = Query(default=10, ge=1, le=50),
    _: object = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(
        db.execute(
            select(Notice)
            .where(Notice.active == 1)
            .order_by(Notice.created_at.desc(), Notice.id.desc())
            .limit(limit)
        ).scalars()
    )
    return ok(
        {
            "items": [
                {
                    "id": row.id,
                    "content": row.content,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        }
    )
