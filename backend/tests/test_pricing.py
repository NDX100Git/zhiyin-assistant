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


# ---------------------------------------------------------------- 改价护栏（上限 / 零元）


from app.config import get_order_rules, get_pricing_config  # noqa: E402
from app.services.pricing import (  # noqa: E402
    ensure_amount_allowed,
    max_legal_order_amount_cents,
)

_PRICE_KEYS = ("bw_single", "bw_duplex", "color_single", "color_duplex")


def _legal_max_cents() -> int:
    """按配置自己算一遍「合法订单不可能超过的金额」，不抄实现里的数字。"""
    limits = get_order_rules()["limits"]
    pricing = get_pricing_config()
    return (
        int(limits["max_page_count"])
        * int(limits["max_copies"])
        * max(int(pricing[key]) for key in _PRICE_KEYS)
    )


def test_改价上限跟着合法最大单走而不是写死的_1000_元():
    """上限必须 ≥ 配置允许的最大订单金额，否则合法单永远完不成、核销不了。

    历史问题：上限写死 100_000 分（1000 元），而配置允许的最大单是
    1000 页 × 50 份 × 彩色 80 分 = 4_000_000 分，1600 元的合法单直接被 40062 拒。
    """
    ceiling = max_legal_order_amount_cents()
    assert ceiling == _legal_max_cents()
    assert ceiling >= 160_000, "1600 元的合法订单必须落在上限之内"


def test_1600_元的合法订单能通过改价护栏():
    assert ensure_amount_allowed(160_000, system_amount_cents=160_000) == 160_000
    # 沿用已有成交价（不带系统价）也必须能过，否则完成/核销被上限卡住
    assert ensure_amount_allowed(160_000) == 160_000


def test_超过合法最大单的金额仍然被拒():
    ceiling = max_legal_order_amount_cents()
    with pytest.raises(BusinessError) as exc:
        ensure_amount_allowed(ceiling + 1, system_amount_cents=ceiling + 1)
    assert exc.value.code == 40062
    assert str(ceiling) in exc.value.message


def test_改价为零元被拒():
    """0 元与已收款并存会让对账分叉：必须大于 0，免单请走作废流程。"""
    with pytest.raises(BusinessError) as exc:
        ensure_amount_allowed(0, system_amount_cents=630)
    assert exc.value.code == 40061
    assert "大于 0" in exc.value.message


def test_改价为负数仍然被拒():
    with pytest.raises(BusinessError) as exc:
        ensure_amount_allowed(-1, system_amount_cents=630)
    assert exc.value.code == 40061
