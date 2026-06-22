"""风控工具：ATR动态止损 + Kelly仓位 + 多信号融合"""
import numpy as np


def calc_atr(high, low, close, period=14):
    """ATR 平均真实波幅 —— 动态止损基准"""
    n = len(close)
    if n < period + 1:
        return round(float(np.mean(high - low)), 2)

    tr = []
    for i in range(1, n):
        h_l = high[i] - low[i]
        h_pc = abs(high[i] - close[i - 1])
        l_pc = abs(low[i] - close[i - 1])
        tr.append(max(h_l, h_pc, l_pc))

    atr = float(np.mean(tr[-period:]))
    return round(atr, 2)


def dynamic_stop_loss(entry_price, atr, multiplier=2.0):
    """ATR动态止损：entry - multiplier * ATR"""
    return round(entry_price - multiplier * atr, 2)


def dynamic_take_profit(entry_price, atr, multiplier=3.0, strategy_target=None):
    """ATR动态止盈，优先用策略目标价"""
    if strategy_target and strategy_target > entry_price:
        return strategy_target
    return round(entry_price + multiplier * atr, 2)


def kelly_position(win_rate, avg_win_pct, avg_loss_pct, max_pct=0.8):
    """Kelly公式仓位计算（保守版，限制最大仓位）"""
    if avg_loss_pct == 0 or win_rate <= 0:
        return 0.25
    wr = win_rate / 100
    aw = avg_win_pct / 100
    al = abs(avg_loss_pct) / 100
    kelly = (wr * aw - (1 - wr) * al) / (aw * al) if (aw * al) > 0 else 0.25
    kelly = max(0.1, min(kelly * 0.5, max_pct))  # 半凯利，限制上下限
    return round(kelly, 2)


def score_to_position(score, base_capital=3000):
    """评分→仓位映射：评分越高仓位越大"""
    if score >= 90:
        pct = 0.55
    elif score >= 80:
        pct = 0.45
    elif score >= 70:
        pct = 0.35
    elif score >= 60:
        pct = 0.25
    else:
        pct = 0
    return round(base_capital * pct, 0), pct


def fuse_signals(yijiner_score=None, dp_score=None):
    """多信号融合：两策略同时触发=高置信度"""
    confidence = "normal"
    bonus = 0

    if yijiner_score and dp_score:
        if yijiner_score >= 70 and dp_score >= 60:
            confidence = "high"
            bonus = 10  # 加分
        elif yijiner_score >= 60 or dp_score >= 50:
            confidence = "medium"
            bonus = 5

    return {"confidence": confidence, "bonus": bonus,
            "label": {"high": "⚡高置信(双策略共振)", "medium": "中等置信", "normal": "单策略信号"}[confidence]}
