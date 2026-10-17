"""文档解析服务（阶段 5-A）。

管线：
  - .doc/.docx/.ppt/.pptx/.xls/.xlsx → LibreOffice headless 转 PDF
  - .pdf                             → 直接解析
  - 图片                             → 交给 OCR（阶段 5-D）
PDF 用 PyMuPDF 提取：页数、每页文本、图片数量与位置尺寸、页边距、空白页、参考文献、章节。
解析结果按 file_key 缓存到 ai_reports（report_json.kind = "parse"），相同文件不重复解析。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from statistics import median
from xml.etree import ElementTree

import fitz  # PyMuPDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, settings
from app.models.ai_report import AiReport
from app.services.storage import get_storage
from app.utils.exceptions import BusinessError
from app.utils.filetype import normalize_ext

logger = logging.getLogger(__name__)

PT_TO_CM = 2.54 / 72
RUNTIME_TMP_DIR = BACKEND_DIR / "runtime" / "tmp"

OFFICE_EXTS = {"doc", "docx", "ppt", "pptx", "xls", "xlsx"}
IMAGE_EXTS = {"jpg", "jpeg", "png"}

# 章节识别：标准结构名 + “第X章 / 1.1” 式标题
STRUCTURE_KEYWORDS = ("摘要", "关键词", "目录", "参考文献", "致谢", "Abstract", "Keywords")
# 注意：必须开 MULTILINE，否则 ^ 只匹配整篇开头，章节标题会漏检
CHAPTER_PATTERN = re.compile(
    r"^\s*(第[一二三四五六七八九十百]+章|[0-9]{1,2}(\.[0-9]{1,2})*\s+\S)", re.MULTILINE
)
REFERENCE_ITEM_PATTERN = re.compile(r"^\s*[\[［](\d+)[\]］]\s*(.*)$")
BLANK_TEXT_THRESHOLD = 10  # 少于该字符且无图，视为空白页
MIN_TYPOGRAPHY_CHARS = 200  # 正文样本少于该字数时不做排版属性判断
# Word 的"单倍行距"基线间距约为字号的 1.2 倍，用它把实测间距换算成行距倍数
SINGLE_LINE_FACTOR = 1.2


# ---------------------------------------------------------------- 缓存
def get_cached_report(db: Session, file_key: str, kind: str = "parse") -> dict | None:
    """按 file_key 取缓存报告（报告类型用 report_json.kind 区分）。"""
    row = db.execute(
        select(AiReport)
        .where(AiReport.file_key == file_key)
        .where(AiReport.report_json["kind"].as_string() == kind)
        .order_by(AiReport.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.report_json if row else None


def save_report(db: Session, file_key: str, payload: dict, kind: str = "parse") -> dict:
    """把报告写入 ai_reports 缓存表。"""
    data = {**payload, "kind": kind}
    db.add(AiReport(file_key=file_key, report_json=data))
    db.commit()
    return data


# ---------------------------------------------------------------- 页数快读
def quick_page_count(path: Path) -> int | None:
    """优先从 docx 的 docProps/app.xml 读 Pages 属性（Word 保存过才有）。

    取不到返回 None，由调用方决定是否回退到 LibreOffice 转换统计页数。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open("docProps/app.xml") as fp:
                root = ElementTree.parse(fp).getroot()
    except (zipfile.BadZipFile, KeyError, OSError, ElementTree.ParseError):
        return None

    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "Pages":
            try:
                value = int((element.text or "").strip())
            except ValueError:
                return None
            return value if value > 0 else None
    return None


# ---------------------------------------------------------------- 转换
def _file_url(path: Path) -> str:
    return "file:///" + str(path).replace("\\", "/").lstrip("/")


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _subprocess_temp_dir(out_dir: Path) -> Path:
    """给 LibreOffice 子进程准备纯 ASCII 的临时目录。

    实测：当 TEMP/TMP 指向含中文的路径（如 C:\\Users\\某用户\\AppData\\Local\\Temp）时，
    LibreOffice 会报 "source file could not be loaded" 而无法转换文档，
    因此这里显式覆盖子进程的 TMP/TEMP。
    """
    candidate = out_dir / "tmp"
    if _is_ascii_path(candidate):
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    fallback = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp" / f"zhiyin-lo-{os.getpid()}"
    fallback.mkdir(parents=True, exist_ok=True)
    logger.warning("项目路径含非 ASCII 字符，LibreOffice 临时目录回退到 %s", fallback)
    return fallback


def convert_to_pdf(input_path: Path, out_dir: Path) -> Path:
    """用 LibreOffice headless 把 Office 文档转成 PDF。路径读配置，不硬编码。"""
    soffice = Path(settings.soffice_path)
    if not soffice.exists():
        raise BusinessError(
            f"未找到 LibreOffice：{soffice}，请在 .env 中配置 SOFFICE_PATH", code=50040
        )

    # 每次转换用独立的 UserInstallation，避免多请求互相转发到一个实例
    profile_dir = out_dir / "lo-profile"
    stdout_log = out_dir / "soffice.out.log"
    stderr_log = out_dir / "soffice.err.log"
    cmd = [
        str(soffice),
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nologo",
        f"-env:UserInstallation={_file_url(profile_dir)}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(input_path),
    ]
    logger.info("调用 LibreOffice 转换：%s", input_path.name)

    # 注意：不能用 subprocess.PIPE 捕获输出。LibreOffice 会派生后台子进程并继承管道句柄，
    # 导致父进程已退出但管道不关闭、读取永久阻塞。改为重定向到日志文件。
    child_env = {**os.environ}
    child_env["TMP"] = child_env["TEMP"] = str(_subprocess_temp_dir(out_dir))
    with stdout_log.open("wb") as stdout_fp, stderr_log.open("wb") as stderr_fp:
        proc = subprocess.Popen(
            cmd, stdout=stdout_fp, stderr=stderr_fp, cwd=str(out_dir), env=child_env
        )
        try:
            proc.wait(timeout=settings.soffice_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise BusinessError("文档转换超时，请稍后重试", code=50041) from exc

    output = out_dir / f"{input_path.stem}.pdf"
    if not output.exists():
        detail = stderr_log.read_text(encoding="utf-8", errors="ignore").strip()
        if not detail:
            detail = stdout_log.read_text(encoding="utf-8", errors="ignore").strip()
        detail = " ".join(detail.split())[:200]
        raise BusinessError(f"文档转换失败：{detail or '未知原因'}", code=50042)
    return output


def _ensure_pdf(local_path: Path, ext: str, work_dir: Path) -> tuple[Path, str | None]:
    """把上传文件变成可解析的 PDF，返回 (pdf 路径, 转换来源)。"""
    if ext == "pdf":
        return local_path, None
    if ext in OFFICE_EXTS:
        return convert_to_pdf(local_path, work_dir), ext
    if ext in IMAGE_EXTS:
        raise BusinessError("图片请使用 OCR 接口识别文字", code=40070)
    raise BusinessError(f"暂不支持解析该文件类型：{ext or '未知'}", code=40071)


# ---------------------------------------------------------------- PDF 解析
def _points_to_cm(value: float) -> float:
    return round(value * PT_TO_CM, 2)


def _page_margins(pdf_page: fitz.Page) -> dict | None:
    """用文本块坐标推算页边距（cm）。页面没有文本时返回 None。"""
    blocks = [b for b in pdf_page.get_text("blocks") if len(b) >= 5 and str(b[4]).strip()]
    if not blocks:
        return None
    rect = pdf_page.rect
    left = min(block[0] for block in blocks)
    top = min(block[1] for block in blocks)
    right = max(block[2] for block in blocks)
    bottom = max(block[3] for block in blocks)
    return {
        "top_cm": _points_to_cm(top),
        "bottom_cm": _points_to_cm(rect.height - bottom),
        "left_cm": _points_to_cm(left),
        "right_cm": _points_to_cm(rect.width - right),
    }


def _page_images(pdf_page: fitz.Page) -> list[dict]:
    """页面内图片的位置与尺寸（cm）。"""
    images: list[dict] = []
    for image in pdf_page.get_images(full=True):
        xref = image[0]
        colorspace = str(image[5] or "")  # 用于判断是否含彩色图片
        for rect in pdf_page.get_image_rects(xref):
            images.append(
                {
                    "xref": xref,
                    "color": colorspace not in {"DeviceGray", "G", "1", ""},
                    "x_cm": _points_to_cm(rect.x0),
                    "y_cm": _points_to_cm(rect.y0),
                    "width_cm": _points_to_cm(rect.width),
                    "height_cm": _points_to_cm(rect.height),
                }
            )
    return images


def detect_sections(full_text: str) -> list[str]:
    """识别论文章节（用于 5-B 的结构完整性与顺序校验）。"""
    positions: dict[str, int] = {}
    for keyword in STRUCTURE_KEYWORDS:
        index = full_text.find(keyword)
        if index >= 0:
            positions[keyword] = index

    for match in CHAPTER_PATTERN.finditer(full_text):
        positions.setdefault("正文", match.start())
        break

    return [name for name, _ in sorted(positions.items(), key=lambda item: item[1])]


def extract_references(full_text: str) -> list[dict]:
    """提取参考文献条目（以 [数字] 开头的连续段落）。"""
    marker = full_text.find("参考文献")
    body = full_text[marker:] if marker >= 0 else full_text

    items: list[dict] = []
    current: dict | None = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = REFERENCE_ITEM_PATTERN.match(line)
        if match:
            if current is not None:
                items.append(current)
            current = {"index": int(match.group(1)), "text": match.group(2).strip()}
        elif current is not None:
            current["text"] = f"{current['text']} {line}".strip()
    if current is not None:
        items.append(current)
    return items


def parse_pdf(pdf_path: Path) -> dict:
    """PDF → 统一结构 JSON。"""
    pages: list[dict] = []
    typography = typography_stats(pdf_path)
    with fitz.open(pdf_path) as doc:
        if doc.page_count == 0:
            raise BusinessError("文档没有任何页面", code=40072)

        for page_index in range(doc.page_count):
            pdf_page = doc.load_page(page_index)
            text = pdf_page.get_text("text") or ""
            images = _page_images(pdf_page)
            pages.append(
                {
                    "index": page_index + 1,
                    "width_cm": _points_to_cm(pdf_page.rect.width),
                    "height_cm": _points_to_cm(pdf_page.rect.height),
                    "char_count": len(text.strip()),
                    "blank": len(text.strip()) < BLANK_TEXT_THRESHOLD and not images,
                    "image_count": len(images),
                    "images": images,
                    "margins_cm": _page_margins(pdf_page),
                    "text": text,
                }
            )

        first_page = doc.load_page(0)
        page_size = {
            "width_cm": _points_to_cm(first_page.rect.width),
            "height_cm": _points_to_cm(first_page.rect.height),
        }

    full_text = "\n".join(page["text"] for page in pages)
    return {
        "page_count": len(pages),
        "page_size_cm": page_size,
        "sections": detect_sections(full_text),
        "references": extract_references(full_text),
        "blank_pages": [page["index"] for page in pages if page["blank"]],
        "pages_with_images": [page["index"] for page in pages if page["image_count"]],
        "text_chars": len(full_text.strip()),
        "typography": typography,
        "pages": pages,
    }


def typography_stats(pdf_path: Path) -> dict:
    """提取正文字体、字号与行距（用于排版属性检查）。

    做法：按字符数统计出现最多的 (字体, 字号) 组合作为正文字体；
    行距用同一文本块内相邻行的基线间距换算：实测间距 ÷ (字号 × 1.2)。
    """
    font_counter: Counter[tuple[str, float]] = Counter()
    gaps: list[tuple[float, float]] = []

    with fitz.open(pdf_path) as doc:
        for page in doc:
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                previous_bottom: float | None = None
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(str(span.get("text", "")) for span in spans)
                    if not text.strip():
                        continue
                    for span in spans:
                        content = str(span.get("text", "")).strip()
                        size = round(float(span.get("size") or 0), 1)
                        if content and size > 0:
                            font_counter[(str(span.get("font") or ""), size)] += len(content)

                    bottom = float(line.get("bbox", (0, 0, 0, 0))[3])
                    size = max((float(span.get("size") or 0) for span in spans), default=0.0)
                    if previous_bottom is not None and size > 0:
                        gap = bottom - previous_bottom
                        if 0 < gap < 120:
                            gaps.append((round(size, 1), gap))
                    previous_bottom = bottom

    total_chars = sum(font_counter.values())
    if total_chars < MIN_TYPOGRAPHY_CHARS or not font_counter:
        return {"available": False, "sampled_chars": total_chars}

    (font_name, size_pt), matched_chars = font_counter.most_common(1)[0]
    body_gaps = [gap for size, gap in gaps if abs(size - size_pt) <= 0.6]
    line_spacing = None
    if body_gaps:
        line_spacing = round(median(body_gaps) / (size_pt * SINGLE_LINE_FACTOR), 2)

    return {
        "available": True,
        "sampled_chars": total_chars,
        "body_font": normalize_font_name(font_name),
        "body_font_raw": font_name,
        "body_size_pt": size_pt,
        "body_ratio": round(matched_chars / total_chars, 2),
        "line_spacing": line_spacing,
        "font_histogram": [
            {"font": normalize_font_name(name), "size_pt": size, "chars": count}
            for (name, size), count in font_counter.most_common(5)
        ],
    }


# PDF 里的字体名常见英文写法，统一映射成中文名便于与规则比对
FONT_ALIASES = {
    "simsun": "宋体",
    "nsimsun": "新宋体",
    "simhei": "黑体",
    "kaiti": "楷体",
    "simkai": "楷体",
    "fangsong": "仿宋",
    "simfang": "仿宋",
    "microsoftyahei": "微软雅黑",
    "microsoftyaheiui": "微软雅黑",
    "dengxian": "等线",
    "songti": "宋体",
    "heiti": "黑体",
}


def normalize_font_name(raw_name: str) -> str:
    """去掉子集前缀并统一常见中西文字体名（SimSun → 宋体）。"""
    name = re.sub(r"^[A-Z]{6}\+", "", (raw_name or "").strip())
    for suffix in ("-Bold", "-Italic", "-BoldItalic", ",Bold", ",Italic"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    compact = re.sub(r"[\s\-_,]", "", name).lower()
    return FONT_ALIASES.get(compact, name)


# ---------------------------------------------------------------- 对外入口
def parse_document(db: Session, file_key: str, force: bool = False) -> dict:
    """解析文档（带缓存）。file_key 由上传接口返回。"""
    if not file_key:
        raise BusinessError("缺少文件标识", code=40073)
    if not force:
        cached = get_cached_report(db, file_key, "parse")
        if cached is not None:
            logger.info("命中解析缓存：%s", file_key)
            return cached

    ext = normalize_ext(file_key)
    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parse-", dir=RUNTIME_TMP_DIR) as tmp:
        work_dir = Path(tmp)
        local_path = get_storage().download_to_file(file_key, work_dir / f"source.{ext or 'bin'}")
        pdf_path, converted_from = _ensure_pdf(local_path, ext, work_dir)
        report = parse_pdf(pdf_path)

        report["file_key"] = file_key
        report["source"] = {
            "ext": ext,
            "converted_from": converted_from,
            "page_count_fast": quick_page_count(local_path) if ext == "docx" else None,
        }
        # 页数优先用 docx 自带的 Pages；缺失时以转换后的 PDF 页数为准
        if report["source"]["page_count_fast"]:
            report["page_count_from_app_xml"] = report["source"]["page_count_fast"]

    save_report(db, file_key, report, "parse")
    return report
