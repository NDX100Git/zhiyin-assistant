"""文件上传业务逻辑：校验 -> 存私有桶 -> 返回限时签名 URL。

铁律：对象 key 由服务端生成；私有桶只发限时签名 URL；文件访问写日志。
V1 的校验规则来自 config/upload_rules.json，不写死在代码里。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.config import BACKEND_DIR, get_upload_rules, settings
from app.models.user import User
from app.services.storage import build_object_key, get_storage
from app.utils.exceptions import BusinessError
from app.utils.filetype import MIME_BY_EXT, check_content_matches_ext, normalize_ext

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
RUNTIME_TMP_DIR = BACKEND_DIR / "runtime" / "tmp"


def _save_to_temp(upload: UploadFile, ext: str, max_bytes: int) -> tuple[Path, Path, int]:
    """把上传流落到临时文件，同时做大小上限校验。

    返回 (临时目录, 文件路径, 字节数)，由调用方负责清理临时目录。
    注意：本函数内部失败时自行清理临时目录，避免校验不通过时残留垃圾文件。
    """
    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="upload-", dir=RUNTIME_TMP_DIR))
    dest = tmp_dir / f"source.{ext}"

    try:
        size = 0
        upload.file.seek(0)
        with dest.open("wb") as out:
            while True:
                chunk = upload.file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise BusinessError(
                        f"文件超过 {max_bytes // 1024 // 1024}MB 限制", code=40005
                    )
                out.write(chunk)

        if size == 0:
            raise BusinessError("上传的文件是空的", code=40006)
        return tmp_dir, dest, size
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def store_upload(user: User, upload: UploadFile) -> dict:
    """校验并保存上传文件，返回上传结果（含 30 分钟签名 URL）。"""
    rules = get_upload_rules()
    max_bytes = int(rules["max_size_mb"]) * 1024 * 1024
    allowed = {str(item).lower() for item in rules["allowed_extensions"]}
    max_name_len = int(rules.get("max_filename_length", 120))

    filename = (upload.filename or "").strip()
    if not filename:
        raise BusinessError("缺少文件名", code=40003)
    if len(filename) > max_name_len:
        raise BusinessError(f"文件名过长，最多 {max_name_len} 个字符", code=40003)

    ext = normalize_ext(filename)
    if ext not in allowed:
        raise BusinessError(
            f"不支持的文件类型：{ext or '未知'}；仅支持 {', '.join(sorted(allowed))}",
            code=40004,
        )

    tmp_dir: Path | None = None
    try:
        tmp_dir, path, size = _save_to_temp(upload, ext, max_bytes)

        # 读文件头校验真实类型，与扩展名不符直接拒绝（不信任客户端 content-type）
        matched, actual = check_content_matches_ext(path, ext)
        if not matched:
            raise BusinessError(
                f"文件内容与扩展名不符（{actual}），已拒绝上传", code=40007
            )

        key = build_object_key(filename)
        content_type = MIME_BY_EXT.get(ext, "application/octet-stream")
        storage = get_storage()
        with path.open("rb") as fp:
            storage.upload(key, fp, size, content_type)
        signed_url = storage.signed_url(key)

        logger.info("用户 %s 上传文件 %s -> %s (%d 字节)", user.id, filename, key, size)
        return {
            "file_key": key,
            "file_name": filename,
            "size": size,
            "content_type": content_type,
            "signed_url": signed_url,
            "expires_minutes": settings.signed_url_expire_minutes,
        }
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


DEMO_FILE_NAME = "智印助手示例论文.docx"


def load_demo_document(user: User) -> dict:
    """生成并上传一份服务端示例论文，作为 App 端选文件失败时的演示兜底。

    示例文档含格式错误（缺目录、图片超边距、参考文献著录错误等），
    方便在任何平台上把「上传 → AI 体检」链路跑通。
    """
    from scripts.make_demo_docx import build

    demo_path = RUNTIME_TMP_DIR / "demo_thesis_flawed.docx"
    build(demo_path)
    size = demo_path.stat().st_size

    key = build_object_key(DEMO_FILE_NAME)
    storage = get_storage()
    with demo_path.open("rb") as fp:
        storage.upload(key, fp, size, MIME_BY_EXT["docx"])
    signed_url = storage.signed_url(key)

    logger.info("用户 %s 载入示例论文 -> %s", user.id, key)
    return {
        "file_key": key,
        "file_name": DEMO_FILE_NAME,
        "size": size,
        "content_type": MIME_BY_EXT["docx"],
        "signed_url": signed_url,
        "expires_minutes": settings.signed_url_expire_minutes,
        "is_demo": True,
    }
