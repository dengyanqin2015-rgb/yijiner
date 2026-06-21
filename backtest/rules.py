"""A股交易规则: T+1、涨跌停、可交易性判断"""

from config.constants import get_limit_rate


def can_buy(code: str, open_price: float, pre_close: float, high: float, low: float) -> bool:
    """判断次日开盘是否可以买入"""
    limit_rate = get_limit_rate(code)
    limit_up = pre_close * (1 + limit_rate)

    if open_price >= limit_up:
        return False  # 开盘涨停，无法买入

    if low >= limit_up:
        return False  # 全天封死一字板

    return True


def can_sell(code: str, open_price: float, pre_close: float, high: float, low: float) -> bool:
    """判断是否可以卖出"""
    limit_rate = get_limit_rate(code)
    limit_down = pre_close * (1 - limit_rate)

    if open_price <= limit_down:
        return False  # 开盘跌停，无法卖出
    return True


def is_tradable(code: str, open_price: float, pre_close: float) -> bool:
    """判断是否可交易（排除停牌等）"""
    if open_price <= 0.01 or pre_close <= 0.01:
        return False
    return True


def get_next_day_limit(code: str, pre_close: float) -> tuple[float, float]:
    """返回次日涨跌停价格"""
    rate = get_limit_rate(code)
    limit_up = pre_close * (1 + rate)
    limit_down = pre_close * (1 - rate)
    return round(limit_up, 2), round(limit_down, 2)
