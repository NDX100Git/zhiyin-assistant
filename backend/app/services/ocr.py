"""图片文字识别（阶段 5-D）。

按 5.4（5）的提示词，用 base64 内联方式把图片随消息发给多模态模型，返回纯文本。
限制：单张 ≤ 32MB，仅支持 jpg/jpeg/png；结果按 file_key 缓存。
"""

from __future__ import annotations

import base64
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.services.doc_parser import get_cached_report, save_report
from app.services.llm import chat
from app.services.storage import get_storage
from app.utils.exceptions import BusinessError
from app.utils.filetype import MIME_BY_EXT, normalize_ext

logger = logging.getLogger(__name__)

RUNTIME_TMP_DIR = BACKEND_DIR / "runtime" / "tmp"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
ALLOWED_EXTS = {"jpg", "jpeg", "png"}

SYSTEM_PROMPT = (
    "请提取图片中的所有文字，保留原有的段落结构与编号顺序，输出纯文本，"
    "不要添加任何解释或总结。"
)


def ocr_image(db: Session, file_key: str, force: bool = False) -> dict:
    """把图片交给多模态模型识别文字。"""
    ext = normalize_ext(file_key)
    if ext not in ALLOWED_EXTS:
        raise BusinessError("OCR 只支持 jpg / jpeg / png 图片", code=40075)

    if not force:
        cached = get_cached_report(db, file_key, "ocr")
        if cached is not None:
            logger.info("命中 OCR 缓存：%s", file_key)
            return cached

    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ocr-", dir=RUNTIME_TMP_DIR) as tmp:
        local_path = get_storage().download_to_file(
            file_key, Path(tmp) / f"image.{ext}"
        )
        size = local_path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise BusinessError("图片超过 32MB，无法识别", code=40076)
        data = local_path.read_bytes()

    mime = MIME_BY_EXT.get(ext, "image/png")
    encoded = base64.b64encode(data).decode("ascii")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请提取这张图片中的全部文字。"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        },
    ]

    text = chat(messages).strip()
    if not text:
        raise BusinessError("未能从图片中识别出文字", code=40077)

    report = {
        "file_key": file_key,
        "text": text,
        "char_count": len(text),
        "image_bytes": len(data),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_report(db, file_key, report, "ocr")
    return report
