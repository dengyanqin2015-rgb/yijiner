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
    """扫描全市场双底形态 - 先粗筛再精查，避免限流"""
    import akshare as ak
    import time as _time

    try:
        spot = ak.stock_zh_a_spot_em()
    except Exception:
        return []

    if spot.empty:
        return []

    spot["代码"] = spot["代码"].astype(str).str.zfill(6)
    spot = spot[~spot["代码"].str.startswith(("300", "301", "688", "689", "920", "8", "4"))]
    spot = spot[~spot["名称"].str.contains("ST|退|N", na=False)]

    for col in ["最新价", "涨跌幅", "换手率"]:
        spot[col] = pd.to_numeric(spot[col], errors="coerce")
    spot = spot[(spot["最新价"] >= 3) & (spot["最新价"] <= 25)]
    spot = spot.dropna(subset=["最新价"])

    # 粗筛：优先低涨幅+低换手（可能是筑底形态）
    spot["rank"] = spot["涨跌幅"].rank() * 0.5 + spot["换手率"].rank() * 0.5
    candidates = spot.sort_values("rank").head(50)  # 50只=约60秒  # 只查150只

    picks = []
    for _, row in candidates.iterrows():
        code = row["代码"]
        name = str(row["名称"])
        price = float(row["最新价"])

        try:
            prefix = "sh" if code.startswith("6") else "sz"
            raw = ak.stock_zh_a_daily(symbol=f"{prefix}{code}",
                                       start_date=(datetime.now() - timedelta(days=90)).strftime("%Y%m%d"),
                                       end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
            if raw.empty or len(raw) < 30:
                continue
            kline = raw.rename(columns={"date":"日期","open":"开盘","close":"收盘","high":"最高","low":"最低","volume":"成交量"})
        except Exception:
            continue

        _time.sleep(1.0)  # Sina限流严，必须间隔1秒

        result = find_double_bottom(kline)
        close_arr = kline["收盘"].values.astype(float)
        ma60 = np.mean(close_arr[-60:]) if len(close_arr) >= 60 else np.mean(close_arr)

        # 放宽：即使没检测到完美双底，只要低位缩量就算
        score = 0
        buy_price = round(price * 1.002, 2)
        target_price = round(ma60, 2)
        stop_price = round(price * 0.95, 2)

        if result:
            score += 35
            score += min(20, (1 - result["volume_shrink"]) * 20)
            score += min(15, result["days_between"] / 3)
            stop_price = round(result["low_2"] * 0.98, 2)
            reason = f"双底间隔{result['days_between']}天，缩量{result['volume_shrink']*100:.0f}%"
            low1, low2 = round(result["low_1"], 2), round(result["low_2"], 2)
            vs = round(result["volume_shrink"] * 100, 1)
            db_days = result["days_between"]
        else:
            # 没有完美双底，但股价在低位且有反弹迹象
            if price > ma60 * 1.05:
                continue
            pos = (price - min(close_arr[-60:])) / (max(close_arr[-60:]) - min(close_arr[-60:])) if max(close_arr[-60:]) != min(close_arr[-60:]) else 0.5
            if pos > 0.35:  # 不在低位
                continue

            # 最近3天有没有阳线
            recent_3 = close_arr[-3:]
            up_days = sum(1 for i in range(1, len(recent_3)) if recent_3[i] > recent_3[i-1])
            if up_days < 1:
                continue

            score += 15
            score += min(20, (1 - pos) * 20)
            score += up_days * 10
            reason = f"低位筑底(位置{pos*100:.0f}%)，近3日{up_days}阳"
            low1 = round(min(close_arr[-60:]), 2)
            low2 = price
            vs = 0
            db_days = 0

        score += min(15, (ma60 - price) / price * 60)
        if score < 35:
            continue

        picks.append(DipPick(
            code=code, name=name, score=round(min(100, score), 1),
            price=price, buy_price=buy_price,
            target_price=target_price, stop_price=stop_price,
            low_1=low1, low_2=low2,
            ma60=round(ma60, 2),
            volume_shrink=vs,
            days_between_lows=db_days,
            reason=reason,
        ))

    picks.sort(key=lambda p: p.score, reverse=True)
    return picks[:30]
