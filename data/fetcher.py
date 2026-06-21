"""封装所有 akshare 调用，统一带缓存"""

import akshare as ak
import pandas as pd
from data.cache import DataCache

_cache = DataCache()


def fetch_daily_zt_pool(date: str) -> pd.DataFrame:
    """涨停板池 (全量)"""
    df = _cache.get("zt_pool", date)
    if df is not None:
        return df
    try:
        df = ak.stock_zt_pool_em(date=date)
        if df is not None and not df.empty:
            _cache.set(df, "zt_pool", date)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def fetch_strong_zt_pool(date: str) -> pd.DataFrame:
    """强势池 (连续涨停)"""
    df = _cache.get("strong_zt", date)
    if df is not None:
        return df
    try:
        df = ak.stock_zt_pool_strong_em(date=date)
        if df is not None and not df.empty:
            _cache.set(df, "strong_zt", date)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def fetch_breakout_pool(date: str) -> pd.DataFrame:
    """炸板池"""
    df = _cache.get("breakout", date)
    if df is not None:
        return df
    try:
        df = ak.stock_zt_pool_zbgc_em(date=date)
        if df is not None and not df.empty:
            _cache.set(df, "breakout", date)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def fetch_previous_zt_performance(date: str) -> pd.DataFrame:
    """昨日涨停今日表现"""
    df = _cache.get("prev_zt_perf", date)
    if df is not None:
        return df
    try:
        df = ak.stock_zt_pool_previous_em(date=date)
        if df is not None and not df.empty:
            _cache.set(df, "prev_zt_perf", date)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def fetch_concept_boards() -> pd.DataFrame:
    """所有概念板块列表"""
    df = _cache.get("concept_boards")
    if df is not None:
        return df
    try:
        df = ak.stock_board_concept_name_em()
        if df is not None and not df.empty:
            _cache.set(df, "concept_boards")
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def fetch_concept_constituents(symbol: str) -> pd.DataFrame:
    """概念板块成分股, symbol 如 'BK1184'"""
    df = _cache.get("concept_cons", symbol)
    if df is not None:
        return df
    try:
        df = ak.stock_board_concept_cons_em(symbol=symbol)
        if df is not None and not df.empty:
            _cache.set(df, "concept_cons", symbol)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def fetch_concept_fund_flow() -> pd.DataFrame:
    """概念板块资金流向 (即时)"""
    df = _cache.get("concept_fund_flow")
    if df is not None:
        return df
    try:
        df = ak.stock_fund_flow_concept(symbol="即时")
        if df is not None and not df.empty:
            _cache.set(df, "concept_fund_flow")
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def fetch_individual_fund_flow(code: str) -> pd.DataFrame:
    """个股资金流向, code 如 '600001'"""
    df = _cache.get("ind_fund_flow", code)
    if df is not None:
        return df
    try:
        market = "sh" if code.startswith(("6", "68")) else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is not None and not df.empty:
            _cache.set(df, "ind_fund_flow", code)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        print(f"  [WARN] 涨停板池获取失败 {date}: {e}")
        return pd.DataFrame()


def _code_to_sina(code: str) -> str:
    """600519 → sh600519, 000001 → sz000001"""
    if code.startswith(("6", "68")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def fetch_daily_kline(code: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
    """个股日K线 (前复权) —— 使用新浪数据源（绕过代理限制）"""
    df = _cache.get("kline_sina", code, start, end, adjust)
    if df is not None:
        return df
    try:
        symbol = _code_to_sina(code)
        raw = ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust=adjust)
        if raw is None or raw.empty:
            return pd.DataFrame()
        # 新浪列名英→中映射，与 preprocessor 和 backtest 保持一致
        df = raw.rename(columns={
            "date": "日期", "open": "开盘", "high": "最高",
            "low": "最低", "close": "收盘", "volume": "成交量",
            "amount": "成交额",
        })
        df["昨收"] = df["收盘"].shift(1)
        _cache.set(df, "kline_sina", code, start, end, adjust)
        return df
    except Exception as e:
        print(f"  [WARN] K线获取失败 {code}: {e}")
        return pd.DataFrame()


def fetch_trade_calendar(start: str, end: str) -> list[str]:
    """交易日列表"""
    key = ("trade_cal", start, end)
    df = _cache.get(*key)
    if df is not None:
        return df["trade_date"].tolist()
    try:
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty:
            dates = sorted(df["trade_date"].astype(str).tolist())
            dates = [d for d in dates if start <= d <= end]
            result = pd.DataFrame({"trade_date": dates})
            _cache.set(result, *key)
            return dates
    except Exception:
        pass
    return []
