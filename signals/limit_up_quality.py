"""F1: 涨停板质量 (权重30%) - 封板时间 + 封单比 + 回封 + 板上成交"""

import pandas as pd
from config.settings import FactorWeights


def compute(zt_row: pd.Series, weights: FactorWeights) -> float:
    """返回 0~100 的加权得分"""
    w = weights.f1_sub
    s1 = _seal_time_score(zt_row) * w.sub1
    s2 = _seal_amount_score(zt_row) * w.sub2
    s3 = _reseal_score(zt_row) * w.sub3
    s4 = _onboard_volume_score(zt_row) * w.sub4
    return s1 + s2 + s3 + s4


def _seal_time_score(row: pd.Series) -> float:
    val = row.get("seal_time_cleaned", 999999)
    if val <= 93030:   return 100
    if val <= 94500:   return 95
    if val <= 100000:  return 90
    if val <= 101500:  return 85
    if val <= 103000:  return 75
    if val <= 110000:  return 65
    if val <= 113000:  return 55
    if val <= 130500:  return 50
    if val <= 133000:  return 40
    if val <= 140000:  return 30
    if val <= 143000:  return 20
    if val <= 145000:  return 10
    return 5


def _seal_amount_score(row: pd.Series) -> float:
    ratio = row.get("seal_amount_ratio", 0)
    if ratio >= 0.05:  return 100
    if ratio >= 0.03:  return 85
    if ratio >= 0.01:  return 65
    if ratio >= 0.005: return 45
    return 25


def _reseal_score(row: pd.Series) -> float:
    opens = row.get("打开次数", row.get("open_count", 0))
    try:
        opens = int(opens)
    except (ValueError, TypeError):
        opens = 0
    if opens == 0:  return 100
    if opens == 1:  return 75
    if opens == 2:  return 45
    return 20


def _onboard_volume_score(row: pd.Series) -> float:
    onboard_ratio = row.get("onboard_vol_ratio", row.get("板上成交占比", 0))
    try:
        onboard_ratio = float(onboard_ratio)
    except (ValueError, TypeError):
        onboard_ratio = 0
    if onboard_ratio >= 0.6:  return 100
    if onboard_ratio >= 0.4:  return 70
    return 40
