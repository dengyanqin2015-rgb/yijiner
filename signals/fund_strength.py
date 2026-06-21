"""F3: 资金强度 (权重20%) - 主力净流入 + 封单金额 + 量比 + 换手率"""

import pandas as pd
from config.settings import FactorWeights


def compute(zt_row: pd.Series, weights: FactorWeights) -> float:
    w = weights.f3_sub
    s1 = _main_inflow_score(zt_row) * w.sub1
    s2 = _seal_amount_abs_score(zt_row) * w.sub2
    s3 = _volume_ratio_score(zt_row) * w.sub3
    s4 = _turnover_score(zt_row) * w.sub4
    return s1 + s2 + s3 + s4


def _main_inflow_score(row: pd.Series) -> float:
    val = row.get("主力净流入", row.get("main_net_inflow", 0))
    try:
        val = float(val)
    except (ValueError, TypeError):
        val = 0
    if val > 1e8:   return 100
    if val > 5e7:   return 85
    if val > 2e7:   return 70
    if val > 0:     return 55
    if val > -5e7:  return 40
    return 25


def _seal_amount_abs_score(row: pd.Series) -> float:
    val = row.get("封单金额", row.get("seal_amount", 0))
    try:
        val = float(val)
    except (ValueError, TypeError):
        val = 0
    if val > 1e8:   return 100
    if val > 5e7:   return 80
    if val > 2e7:   return 60
    if val > 1e7:   return 40
    return 20


def _volume_ratio_score(row: pd.Series) -> float:
    vr = row.get("量比", row.get("volume_ratio", 0))
    try:
        vr = float(vr)
    except (ValueError, TypeError):
        vr = 1
    if 2 <= vr <= 5:    return 100
    if 1.5 <= vr <= 8:  return 75
    if 1 <= vr <= 1.5:  return 50
    return 25


def _turnover_score(row: pd.Series) -> float:
    tr = row.get("换手率", row.get("turnover_rate", 0))
    try:
        tr = float(tr)
    except (ValueError, TypeError):
        tr = 5
    if 5 <= tr <= 15:   return 100
    if 3 <= tr <= 20:   return 75
    if tr < 3:          return 50
    return 30
