"""对象 key 的生成规则。

铁律：key 由服务端生成，客户端无权指定；不使用原始文件名，
避免路径可枚举猜测，也避开中文文件名的 URL 编码问题。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.utils.filetype import normalize_ext


def build_object_key(filename: str | None, prefix: str = "files") -> str:
    """生成形如 files/2026/10/9f3c....pdf 的对象 key。"""
    ext = normalize_ext(filename) or "bin"
    now = datetime.now()  # 服务端时间
    return f"{prefix}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}.{ext}"
