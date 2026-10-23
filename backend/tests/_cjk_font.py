"""测试夹具共用的中文字体选择：哪个系统有就用哪个，实在没有退回 PyMuPDF 内置。

这些测试要生成带中文的 PDF 或图片，字体不能被某一个系统绑死。
Windows 的 simsun、Linux 的 Noto 与文泉驿、macOS 的苹方都试一遍；
全都找不到时用 ``fontname="china-s"``（PyMuPDF 自带的简体中文字体，无需字体文件）。
"""

from __future__ import annotations

from pathlib import Path

# 按常见程度排，找到第一个存在的就是它
_CANDIDATES = (
    # Windows
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
)


def cjk_font_args() -> dict[str, str]:
    """给 PyMuPDF 的 insert_text 用的字体参数。"""
    for candidate in _CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return {"fontfile": str(path), "fontname": "cjk"}
    return {"fontname": "china-s"}
