"""文件上传相关路由。

业务代码只依赖统一存储接口（get_storage），不直接调用 MinIO SDK。
上传需要携带 token（铁律：身份标识来自服务端）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.ai import PageCountRequest
from app.schemas.file import FileDeleteRequest
from app.services.doc_parser import parse_document
from app.services.file_service import delete_uploaded_file, load_demo_document, store_upload
from app.utils.response import ok
from app.utils.security import get_current_user

router = APIRouter()


@router.post("/upload", summary="上传文件（需登录）", response_model=None)
def upload(
    file: UploadFile = File(..., description="待体检的文档或图片"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """校验类型与大小后存入私有桶，返回对象 key 与限时签名 URL。"""
    return ok(store_upload(user, file, db))


@router.post("/delete", summary="撤回未下单的上传文件（需登录）", response_model=None)
def delete_file(
    payload: FileDeleteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """只允许上传者本人撤回；已被订单、版本或待确认请求引用的文件拒绝删除。"""
    return ok(delete_uploaded_file(db, user, payload.file_key))


@router.post("/demo", summary="载入服务端示例论文（演示兜底）", response_model=None)
def upload_demo(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """App 端选文件不可用时，用它保证「上传 → AI 体检」链路可演示。"""
    return ok(load_demo_document(user, db))


@router.post("/page-count", summary="读取页数（不调用 AI）", response_model=None)
def page_count(
    payload: PageCountRequest,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """只做文档解析取页数，供「跳过 AI 体检、直接打印」的学生使用。"""
    parsed = parse_document(db, payload.file_key)
    return ok({"file_key": payload.file_key, "page_count": parsed.get("page_count", 1)})
