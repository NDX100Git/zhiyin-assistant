"""文件上传相关路由。

业务代码只依赖统一存储接口（get_storage），不直接调用 MinIO SDK。
上传需要携带 token（铁律：身份标识来自服务端）。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.ai import PageCountRequest
from app.schemas.file import FileDeleteRequest
from app.services.doc_parser import parse_document
from app.services.file_service import (
    delete_uploaded_file,
    ensure_files_owned_by,
    load_demo_document,
    store_upload,
)
from app.services.storage import get_storage
from app.utils.filetype import MIME_BY_EXT, normalize_ext
from app.utils.response import ok
from app.utils.security import get_current_user

router = APIRouter()


@router.get("/download", summary="下载文件（需登录，手机端用）", response_model=None)
def download(
    file_key: str = Query(description="对象 key，来自上传或转换接口的返回"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """把对象以附件形式回给客户端。

    为什么手机不直接用预签名地址：签名地址里的 host 是存储服务本身
    （配置里是 127.0.0.1:9000），手机拿到这个地址只会连到手机自己，必然失败。
    走这个接口则始终是后端的 8000 端口，手机只要能打开页面就能下载，
    也不必把存储服务的端口对外暴露。

    顺带补上归属校验：预签名地址一旦发出去，任何拿到链接的人在有效期内都能下载；
    这个接口要求登录，并且只能下载自己的文件。
    """
    ensure_files_owned_by(db, user, [file_key])

    ext = normalize_ext(file_key)
    media_type = MIME_BY_EXT.get(ext, "application/octet-stream")
    # 直接把对象 key 里的随机串当文件名太难认，用「下载.<扩展名>」更直观
    filename = f"zhuyin-{file_key.rsplit('/', 1)[-1]}" if file_key else f"download.{ext}"

    data = get_storage().read_bytes(file_key)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            # filename* 用 RFC 5987 编码，中文名在安卓下载器里才不会变成乱码
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(data)),
        },
    )


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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """只做文档解析取页数，供「跳过 AI 体检、直接打印」的学生使用。

    校验归属：页数背后是整篇正文，不能让登录用户拿别人的 file_key 来读。
    """
    ensure_files_owned_by(db, user, [payload.file_key])
    parsed = parse_document(db, payload.file_key)
    return ok({"file_key": payload.file_key, "page_count": parsed.get("page_count", 1)})
