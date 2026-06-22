"""双峰波段低价抄底 —— W底形态识别 + 低位反弹信号"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class DipPick:
    code: str
    name: str
    score: float          # 0-100
    price: float          # 当前价
    buy_price: float      # 建议买入价
    target_price: float   # 目标价（60日均线/前高）
    stop_price: float     # 止损价（前低）
    low_1: float          # 第一个底
    low_2: float          # 第二个底
    ma60: float           # 60日均线
    volume_shrink: float  # 缩量比例
    days_between_lows: int  # 双底间隔天数
    reason: str


def find_double_bottom(kline: pd.DataFrame) -> dict | None:
    """在60日K线中寻找W双底形态"""
    if kline.empty or len(kline) < 30:
        return None

    close = kline["收盘"].values
    low = kline["最低"].values
    volume = kline["成交量"].values

    # 找局部低点
    n = len(low)
    local_lows = []
    for i in range(3, n - 3):
        if low[i] <= low[i-1] and low[i] <= low[i-2] and low[i] <= low[i-3] \
           and low[i] <= low[i+1] and low[i] <= low[i+2] and low[i] <= low[i+3]:
            local_lows.append((i, low[i]))

    if len(local_lows) < 2:
        return None

    # 取最近的两个低点
    recent_lows = local_lows[-4:] if len(local_lows) >= 4 else local_lows[-2:]

    for j in range(len(recent_lows) - 1):
        i1, low1 = recent_lows[j]
        i2, low2 = recent_lows[j + 1]
        days_apart = i2 - i1

        # 双底条件（放宽）
        if days_apart < 3 or days_apart > 60:
            continue
        if abs(low1 - low2) / max(low1, low2) > 0.08:  # 两底价格差<8%
            continue

        # 缩量确认
        vol_1 = volume[max(0, i1-2):i1+3].mean()
        vol_2 = volume[max(0, i2-2):i2+3].mean()
        shrink = vol_2 / vol_1 if vol_1 > 0 else 1

        if shrink > 0.90:  # 第二个底必须缩量（放宽到90%）
            continue

        return {"low_1": low1, "low_2": low2, "idx_1": i1, "idx_2": i2,
                "days_between": days_apart, "volume_shrink": shrink}

    return None


def scan(capital: float = 3000.0) -> list[DipPick]:
    """扫描全市场双底形态"""
    import akshare as ak

    try:
        spot = ak.stock_zh_a_spot_em()
    except Exception:
        return []

    if spot.empty:
        return []

    spot["代码"] = spot["代码"].astype(str).str.zfill(6)
    # 主板only
    spot = spot[~spot["代码"].str.startswith(("300", "301", "688", "689", "920", "8", "4"))]
    spot = spot[~spot["名称"].str.contains("ST|退|N", na=False)]

    for col in ["最新价", "涨跌幅"]:
        spot[col] = pd.to_numeric(spot[col], errors="coerce")
    spot = spot[(spot["最新价"] >= 3) & (spot["最新价"] <= 25)]
    spot = spot.dropna(subset=["最新价"])

    picks = []

    for _, row in spot.head(800).iterrows():
        code = row["代码"]
        name = str(row["名称"])
        price = float(row["最新价"])

        try:
            prefix = "sh" if code.startswith("6") else "sz"
            kline = ak.stock_zh_a_daily(symbol=f"{prefix}{code}",
                                        start_date=(datetime.now() - timedelta(days=120)).strftime("%Y%m%d"),
                                        end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
            if kline.empty or len(kline) < 30:
                continue
        except Exception:
            continue

        result = find_double_bottom(kline)
        if result is None:
            continue

        close_arr = kline["收盘"].values
        ma60 = np.mean(close_arr[-60:]) if len(close_arr) >= 60 else np.mean(close_arr)

        # 必须在60日均线下方
        if price > ma60 * 0.95:
            continue

        # 打分
        score = 0
        score += min(40, (1 - result["volume_shrink"]) * 40)  # 缩量越明显分越高
        score += min(20, result["days_between"] / 2)            # 双底间隔够大
        score += min(20, (ma60 - price) / price * 100)          # 距均线越远弹性越大
        score += 20 if price > result["low_2"] * 1.02 else 10   # 确认回升

        buy_price = round(price * 1.002, 2)
        target_price = round(ma60, 2)
        stop_price = round(result["low_2"] * 0.98, 2)

        score = min(100, score)
        if score < 55:
            continue

        picks.append(DipPick(
            code=code, name=name, score=round(score, 1),
            price=price, buy_price=buy_price,
            target_price=target_price, stop_price=stop_price,
            low_1=round(result["low_1"], 2), low_2=round(result["low_2"], 2),
            ma60=round(ma60, 2),
            volume_shrink=round(result["volume_shrink"] * 100, 1),
            days_between_lows=result["days_between"],
            reason=f"双底间隔{result['days_between']}天，缩量{result['volume_shrink']*100:.0f}%",
        ))

    picks.sort(key=lambda p: p.score, reverse=True)
    return picks[:20]
