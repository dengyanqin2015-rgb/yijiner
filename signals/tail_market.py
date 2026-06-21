"""尾盘确认策略 —— 14:30扫描，14:50买入，次日开盘卖出"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TailPick:
    code: str
    name: str
    score: float
    price: float
    pct: float           # 当日涨幅
    near_high: float     # 距离最高价%
    volume: float        # 成交额(亿)
    buy_price: float     # 建议买入价
    sell_price: float    # 次日止盈价
    stop_price: float    # 次日止损价
    reason: str


def scan(capital: float = 3000.0) -> list[TailPick]:
    """14:30扫描全市场，返回尾盘候选"""
    import akshare as ak

    try:
        df = ak.stock_zh_a_spot_em()
    except Exception:
        return []

    if df.empty:
        return []

    # === 基础清洗 ===
    df["代码"] = df["代码"].astype(str).str.zfill(6)

    # 主板 only
    df = df[~df["代码"].str.startswith(("300", "301", "688", "689", "920", "8", "4"))]
    # 非ST
    df = df[~df["名称"].str.contains("ST|退|N", na=False)]

    for col in ["涨跌幅", "最新价", "成交额", "最高", "今开"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["涨跌幅", "最新价", "最高"])

    # === 四条规则筛选 ===
    df["near_high"] = (df["最高"] - df["最新价"]) / df["最高"] * 100

    mask = (
        (df["涨跌幅"] >= 3) & (df["涨跌幅"] <= 7) &       # 规则1: 涨幅3-7%
        (df["near_high"] >= -2) & (df["near_high"] <= 2) & # 规则2: 收盘在最高价2%内
        (df["成交额"] > 50000000) &                          # 规则3: 成交额>5000万
        (df["最新价"] >= 3) & (df["最新价"] <= 30) &        # 规则4: 价格3-30元
        (df["最新价"] > df["今开"])                           # 今天确实是涨的
    )

    candidates = df[mask].copy()

    if candidates.empty:
        return []

    # === 评分 ===
    candidates["score"] = (
        candidates["涨跌幅"].clip(3, 7) * 10 +          # 涨幅分 (30-70)
        (2 - candidates["near_high"].abs()) * 15 +       # 收盘位置分 (0-30)
        candidates["成交额"].rank(pct=True) * 20          # 流动性分 (0-20)
    )

    candidates = candidates.sort_values("score", ascending=False)

    picks = []
    for _, row in candidates.head(20).iterrows():
        price = row["最新价"]
        lots = max(1, int(capital * 0.5 / (price * 100)))

        buy_price = round(price * 1.001, 2)
        sell_price = round(price * 1.025, 2)   # +2.5%止盈
        stop_price = round(price * 0.985, 2)   # -1.5%止损

        reason_parts = []
        pct_val = row["涨跌幅"]
        nh_val = row["near_high"]
        if pct_val >= 5:
            reason_parts.append("强势领涨")
        if nh_val <= 0.5:
            reason_parts.append("收于最高")
        if row["成交额"] > 2e8:
            reason_parts.append("资金活跃")

        picks.append(TailPick(
            code=row["代码"], name=str(row["名称"]),
            score=round(row["score"], 1),
            price=price, pct=round(pct_val, 1),
            near_high=round(nh_val, 1),
            volume=row["成交额"] / 1e8,
            buy_price=buy_price, sell_price=sell_price,
            stop_price=stop_price,
            reason=" | ".join(reason_parts) if reason_parts else "标准候选",
        ))

    return picks
