"""三级熔断器 —— 连续亏损/日亏损/周亏损"""

import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RISK_FILE = os.path.join(PROJECT_ROOT, "output", "risk_state.json")


@dataclass
class CircuitBreaker:
    consecutive_losses: int = 0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    weekly_pnl_pct: float = 0.0
    peak_equity: float = 3000.0
    paused_until: str = ""
    trades_today: list = field(default_factory=list)
    trades_this_week: list = field(default_factory=list)

    # 阈值
    max_consecutive_losses: int = 3     # 连续亏3笔 → 停1天
    max_daily_loss_pct: float = -0.05   # 日亏5% → 清仓
    max_weekly_loss_pct: float = -0.10  # 周亏10% → 本周停
    max_drawdown_pct: float = -0.15     # 回撤15% → 休息5天


def check_before_trade(breaker: CircuitBreaker) -> tuple[bool, str]:
    """开仓前检查，返回(是否可交易, 原因)"""
    today = datetime.now().strftime("%Y%m%d")

    if breaker.paused_until:
        if today <= breaker.paused_until:
            return False, f"熔断暂停至 {breaker.paused_until}"
        else:
            breaker.paused_until = ""  # 解禁

    if breaker.consecutive_losses >= breaker.max_consecutive_losses:
        breaker.paused_until = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        return False, f"连续亏损{breaker.consecutive_losses}笔，停1天"

    if breaker.daily_pnl_pct <= breaker.max_daily_loss_pct:
        breaker.paused_until = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        return False, f"今日亏损{breaker.daily_pnl_pct*100:.1f}%，清仓休息"

    if breaker.weekly_pnl_pct <= breaker.max_weekly_loss_pct:
        # 停到周末
        dt = datetime.now()
        days_to_monday = (7 - dt.weekday()) % 7
        breaker.paused_until = (dt + timedelta(days=days_to_monday)).strftime("%Y%m%d")
        return False, f"本周亏损{breaker.weekly_pnl_pct*100:.1f}%，本周停"

    return True, "OK"


def on_trade_close(breaker: CircuitBreaker, pnl_pct: float):
    """每笔交易平仓后更新状态"""
    breaker.trades_today.append(pnl_pct)
    breaker.trades_this_week.append(pnl_pct)

    if pnl_pct < 0:
        breaker.consecutive_losses += 1
    else:
        breaker.consecutive_losses = 0

    breaker.daily_pnl_pct = sum(breaker.trades_today)
    breaker.weekly_pnl_pct = sum(breaker.trades_this_week)


def on_day_start(breaker: CircuitBreaker, current_equity: float):
    """每日开盘重置日统计"""
    breaker.trades_today = []
    breaker.daily_pnl_pct = 0.0


def on_week_start(breaker: CircuitBreaker):
    """每周一重置周统计"""
    breaker.trades_this_week = []
    breaker.weekly_pnl_pct = 0.0


def save(breaker: CircuitBreaker, date: str):
    """保存风控状态到文件"""
    os.makedirs(os.path.dirname(RISK_FILE), exist_ok=True)
    with open(RISK_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "date": date,
            "consecutive_losses": breaker.consecutive_losses,
            "daily_pnl": breaker.daily_pnl_pct,
            "weekly_pnl": breaker.weekly_pnl_pct,
            "peak_equity": breaker.peak_equity,
            "paused_until": breaker.paused_until,
        }, f, ensure_ascii=False, indent=2)


def load() -> CircuitBreaker:
    """从文件恢复风控状态"""
    if not os.path.exists(RISK_FILE):
        return CircuitBreaker()
    with open(RISK_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return CircuitBreaker(
        consecutive_losses=data.get("consecutive_losses", 0),
        paused_until=data.get("paused_until", ""),
        peak_equity=data.get("peak_equity", 3000.0),
    )
