"""计费规则：全部读取 config/pricing.json，金额单位一律为「分」。

总价 = 页数 × 单价 × 份数。禁止浮点数参与金额计算。
"""

from __future__ import annotations

from app.config import get_order_rules, get_pricing_config
from app.utils.exceptions import BusinessError


def unit_price_cents(print_side: int, color_mode: int) -> int:
    """按单双面与黑白彩色取单价（分/页）。

    取值必须显式校验：早期实现用 `== 1` / `== 2` 判断，脏值（"双面" 被 int 成
    别的数、前端传 5）会静默落到 bw_single 这个最低价，学生就少付钱。
    """
    try:
        side = int(print_side)
        color = int(color_mode)
    except (TypeError, ValueError) as exc:
        raise BusinessError("打印参数不合法：单双面与颜色必须是整数", code=40041) from exc
    if side not in (1, 2):
        raise BusinessError(f"单双面取值不合法：{print_side}（1=单面，2=双面）", code=40041)
    if color not in (0, 1):
        raise BusinessError(f"颜色模式取值不合法：{color_mode}（0=黑白，1=彩色）", code=40041)

    config = get_pricing_config()
    key = ("color" if color == 1 else "bw") + ("_duplex" if side == 2 else "_single")
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


# 老师改价的护栏。只挡负数等于没挡：把 300 敲成 3000 元照样落库并现场收款，
# 学生当场扫码就多付十倍；偏离系统价太多时一律要求重新核对。
#
# 绝对上限不能写死一个数字：写死 1000 元时，系统允许的最大合法单（1000 页 × 50 份
# × 彩色 80 分 = 4 万元）里的任何一单都会被 40062 挡在「完成打印」之外，改价、核销
# 全走不通。上限跟着配置里的下单限制走，合法单才不会被护栏拦住。
MAX_AMOUNT_RATIO = 10             # 相对系统价最多 10 倍
MIN_ALLOWANCE_CENTS = 2_000       # 但至少给到 20 元的浮动空间
PRICE_KEYS = ("bw_single", "bw_duplex", "color_single", "color_duplex")


def max_legal_order_amount_cents() -> int:
    """任何合法订单都不可能超过的金额（分），由下单限制与最高单价推出来。

    整单合计页数上限（max_page_count）× 每份份数上限（max_copies）× 最高单价。
    配置里页数上限是**整单合计**（见 order_file_service.normalize_file_items），
    所以文件数不能再乘一遍，否则上限会被放大成几十万元，护栏形同虚设。
    """
    limits = get_order_rules().get("limits", {})
    config = get_pricing_config()
    units = [config.get(key) for key in PRICE_KEYS]
    prices = [int(unit) for unit in units if isinstance(unit, int) and unit >= 0]
    if not prices:
        raise BusinessError("计费配置有误：没有任何可用的页单价", code=50010)
    return int(limits["max_page_count"]) * int(limits["max_copies"]) * max(prices)


def ensure_amount_allowed(amount_cents: int, system_amount_cents: int = 0) -> int:
    """校验改价/成交价：必须大于 0、有绝对上限、相对系统价有倍数上限。返回校验后的金额。

    金额为 0 与已收款并存会让对账分叉（收了一笔钱，账上却是 0 元单），所以 0 元
    一律拒绝；确有免单需求时走作废流程，或者显式登记全额退款，不要用 0 元成交价
    伪造一单「免费」。

    倍数上限取「系统价 × 10」与「20 元」的较大者：1 页黑白单系统价只有 15 分，
    只用倍数会把「老师让价到 2 元」也拦掉，那不是护栏而是故障。
    """
    amount = int(amount_cents)
    if amount < 0:
        raise BusinessError("金额不能为负数", code=40061)
    if amount == 0:
        raise BusinessError(
            "金额必须大于 0 分；确需免单请走作废流程", code=40061
        )
    ceiling = max_legal_order_amount_cents()
    if amount > ceiling:
        raise BusinessError(
            f"金额 {amount} 分超出单笔上限 {ceiling} 分，请核对后重新输入",
            code=40062,
        )
    if system_amount_cents > 0:
        ceiling = max(system_amount_cents * MAX_AMOUNT_RATIO, MIN_ALLOWANCE_CENTS)
        if amount > ceiling:
            raise BusinessError(
                f"金额 {amount} 分明显高于系统计价 {system_amount_cents} 分"
                f"（上限 {ceiling} 分），请核对后重新输入",
                code=40062,
            )
    return amount
