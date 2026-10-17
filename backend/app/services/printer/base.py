"""打印执行抽象接口。

V1 由老师手动打印：Provider 只返回取件用的下载链接；
V2 接云打印 / 打印机 SDK 时新增实现类并改配置即可，业务代码不动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.order import Order


class PrinterProvider(ABC):
    """打印方式抽象。"""

    name: str = "unknown"

    @abstractmethod
    def print(self, order: Order, file_url: str) -> dict[str, Any]:
        """执行（或准备）打印，返回任务信息，如 job_id、下载链接。"""
