"""F5: 市场情绪 (权重10%) - 全市场涨停数 + 炸板率 + 最高板 + 昨日溢价"""

import pandas as pd
from config.settings import FactorWeights


def compute(market: dict, weights: FactorWeights) -> float:
    """market = {"total_zt": N, "break_rate": 0~1, "max_board": N, "prev_premium": %}"""
    w = weights.f5_sub
    s1 = _total_zt_score(market.get("total_zt", 0)) * w.sub1
    s2 = _break_rate_score(market.get("break_rate", 0)) * w.sub2
    s3 = _max_board_score(market.get("max_board", 1)) * w.sub3
    s4 = _prev_premium_score(market.get("prev_premium", 0)) * w.sub4
    return s1 + s2 + s3 + s4


def _total_zt_score(total_zt: int) -> float:
    if total_zt >= 80:  return 100
    if total_zt >= 50:  return 75
    if total_zt >= 30:  return 50
    return 25


def _break_rate_score(rate: float) -> float:
    if rate < 0.20:  return 100
    if rate < 0.30:  return 75
    if rate < 0.40:  return 50
    return 25


def _max_board_score(max_board: int) -> float:
    if max_board >= 7:  return 100
    if max_board >= 5:  return 85
    if max_board >= 3:  return 70
    if max_board >= 1:  return 50
    return 30


def _prev_premium_score(premium: float) -> float:
    if premium > 3:   return 100
    if premium > 1:   return 75
    if premium > 0:   return 50
    return 25
