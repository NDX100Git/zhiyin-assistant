"""文件上传相关路由。

业务代码只依赖统一存储接口（get_storage），不直接调用 MinIO SDK。
上传需要携带 token（铁律：身份标识来自服务端）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.models.user import User
from app.services.file_service import load_demo_document, store_upload
from app.utils.response import ok
from app.utils.security import get_current_user

router = APIRouter()


@router.post("/upload", summary="上传文件（需登录）", response_model=None)
def upload(
    file: UploadFile = File(..., description="待体检的文档或图片"),
    user: User = Depends(get_current_user),
) -> dict:
    """校验类型与大小后存入私有桶，返回对象 key 与限时签名 URL。"""
    return ok(store_upload(user, file))


@router.post("/demo", summary="载入服务端示例论文（演示兜底）", response_model=None)
def upload_demo(user: User = Depends(get_current_user)) -> dict:
    """App 端选文件不可用时，用它保证「上传 → AI 体检」链路可演示。"""
    return ok(load_demo_document(user))
