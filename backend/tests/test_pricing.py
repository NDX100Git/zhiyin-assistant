"""计费规则测试：金额一律用「分」计算。"""

from __future__ import annotations

import pytest

from app.services.pricing import calc_amount_cents, unit_price_cents
from app.utils.exceptions import BusinessError


def test_单价按配置读取():
    assert unit_price_cents(print_side=1, color_mode=0) == 15
    assert unit_price_cents(print_side=2, color_mode=0) == 15
    assert unit_price_cents(print_side=1, color_mode=1) == 80
    assert unit_price_cents(print_side=2, color_mode=1) == 80


def test_总价等于页数乘单价乘份数():
    assert calc_amount_cents(page_count=10, print_side=2, color_mode=0, copies=2) == 300
    assert calc_amount_cents(page_count=7, print_side=1, color_mode=1, copies=3) == 1680


def test_份数或页数非法时报错():
    with pytest.raises(BusinessError):
        calc_amount_cents(page_count=10, print_side=1, color_mode=0, copies=0)
