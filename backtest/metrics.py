"""绩效指标: 年化收益、夏普、最大回撤、Calmar、胜率"""

import numpy as np


def calc_metrics(trades: list[dict], equity_curve: list[float], initial_capital: float) -> dict:
    """
    trades: [{"buy_date", "sell_date", "buy_price", "sell_price", "pnl", "pnl_pct"}, ...]
    equity_curve: 每日净值序列
    """
    if not trades:
        return {"total_return": 0, "annual_return": 0, "sharpe": 0,
                "max_drawdown": 0, "calmar": 0, "win_rate": 0, "total_trades": 0}

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] < 0]
    win_rate = len(wins) / len(trades)

    total_return = (equity_curve[-1] - initial_capital) / initial_capital

    # 年化收益 (假设250个交易日)
    trading_days = len(equity_curve)
    annual_return = (1 + total_return) ** (250 / trading_days) - 1 if trading_days > 0 else 0

    # 最大回撤
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (np.array(equity_curve) - peak) / peak
    max_drawdown = abs(drawdown.min())

    # Calmar
    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0

    # 夏普 (无风险利率按2%)
    daily_returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
    excess = np.mean(daily_returns) - 0.02 / 250
    sharpe = (excess / np.std(daily_returns) * np.sqrt(250)) if np.std(daily_returns) > 0 else 0

    avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
    profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 0

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate * 100, 1),
        "total_return": round(total_return * 100, 1),
        "annual_return": round(annual_return * 100, 1),
        "max_drawdown": round(max_drawdown * 100, 1),
        "sharpe": round(sharpe, 2),
        "calmar": round(calmar, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win * 100, 1),
        "avg_loss_pct": round(avg_loss * 100, 1),
    }
