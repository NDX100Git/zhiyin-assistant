"""计费规则：全部读取 config/pricing.json，金额单位一律为「分」。

总价 = 页数 × 单价 × 份数。禁止浮点数参与金额计算。
"""

from __future__ import annotations

from app.config import get_pricing_config
from app.utils.exceptions import BusinessError


def unit_price_cents(print_side: int, color_mode: int) -> int:
    """按单双面与黑白彩色取单价（分/页）。"""
    config = get_pricing_config()
    key = ("color" if int(color_mode) == 1 else "bw") + (
        "_duplex" if int(print_side) == 2 else "_single"
    )
    price = config.get(key)
    if not isinstance(price, int) or price < 0:
        raise BusinessError(f"计费配置有误：{key} 必须是「分」为单位的整数", code=50010)
    return price


def calc_amount_cents(page_count: int, print_side: int, color_mode: int, copies: int) -> int:
    """计算总金额（分）。"""
    pages = int(page_count)
    count = int(copies)
    if pages < 0 or count < 1:
        raise BusinessError("页数与份数不合法", code=40040)
    return unit_price_cents(print_side, color_mode) * pages * count
