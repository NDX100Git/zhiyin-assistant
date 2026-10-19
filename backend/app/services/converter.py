"""格式转换（阶段 5-D）：PDF 转 Word。

这是格式转换而非语言任务，用 pdf2docx 完成，绝不调用大模型。
转换结果存回私有桶并返回新的 file_key（前端可继续走体检/下单流程）。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.models.user import User
from app.services.file_service import record_generated_upload
from app.services.storage import build_object_key, get_storage
from app.utils.exceptions import BusinessError
from app.utils.filetype import MIME_BY_EXT, normalize_ext

logger = logging.getLogger(__name__)

RUNTIME_TMP_DIR = BACKEND_DIR / "runtime" / "tmp"


def convert_pdf_to_docx(
    db: Session,
    file_key: str,
    file_name: str | None = None,
    *,
    user: User | None = None,
) -> dict:
    """PDF → Word（.docx）。扫描件请先 OCR，否则正文可能为空。

    传入 `user` 时把生成的文件写进上传记录，归属当前用户；否则学生无法撤回它。
    """
    ext = normalize_ext(file_key)
    if ext != "pdf":
        raise BusinessError("目前只支持 PDF 转 Word", code=40078)

    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    storage = get_storage()
    with tempfile.TemporaryDirectory(prefix="convert-", dir=RUNTIME_TMP_DIR) as tmp:
        work_dir = Path(tmp)
        pdf_path = storage.download_to_file(file_key, work_dir / "source.pdf")
        docx_path = work_dir / "converted.docx"

        pdf_to_docx_file(pdf_path, docx_path)

        target_name = file_name or "converted.docx"
        new_key = build_object_key(target_name)
        size = docx_path.stat().st_size
        with docx_path.open("rb") as fp:
            storage.upload(new_key, fp, size, MIME_BY_EXT["docx"])
        signed_url = storage.signed_url(new_key)

    if user is not None:
        record_generated_upload(
            db,
            user,
            file_key=new_key,
            file_name=target_name,
            size=size,
            content_type=MIME_BY_EXT["docx"],
        )

    logger.info("PDF 转 Word 完成：%s -> %s", file_key, new_key)
    return {
        "file_key": new_key,
        "file_name": target_name,
        "size": size,
        "content_type": MIME_BY_EXT["docx"],
        "signed_url": signed_url,
        "source_file_key": file_key,
        "note": "由服务端用 pdf2docx 转换，未调用大模型",
    }


def pdf_to_docx_file(pdf_path: Path, docx_path: Path) -> Path:
    """本地 PDF → docx（供格式转换接口与文档修复流程共用）。"""
    try:
        from pdf2docx import Converter

        converter = Converter(str(pdf_path))
        try:
            converter.convert(str(docx_path))
        finally:
            converter.close()
    except Exception as exc:  # 转换失败统一转成业务错误，避免暴露底层栈
        logger.exception("PDF 转 Word 失败：%s", pdf_path.name)
        raise BusinessError("PDF 转 Word 失败，请确认文件未加密且未损坏", code=50043) from exc

    if not docx_path.exists() or docx_path.stat().st_size == 0:
        raise BusinessError("PDF 转 Word 失败：生成的文件为空", code=50043)
    return docx_path
