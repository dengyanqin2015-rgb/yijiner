"""持仓卖出建议: 次日开盘卖出 / 止损 / 止盈"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SellAdvice:
    code: str
    name: str
    buy_price: float
    current_price: float
    pnl_pct: float
    hold_days: int
    action: str  # "卖出" / "止损" / "止盈" / "持有"


def generate(positions: list[dict], latest_prices: dict) -> list[SellAdvice]:
    """根据当前持仓和最新价格生成卖出建议"""
    advices = []
    for pos in positions:
        code = pos["code"]
        name = pos.get("name", "")
        buy_price = pos["buy_price"]
        hold_days = pos.get("hold_days", 1)
        current_price = latest_prices.get(code, buy_price)
        pnl_pct = (current_price - buy_price) / buy_price

        if pnl_pct >= 0.03:
            action = "止盈"
        elif pnl_pct <= -0.05:
            action = "止损"
        elif hold_days >= 2:
            action = "卖出"
        else:
            action = "持有"

        advices.append(SellAdvice(
            code=code, name=name, buy_price=buy_price,
            current_price=current_price, pnl_pct=pnl_pct,
            hold_days=hold_days, action=action,
        ))
    return advices
