"""V1 实现：不驱动任何设备，只把文件与打印参数交给老师手动打印。"""

from __future__ import annotations

import logging
from typing import Any

from app.models.order import Order
from app.services.printer.base import PrinterProvider

logger = logging.getLogger(__name__)


class ManualPrinterProvider(PrinterProvider):
    name = "manual"

    def print(self, order: Order, file_url: str) -> dict[str, Any]:
        logger.info("手动打印模式：order_no=%s 页数=%s 单双面=%s",
                    order.order_no, order.page_count, order.print_side)
        return {
            "provider": self.name,
            "job_id": None,
            "file_url": file_url,
            "manual": True,
            "params": {
                "page_count": order.page_count,
                "print_side": order.print_side,
                "color_mode": order.color_mode,
                "copies": order.copies,
                "paper_size": order.paper_size,
            },
        }
