"""特征工程: 清洗、衍生字段计算、缺省值填充"""

import pandas as pd
import numpy as np
from datetime import datetime


def clean_seal_time(time_str) -> int:
    """封板时间清洗: '09:30:00' → 93000, '10:15:30' → 101530"""
    if pd.isna(time_str) or time_str in ("", "-", "0"):
        return 999999
    s = str(time_str).strip()
    if ":" in s:
        parts = s.split(":")
        return int(parts[0]) * 10000 + int(parts[1]) * 100 + int(parts[2]) if len(parts) >= 3 else 0
    if len(s) >= 6:
        return int(s[:6])
    try:
        return int(s)
    except ValueError:
        return 999999


def seal_time_score(seal_time: int) -> float:
    """封板时间 → 0~100 分, 越早越高"""
    if seal_time <= 93030:   return 100.0
    if seal_time <= 94500:   return 95.0
    if seal_time <= 100000:  return 90.0
    if seal_time <= 101500:  return 85.0
    if seal_time <= 103000:  return 75.0
    if seal_time <= 110000:  return 65.0
    if seal_time <= 113000:  return 55.0
    if seal_time <= 130500:  return 50.0
    if seal_time <= 133000:  return 40.0
    if seal_time <= 140000:  return 30.0
    if seal_time <= 143000:  return 20.0
    if seal_time <= 145000:  return 10.0
    return 5.0


def is_yizi_board(first_seal_time: int, open_count: int) -> bool:
    """一字板判断: 09:25前封板 且 从未打开"""
    return first_seal_time <= 92559 and open_count == 0


def is_tail_attack(first_seal_time: int) -> bool:
    """尾盘偷袭板: 14:30之后才封板"""
    return first_seal_time >= 143000


def is_st_stock(code: str, name: str) -> bool:
    """排除 ST / *ST"""
    n = str(name).upper()
    return "ST" in n or "*ST" in n


def parse_board_height(stats_str) -> int:
    """解析连板数: '3/4'→4天3板(当前4板), '首板'→1"""
    if pd.isna(stats_str):
        return 1
    s = str(stats_str).strip()
    if "首板" in s or s == "" or s == "-":
        return 1
    if "/" in s:
        try:
            return int(s.split("/")[1])
        except (ValueError, IndexError):
            return 1
    try:
        return int(s)
    except ValueError:
        return 1


def build_concept_zt_map(zt_codes: set, concept_boards: pd.DataFrame) -> dict:
    """构建 概念代码→涨停家数 映射表 (需要概念成分股数据)"""
    return {}  # 实际运行时由 scorer 按需构建


def calc_ma_deviation(kline: pd.DataFrame) -> dict:
    """计算均线偏离度"""
    if kline.empty or len(kline) < 60:
        return {"ma5_dev": 0, "ma10_dev": 0, "ma20_dev": 0, "ma60_dev": 0}
    close = kline["收盘"].values
    ma5 = np.mean(close[-5:])
    ma10 = np.mean(close[-10:])
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:])
    latest = close[-1]
    return {
        "ma5_dev": (latest - ma5) / ma5 if ma5 else 0,
        "ma10_dev": (latest - ma10) / ma10 if ma10 else 0,
        "ma20_dev": (latest - ma20) / ma20 if ma20 else 0,
        "ma60_dev": (latest - ma60) / ma60 if ma60 else 0,
    }


def is_bullish_alignment(kline: pd.DataFrame) -> bool:
    """均线多头排列: 5>10>20>60"""
    if kline.empty or len(kline) < 60:
        return False
    close = kline["收盘"].values
    ma5 = np.mean(close[-5:])
    ma10 = np.mean(close[-10:])
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:])
    return bool(ma5 > ma10 > ma20 > ma60)


def calc_relative_position(kline: pd.DataFrame) -> float:
    """60日相对位置: 0~1, 0=最低, 1=最高"""
    if kline.empty or len(kline) < 60:
        return 0.5
    close = kline["收盘"].values[-60:]
    low, high = close.min(), close.max()
    if high == low:
        return 0.5
    return (close[-1] - low) / (high - low)


def calc_amount_ratio(seal_amount, float_market_cap) -> float:
    """封单金额 / 流通市值"""
    if pd.isna(seal_amount) or pd.isna(float_market_cap) or float_market_cap == 0:
        return 0.0
    try:
        return float(seal_amount) / float(float_market_cap)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
