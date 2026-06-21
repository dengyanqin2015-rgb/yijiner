"""T+0跨境ETF日内扫描 —— 82只低价标的，每天推荐买卖"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ETFPick:
    code: str
    name: str
    price: float
    change_pct: float      # 当日涨跌幅
    volume_ratio: float     # 量比
    trend: str              # "上升" / "下跌" / "震荡"
    signal: str             # "买入" / "观望" / "卖出"
    buy_price: float
    sell_price: float
    stop_loss: float
    reason: str


T0_ETF_POOL = [
    ("513100", "纳指ETF"), ("513500", "标普500ETF"), ("513520", "日经ETF"),
    ("513000", "日经225ETF"), ("513880", "日经225ETF"), ("513870", "纳指ETF"),
    ("159941", "纳指ETF"), ("159866", "日经ETF"), ("159509", "纳指科技ETF"),
    ("513390", "纳指100ETF"), ("159660", "纳指ETF"), ("159696", "纳指ETF"),
    ("513110", "纳指ETF"), ("159501", "纳指ETF"), ("513080", "法国ETF"),
    ("513030", "德国ETF"), ("513050", "中概互联ETF"), ("159612", "标普500ETF"),
    ("513380", "恒生科技ETF"), ("513890", "恒科ETF"), ("513730", "东南亚ETF"),
    ("159920", "恒生ETF"), ("510900", "H股ETF"), ("159615", "恒生ETF"),
    ("159605", "中概互联ETF"), ("159607", "中概互联ETF"),
]


def scan() -> list[ETFPick]:
    """扫描T+0 ETF，返回买入推荐"""
    import akshare as ak

    try:
        df = ak.fund_etf_spot_em()
    except Exception:
        return []

    picks = []
    for code, name in T0_ETF_POOL:
        row = df[df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]

        try:
            price = float(row["最新价"])
            change = float(row["涨跌幅"])
            volume = float(row["成交量"])
        except (ValueError, KeyError):
            continue

        # === 趋势判断 ===
        if change > 1.5:
            trend = "上升"
        elif change < -1.5:
            trend = "下跌"
        else:
            trend = "震荡"

        # === 买卖信号 ===
        if trend == "上升" and price < 3.0 and price > 0.5:
            signal = "买入"
            reason = "上升趋势+低价可多买"
        elif trend == "震荡" and abs(change) < 0.5:
            signal = "观望"
            reason = "横盘等待方向"
        elif trend == "下跌" and change < -2:
            signal = "观望"
            reason = "超跌等企稳"
        else:
            signal = "持有"
            reason = "趋势延续"

        buy_price = round(price, 3)
        sell_price = round(price * 1.02, 3)    # +2%止盈
        stop_loss = round(price * 0.98, 3)     # -2%止损

        # 计算可买份额
        shares = int(3000 / price / 100) * 100

        picks.append(ETFPick(
            code=code, name=name, price=price,
            change_pct=change,
            volume_ratio=1.0,
            trend=trend, signal=signal,
            buy_price=buy_price, sell_price=sell_price,
            stop_loss=stop_loss, reason=reason,
        ))

    # 排序：买入 > 观望 > 持有
    signal_order = {"买入": 0, "观望": 1, "持有": 2}
    picks.sort(key=lambda p: (signal_order.get(p.signal, 9), -abs(p.change_pct)))
    return picks[:10]
