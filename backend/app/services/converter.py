"""格式转换（阶段 5-D）：PDF 转 Word。

这是格式转换而非语言任务，用 pdf2docx 完成，绝不调用大模型。
转换结果存回私有桶并返回新的 file_key（前端可继续走体检/下单流程）。
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from docx import Document

from app.config import BACKEND_DIR, settings
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


def _rename_to(name: str | None, ext: str, fallback: str) -> str:
    """把展示名换成目标扩展名。

    build_object_key 是按文件名的扩展名定对象 key 的，如果传进去的名字还带着
    原扩展名（论文.docx 转出来叫 论文.docx），桶里就会存一个扩展名与内容不符的对象，
    下游按扩展名选解析器时会直接判错。
    """
    base = (name or "").strip()
    if not base:
        return fallback
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = stem.strip() or fallback.rsplit(".", 1)[0]
    return f"{stem}.{ext}"


def convert_image_to_docx(
    db: Session,
    file_key: str,
    file_name: str | None = None,
    *,
    user: User | None = None,
    api_key: str | None = None,
) -> dict:
    """图片 → Word：先用多模态模型识别文字，再把文字组装成 docx。

    识别是唯一用到大模型的一步；组装 Word 是纯本地操作。
    学生没配模型密钥时走不通，错误会从 ocr_image 直接冒上来，
    上层据此提示去填密钥，而不是含糊地说转换失败。
    """
    ext = normalize_ext(file_key)
    if ext not in {"jpg", "jpeg", "png"}:
        raise BusinessError("图片转 Word 只支持 jpg / jpeg / png", code=40080)

    # 延迟导入：ocr 依赖 llm 与 doc_parser，放模块顶部容易绕成循环
    from app.services.ocr import ocr_image

    report = ocr_image(db, file_key, api_key=api_key)
    text = str(report.get("text") or "").strip()
    if not text:
        raise BusinessError("未能从图片中识别出文字", code=40077)

    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    storage = get_storage()
    with tempfile.TemporaryDirectory(prefix="ocr-docx-", dir=RUNTIME_TMP_DIR) as tmp:
        docx_path = Path(tmp) / "ocr.docx"
        document = Document()
        # 一行一段：识别结果里的换行就是原来的段落边界。
        # 全部塞进一个段落会让生成的文件整篇黏成一坨，学生拿到手还得重排。
        for line in text.splitlines():
            document.add_paragraph(line.rstrip())
        document.save(str(docx_path))

        target_name = _rename_to(file_name, "docx", "识别结果.docx")
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

    logger.info("图片转 Word 完成：%s -> %s（%d 字）", file_key, new_key, len(text))
    return {
        "file_key": new_key,
        "file_name": target_name,
        "size": size,
        "content_type": MIME_BY_EXT["docx"],
        "signed_url": signed_url,
        "source_file_key": file_key,
        "text": text,
        "char_count": len(text),
        "note": "文字由多模态模型识别，Word 由服务端本地生成",
    }


def convert_docx_to_pdf(
    db: Session,
    file_key: str,
    file_name: str | None = None,
    *,
    user: User | None = None,
) -> dict:
    """Word → PDF：LibreOffice headless 转换，纯格式转换，不碰大模型。"""
    ext = normalize_ext(file_key)
    if ext not in {"doc", "docx"}:
        raise BusinessError("Word 转 PDF 目前只支持 doc / docx", code=40081)

    # 延迟导入：doc_parser 体量大，且它本身也被其它模块共享
    from app.services.doc_parser import convert_to_pdf

    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    storage = get_storage()
    with tempfile.TemporaryDirectory(prefix="topdf-", dir=RUNTIME_TMP_DIR) as tmp:
        work_dir = Path(tmp)
        source_path = storage.download_to_file(file_key, work_dir / f"source.{ext}")
        pdf_path = convert_to_pdf(source_path, work_dir)

        target_name = _rename_to(file_name, "pdf", f"{pdf_path.stem}.pdf")
        new_key = build_object_key(target_name)
        size = pdf_path.stat().st_size
        with pdf_path.open("rb") as fp:
            storage.upload(new_key, fp, size, MIME_BY_EXT["pdf"])
        signed_url = storage.signed_url(new_key)

    if user is not None:
        record_generated_upload(
            db,
            user,
            file_key=new_key,
            file_name=target_name,
            size=size,
            content_type=MIME_BY_EXT["pdf"],
        )

    logger.info("Word 转 PDF 完成：%s -> %s", file_key, new_key)
    return {
        "file_key": new_key,
        "file_name": target_name,
        "size": size,
        "content_type": MIME_BY_EXT["pdf"],
        "signed_url": signed_url,
        "source_file_key": file_key,
        "note": "由服务端用 LibreOffice 转换，未调用大模型",
    }


def pdf_to_docx_file(pdf_path: Path, docx_path: Path) -> Path:
    """本地 PDF → docx（供格式转换接口与文档修复流程共用）。

    pdf2docx 是纯 Python 同步库，本身不带超时也不能取消，扫描件能跑几分钟。
    这里放到独立线程里等，超时就返回业务错误——线程仍会自己跑完，但至少不会
    把请求和 AI 并发槽位一直占住（槽位不释放，其他学生排队 20 秒就超时）。
    """
    timeout = max(1, int(settings.pdf2docx_timeout_seconds))
    errors: list[BaseException] = []

    def _work() -> None:
        try:
            from pdf2docx import Converter

            converter = Converter(str(pdf_path))
            try:
                converter.convert(str(docx_path))
            finally:
                converter.close()
        except BaseException as exc:  # 线程里的异常不会自己冒泡，必须显式带出来
            errors.append(exc)

    worker = threading.Thread(target=_work, name="pdf2docx", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.error("PDF 转 Word 超时（%d 秒）：%s", timeout, pdf_path.name)
        raise BusinessError(
            f"PDF 转 Word 超时（超过 {timeout} 秒），请先把 PDF 拆小或压缩后再试", code=50043
        )
    if errors:
        exc = errors[0]
        logger.exception("PDF 转 Word 失败：%s", pdf_path.name, exc_info=exc)
        raise BusinessError("PDF 转 Word 失败，请确认文件未加密且未损坏", code=50043) from exc

    if not docx_path.exists() or docx_path.stat().st_size == 0:
        raise BusinessError("PDF 转 Word 失败：生成的文件为空", code=50043)
    return docx_path
