"""F2: 板块效应 (权重25%) - 同概念涨停家数 + 龙头 + 资金 + 排名"""

import pandas as pd
from config.settings import FactorWeights


def compute(zt_row: pd.Series, sector_info: dict, weights: FactorWeights) -> float:
    """sector_info = {"zt_count": N, "is_leader": bool, "fund_sign": 1/-1, "rank_pct": 0~1}"""
    w = weights.f2_sub
    s1 = _density_score(sector_info.get("zt_count", 0)) * w.sub1
    s2 = _leader_score(sector_info.get("is_leader", False)) * w.sub2
    s3 = _fund_flow_score(sector_info.get("fund_sign", 0)) * w.sub3
    s4 = _rank_score(sector_info.get("rank_pct", 0.5)) * w.sub4
    return s1 + s2 + s3 + s4


def _density_score(zt_count: int) -> float:
    if zt_count >= 5:  return 100
    if zt_count >= 3:  return 80
    if zt_count >= 2:  return 60
    if zt_count >= 1:  return 30
    return 10


def _leader_score(is_leader: bool) -> float:
    return 100 if is_leader else 50


def _fund_flow_score(fund_sign: int) -> float:
    if fund_sign > 0:  return 100
    if fund_sign == 0: return 60
    return 30


def _rank_score(rank_pct: float) -> float:
    if rank_pct <= 0.1:  return 100
    if rank_pct <= 0.3:  return 75
    if rank_pct <= 0.5:  return 50
    return 25
