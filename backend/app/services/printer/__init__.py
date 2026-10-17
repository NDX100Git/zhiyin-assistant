"""打印工厂：业务代码只调用 get_printer_provider()。"""

from __future__ import annotations

from app.config import settings
from app.services.printer.base import PrinterProvider


def get_printer_provider() -> PrinterProvider:
    """按 PRINTER_PROVIDER 返回打印实现。"""
    provider = settings.printer_provider.lower()
    if provider == "manual":
        from app.services.printer.manual import ManualPrinterProvider

        return ManualPrinterProvider()
    if provider == "cloud":
        raise ValueError("云打印将在 V2 提供，当前请使用 PRINTER_PROVIDER=manual")
    raise ValueError(f"未知的打印方式: {settings.printer_provider}")


__all__ = ["PrinterProvider", "get_printer_provider"]
