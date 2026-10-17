"""对象存储抽象接口。

业务代码只依赖本接口，不直接调用 MinIO / 云存储 SDK。
V2 换云对象存储时，只需新增一个实现类 + 改一行配置。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO


class FileStorage(ABC):
    """统一的文件存储接口（上传 / 限时签名 / 删除 / 取回）。"""

    @abstractmethod
    def ensure_bucket(self) -> None:
        """确保私有桶存在（幂等）。"""

    @abstractmethod
    def upload(self, key: str, data: BinaryIO, size: int,
               content_type: str | None = None) -> str:
        """上传对象，返回对象 key。桶为私有，禁止公开读。"""

    @abstractmethod
    def signed_url(self, key: str, expires_minutes: int | None = None) -> str:
        """生成限时签名下载 URL（默认取配置的 30 分钟）。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除对象（订单完成 N 小时后清理原文件时使用）。"""

    @abstractmethod
    def download_to_file(self, key: str, dest: Path) -> Path:
        """把对象取回本地文件（阶段 5 文档解析 / 转换使用）。"""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes:
        """读取对象字节（阶段 5 图片 OCR 内联 base64 使用）。"""
