"""小资金品种过滤器 —— 主板only, 低价, 流动性"""

import pandas as pd

# 不能买的板块前缀
BLOCKED_PREFIXES = ("300", "301", "688", "689", "8", "4", "920")

# 价格上限（3000元买2手需要股价<15，买1手需要<30）
MAX_PRICE_2_LOTS = 15.0   # 可分2仓
MAX_PRICE_1_LOT = 30.0    # 只能满仓1只
MIN_PRICE = 3.0           # 排除仙股


def is_main_board(code: str) -> bool:
    """判断是否主板（深A主板000/001/002，沪A主板600/601/603/605）"""
    if not code or len(code) < 3:
        return False
    return not code.startswith(BLOCKED_PREFIXES)


def can_afford(code: str, capital: float, price: float, max_positions: int = 2) -> tuple[bool, int]:
    """
    判断是否能买得起，返回(可买, 最大手数)。
    3000元 → 分2仓每仓约1500 → 股价<15元可买1手
    """
    if price <= 0 or price > MAX_PRICE_1_LOT:
        return False, 0

    max_lots = int(capital * 0.55 / (price * 100))
    if max_lots < 1:
        return False, 0
    return True, max_lots


def filter_small_cap_candidates(candidates, capital: float = 3000.0):
    """从打分候选池中过滤出小资金可买的"""
    filtered = []
    for c in candidates:
        if not is_main_board(c.code):
            continue

        # 价格从zt_pool的原始数据获取（如果有的话）
        can_buy_it, lots = can_afford(c.code, capital, 15.0)  # 保守估计
        if can_buy_it:
            filtered.append(c)

    return filtered


def get_etf_defense_pool() -> list[str]:
    """防守型ETF池 —— 市场差时躲进去"""
    return [
        "513100",  # 纳指ETF (T+0)
        "513500",  # 标普500ETF (T+0)
        "513520",  # 日经ETF (T+0)
        "159941",  # 纳指ETF (T+0)
        "159866",  # 日经ETF (T+0)
        "510050",  # 上证50ETF
        "510300",  # 沪深300ETF
    ]


def is_defense_mode(market: dict) -> bool:
    """判断是否应该进入防守模式（不买个股，躲ETF）"""
    total_zt = market.get("total_zt", 0)
    break_rate = market.get("break_rate", 0)
    prev_premium = market.get("prev_premium", 0)

    if total_zt < 20:
        return True  # 市场极冷
    if break_rate > 0.40:
        return True  # 炸板率太高
    if prev_premium < -2:
        return True  # 昨天打板的今天全亏

    return False
