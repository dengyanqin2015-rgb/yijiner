"""每日扫描总调度: 数据获取 → 预处理 → 打分 → 过滤 → 输出"""

import pandas as pd
from datetime import datetime, timedelta

from data.fetcher import (
    fetch_daily_zt_pool, fetch_breakout_pool,
    fetch_previous_zt_performance, fetch_concept_boards,
    fetch_concept_fund_flow, fetch_daily_kline,
)
from data.preprocessor import (
    clean_seal_time, parse_board_height, calc_amount_ratio,
    calc_ma_deviation, is_bullish_alignment, calc_relative_position,
)
from signals.scorer import compute_composite, rank_candidates, ScoredStock, FactorWeights
from decision.buy_candidates import filter_and_rank
from decision.sell_advice import generate as sell_generate


def run(date: str, weights: FactorWeights = None, top_n: int = 20) -> dict:
    """执行每日扫描，返回完整的分析结果"""
    if weights is None:
        weights = FactorWeights()

    prev_date = _prev_trade_date(date)

    # Phase 1: 数据获取
    zt_pool = fetch_daily_zt_pool(date)
    breakout = fetch_breakout_pool(date)
    prev_perf = fetch_previous_zt_performance(date)

    if zt_pool.empty:
        return {"error": f"日期 {date} 无涨停板数据", "date": date, "candidates": [], "market": {}}

    # Phase 2: 预处理
    zt_pool = _preprocess_zt(zt_pool)

    # Phase 3: 市场情绪
    total_zt = len(zt_pool)
    total_break = len(breakout) if not breakout.empty else 0
    break_rate = total_break / (total_zt + total_break) if (total_zt + total_break) > 0 else 0
    max_board = int(zt_pool["board_height"].max()) if not zt_pool.empty else 1
    prev_premium = _calc_prev_premium(prev_perf)
    market = {
        "total_zt": total_zt,
        "break_rate": break_rate,
        "max_board": max_board,
        "prev_premium": prev_premium,
    }

    # Phase 4: 对每只涨停股打分
    scored_list = []
    kline_cache = {}

    for _, row in zt_pool.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        if not code or len(code) != 6:
            continue

        # 获取K线（缓存避免重复请求）
        if code not in kline_cache:
            start = (datetime.strptime(date, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
            kline = fetch_daily_kline(code, start, date)
            kline_cache[code] = kline
        else:
            kline = kline_cache[code]

        kline_info = {
            "bullish": is_bullish_alignment(kline),
            "rel_pos": calc_relative_position(kline),
        }

        sector_info = {
            "zt_count": 0,
            "is_leader": False,
            "fund_sign": 0,
            "rank_pct": 0.5,
        }

        scored = compute_composite(row, market, kline_info, sector_info, weights)
        scored_list.append(scored)

    # Phase 5: 过滤排序
    candidates = filter_and_rank(scored_list, zt_pool, top_n=top_n)

    return {
        "date": date,
        "market": market,
        "candidates": candidates,
        "total_scored": len(scored_list),
    }


def _preprocess_zt(zt_pool: pd.DataFrame) -> pd.DataFrame:
    df = zt_pool.copy()
    if "首次封板时间" in df.columns:
        df["seal_time_cleaned"] = df["首次封板时间"].apply(clean_seal_time)
    elif "sec_first_seal_time" in df.columns:
        df["seal_time_cleaned"] = df["sec_first_seal_time"].apply(clean_seal_time)
    else:
        df["seal_time_cleaned"] = 999999

    if "涨停统计" in df.columns:
        df["board_height"] = df["涨停统计"].apply(parse_board_height)
    elif "zt_statistics" in df.columns:
        df["board_height"] = df["zt_statistics"].apply(parse_board_height)
    else:
        df["board_height"] = 1

    cap_col = None
    for c in ["流通市值", "float_market_cap", "market_cap"]:
        if c in df.columns:
            cap_col = c
            break

    amount_col = None
    for c in ["封单金额", "seal_amount"]:
        if c in df.columns:
            amount_col = c
            break

    if cap_col and amount_col:
        df["seal_amount_ratio"] = df.apply(
            lambda r: calc_amount_ratio(r[amount_col], r[cap_col]), axis=1)
    else:
        df["seal_amount_ratio"] = 0.0

    return df


def _calc_prev_premium(prev_perf: pd.DataFrame) -> float:
    if prev_perf.empty:
        return 0.0
    col = None
    for c in ["今日涨幅", "curr_gain", "涨幅", "涨跌幅"]:
        if c in prev_perf.columns:
            col = c
            break
    if col is None:
        return 0.0
    try:
        return float(prev_perf[col].mean())
    except (ValueError, TypeError):
        return 0.0


def _prev_trade_date(date: str) -> str:
    dt = datetime.strptime(date, "%Y%m%d")
    dt -= timedelta(days=1)
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y%m%d")
