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
    """基于当日涨停池做双底检测（避免spot_em超时）"""
    import akshare as ak
    import time as _time

    try:
        zt = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
    except Exception:
        return []

    if zt.empty:
        return []

    zt["代码"] = zt["代码"].astype(str).str.zfill(6)
    zt = zt[~zt["代码"].str.startswith(("300","301","688","689","920","8","4"))]
    zt = zt[~zt["名称"].str.contains("ST|退|N", na=False)]
    for col in ["最新价"]:
        zt[col] = pd.to_numeric(zt[col], errors="coerce")
    zt = zt[(zt["最新价"]>=3) & (zt["最新价"]<=25)]
    zt = zt.dropna(subset=["最新价"])

    picks = []
    for _, row in zt.iterrows():
        code = row["代码"]; name = str(row["名称"]); price = float(row["最新价"])

        try:
            prefix = "sh" if code.startswith("6") else "sz"
            raw = ak.stock_zh_a_daily(symbol=prefix+code,
                start_date=(datetime.now()-timedelta(days=90)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
            if raw.empty or len(raw) < 30: continue
            kline = raw.rename(columns={"date":"日期","open":"开盘","close":"收盘","high":"最高","low":"最低","volume":"成交量"})
        except Exception: continue
        _time.sleep(0.8)

        result = find_double_bottom(kline)
        close_arr = kline["收盘"].values.astype(float)
        ma60 = np.mean(close_arr[-60:]) if len(close_arr)>=60 else np.mean(close_arr)
        score = 0; buy_price = round(price*1.002,2); target = round(ma60,2)

        if result:
            score += 40 + min(25,(1-result["volume_shrink"])*25) + min(15,result["days_between"]/3)
            sl = round(result["low_2"]*0.98,2)
            reason = f"W底{result['days_between']}天,缩量{result['volume_shrink']*100:.0f}%"
            low1,low2 = round(result["low_1"],2),round(result["low_2"],2)
            vs = round(result["volume_shrink"]*100,1); db_days = result["days_between"]
        else:
            if price > ma60*1.05: continue
            hi = max(close_arr[-60:]); lo = min(close_arr[-60:])
            pos = (price-lo)/(hi-lo) if hi!=lo else 0.5
            if pos > 0.35: continue
            recent = close_arr[-3:]
            up = sum(1 for i in range(1,len(recent)) if recent[i]>recent[i-1])
            if up < 1: continue
            score += 20 + min(25,(1-pos)*25) + up*10
            sl = round(price*0.95,2)
            reason = f"低位筑底(pos{pos*100:.0f}%),近3日{up}阳"
            low1=round(lo,2); low2=price; vs=0; db_days=0

        score += min(15,(ma60-price)/price*60)
        if score < 40: continue

        picks.append(DipPick(code=code,name=name,score=round(min(100,score),1),
            price=price,buy_price=buy_price,target_price=target,stop_price=sl,
            low_1=low1,low_2=low2,ma60=round(ma60,2),
            volume_shrink=vs,days_between_lows=db_days,reason=reason))

    picks.sort(key=lambda p:p.score,reverse=True)
    return picks[:20]
