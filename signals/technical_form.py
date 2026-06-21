"""F4: 技术形态 (权重15%) - 连板高度 + 均线多头 + 市值 + 相对位置"""

import pandas as pd
from config.settings import FactorWeights


def compute(zt_row: pd.Series, kline_info: dict, weights: FactorWeights) -> float:
    """kline_info = {"bullish": bool, "rel_pos": float}"""
    w = weights.f4_sub
    s1 = _board_height_score(zt_row.get("board_height", 1)) * w.sub1
    s2 = _bullish_score(kline_info.get("bullish", False)) * w.sub2
    s3 = _market_cap_score(zt_row) * w.sub3
    s4 = _position_score(kline_info.get("rel_pos", 0.5)) * w.sub4
    return s1 + s2 + s3 + s4


def _board_height_score(height: int) -> float:
    if height == 1:   return 100
    if height == 2:   return 90
    if height == 3:   return 75
    if height == 4:   return 60
    if height == 5:   return 50
    if height == 6:   return 40
    if height == 7:   return 30
    return 20


def _bullish_score(bullish: bool) -> float:
    return 100 if bullish else 30


def _market_cap_score(row: pd.Series) -> float:
    cap = row.get("流通市值", row.get("float_market_cap", 0))
    try:
        cap = float(cap)
    except (ValueError, TypeError):
        return 50
    cap_yi = cap / 1e8
    if 20 <= cap_yi <= 80:   return 100
    if 80 < cap_yi <= 150:   return 75
    if cap_yi < 20:          return 60
    if 150 < cap_yi <= 300:  return 50
    return 30


def _position_score(rel_pos: float) -> float:
    if 0.5 <= rel_pos <= 0.7:  return 100
    if 0.3 <= rel_pos <= 0.8:  return 75
    if rel_pos < 0.3:          return 50
    return 40
