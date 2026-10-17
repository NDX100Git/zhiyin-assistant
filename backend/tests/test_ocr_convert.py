"""OCR 与 PDF 转 Word（阶段 5-D）测试。"""

from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest
from docx import Document
from sqlalchemy import func, select

from app.models.ai_report import AiReport
from app.services.converter import convert_pdf_to_docx
from app.services.ocr import ocr_image
from app.services.storage import build_object_key
from app.utils.exceptions import BusinessError

CJK_FONT = Path(r"C:\Windows\Fonts\simsun.ttc")


def _make_png(path: Path, text: str = "论文格式体检") -> Path:
    """生成一张带中文文字的图片，用于 OCR 流程测试。"""
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=200)
        if CJK_FONT.exists():
            page.insert_text((30, 100), text, fontsize=24, fontfile=str(CJK_FONT), fontname="simsun")
        else:
            page.insert_text((30, 100), text, fontsize=24, fontname="china-s")
        pix = page.get_pixmap(dpi=150)
        pix.save(str(path))
    return path


def _make_pdf(path: Path) -> Path:
    with fitz.open() as doc:
        page = doc.new_page()
        if CJK_FONT.exists():
            page.insert_text((72, 100), "摘要", fontsize=16, fontfile=str(CJK_FONT), fontname="simsun")
            page.insert_text(
                (72, 130),
                "本文研究论文格式检查方法。",
                fontsize=12,
                fontfile=str(CJK_FONT),
                fontname="simsun",
            )
        else:
            page.insert_text((72, 100), "Abstract", fontsize=16)
        doc.save(str(path))
    return path


def test_OCR调用多模态模型并缓存(db_session, test_storage, tmp_path, monkeypatch):
    png_path = _make_png(tmp_path / "shot.png")
    key = build_object_key("shot.png")
    data = png_path.read_bytes()
    test_storage.upload(key, io.BytesIO(data), len(data), "image/png")

    captured: dict = {}

    def fake_chat(messages, json_mode=False, thinking=False, temperature=None):
        captured["messages"] = messages
        return "论文格式体检\n第二行文字"

    monkeypatch.setattr("app.services.ocr.chat", fake_chat)

    result = ocr_image(db_session, key)
    assert result["text"].startswith("论文格式体检")
    assert result["char_count"] > 0

    # 多模态消息结构：文本 + base64 内联图片
    user_content = captured["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    again = ocr_image(db_session, key)
    assert again["text"] == result["text"]
    rows = db_session.execute(
        select(func.count()).select_from(AiReport).where(AiReport.file_key == key)
    ).scalar_one()
    assert rows == 1  # 命中缓存，不重复调用模型


def test_OCR拒绝非图片(db_session, test_storage, tmp_path):
    pdf_path = _make_pdf(tmp_path / "doc.pdf")
    key = build_object_key("doc.pdf")
    data = pdf_path.read_bytes()
    test_storage.upload(key, io.BytesIO(data), len(data), "application/pdf")

    with pytest.raises(BusinessError) as exc:
        ocr_image(db_session, key)
    assert "jpg" in exc.value.message


def test_PDF转Word真实转换(db_session, test_storage, tmp_path):
    pdf_path = _make_pdf(tmp_path / "source.pdf")
    key = build_object_key("source.pdf")
    data = pdf_path.read_bytes()
    test_storage.upload(key, io.BytesIO(data), len(data), "application/pdf")

    result = convert_pdf_to_docx(db_session, key, file_name="转换结果.docx")

    assert result["file_key"].endswith(".docx")
    assert result["source_file_key"] == key
    assert result["size"] > 0
    assert "未调用大模型" in result["note"]

    # 新文件确实可被 Word 解析
    stored = test_storage.read_bytes(result["file_key"])
    document = Document(io.BytesIO(stored))
    assert len(document.paragraphs) >= 1


def test_转换拒绝非PDF(db_session, test_storage, tmp_path):
    png_path = _make_png(tmp_path / "shot.png")
    key = build_object_key("shot.png")
    data = png_path.read_bytes()
    test_storage.upload(key, io.BytesIO(data), len(data), "image/png")

    with pytest.raises(BusinessError) as exc:
        convert_pdf_to_docx(db_session, key)
    assert "PDF" in exc.value.message
