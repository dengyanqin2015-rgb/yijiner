"""目标函数: params → backtest → Calmar Ratio"""

import numpy as np
from config.settings import FactorWeights, BacktestConfig, SubWeights
from backtest.engine import run_backtest


def params_to_weights(params: dict) -> FactorWeights:
    """将参数字典转成 FactorWeights 对象"""
    w = FactorWeights(
        limit_up_quality=params.get("w1", 0.30),
        sector_effect=params.get("w2", 0.25),
        fund_strength=params.get("w3", 0.20),
        technical_form=params.get("w4", 0.15),
        market_sentiment=params.get("w5", 0.10),
    )
    w.f1_sub = SubWeights(
        params.get("f1_s1", 0.35), params.get("f1_s2", 0.25),
        params.get("f1_s3", 0.25), params.get("f1_s4", 0.15))
    w.f2_sub = SubWeights(
        params.get("f2_s1", 0.35), params.get("f2_s2", 0.30),
        params.get("f2_s3", 0.20), params.get("f2_s4", 0.15))
    w.f3_sub = SubWeights(
        params.get("f3_s1", 0.35), params.get("f3_s2", 0.25),
        params.get("f3_s3", 0.20), params.get("f3_s4", 0.20))
    w.f4_sub = SubWeights(
        params.get("f4_s1", 0.25), params.get("f4_s2", 0.30),
        params.get("f4_s3", 0.25), params.get("f4_s4", 0.20))
    w.f5_sub = SubWeights(
        params.get("f5_s1", 0.30), params.get("f5_s2", 0.25),
        params.get("f5_s3", 0.25), params.get("f5_s4", 0.20))
    return w


def fitness(params: dict, config: BacktestConfig = None) -> float:
    """适应度 = Calmar Ratio。越大越好"""
    if config is None:
        config = BacktestConfig()
    try:
        weights = params_to_weights(params)
        result = run_backtest(config=config, weights=weights)
        metrics = result.get("metrics", {})
        calmar = metrics.get("calmar", 0)
        win_rate = metrics.get("win_rate", 0)
        total_trades = metrics.get("total_trades", 0)

        # 惩罚项
        penalty = 1.0
        if win_rate < 40:
            penalty *= 0.5
        if total_trades < 30:
            penalty *= 0.5
        return calmar * penalty
    except Exception:
        return -999.0
