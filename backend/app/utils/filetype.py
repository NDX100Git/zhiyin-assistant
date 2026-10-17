"""文件类型识别（读文件头 magic number，不只信任客户端传的扩展名 / content-type）。

支持的白名单家族：
- pdf                      %PDF-
- doc / xls / ppt          OLE2 复合文档头（D0 CF 11 E0 A1 B1 1A E1）
- docx / xlsx / pptx       ZIP 容器，需进一步看包内目录（word/ xl/ ppt/）
- jpg / jpeg               FF D8 FF
- png                      89 50 4E 47 0D 0A 1A 0A
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# 扩展名 -> 允许的文件头家族
EXT_FAMILY = {
    "pdf": "pdf",
    "doc": "ole",
    "xls": "ole",
    "ppt": "ole",
    "docx": "ooxml",
    "xlsx": "ooxml",
    "pptx": "ooxml",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
}

PDF_MAGIC = b"%PDF-"
OLE_MAGIC = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
ZIP_MAGIC = b"PK\x03\x04"
JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])
PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])

# OOXML 包内目录前缀 -> 实际类型
OOXML_PREFIX = {"word/": "docx", "xl/": "xlsx", "ppt/": "pptx"}

# 服务端认定的 MIME 类型（不使用客户端上传的 content-type）
MIME_BY_EXT = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


def normalize_ext(filename: str | None) -> str:
    """取小写扩展名（不含点）。没有扩展名时返回空串。"""
    if not filename:
        return ""
    suffix = Path(filename).suffix
    return suffix[1:].lower() if suffix else ""


def sniff_head(head: bytes) -> str:
    """按文件头返回家族名：pdf / ole / zip / jpeg / png / unknown。"""
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return "ole"
    if head.startswith(ZIP_MAGIC):
        return "zip"
    if head.startswith(JPEG_MAGIC):
        return "jpeg"
    if head.startswith(PNG_MAGIC):
        return "png"
    return "unknown"


def detect_ooxml_kind(path: Path) -> str | None:
    """区分 docx / xlsx / pptx：看 ZIP 包内的顶层目录。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return None
    for prefix, kind in OOXML_PREFIX.items():
        if any(name.startswith(prefix) for name in names):
            return kind
    return None


def check_content_matches_ext(path: Path, ext: str) -> tuple[bool, str]:
    """校验文件真实类型是否与扩展名相符。

    返回 (是否通过, 实际类型描述)。不通过时描述用于拼接中文提示。
    """
    family = EXT_FAMILY.get(ext)
    if family is None:
        return False, "不在允许的文件类型内"

    with path.open("rb") as fp:
        head = fp.read(16)
    sniffer = sniff_head(head)

    if family == "ooxml":
        if sniffer != "zip":
            return False, "文件内容不是 Office 文档"
        kind = detect_ooxml_kind(path)
        if kind != ext:
            return False, f"文件内容实际是 {kind or '未知类型'}"
        return True, kind

    if sniffer != family:
        return False, f"文件内容实际是 {sniffer}"
    return True, family
