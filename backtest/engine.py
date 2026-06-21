"""回测主引擎: 严格T+1规则 —— T日扫描→T+1买→T+2卖"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from config.settings import BacktestConfig, FactorWeights
from decision.daily_scan import run as daily_scan
from data.fetcher import fetch_daily_kline, fetch_daily_zt_pool
from data.preprocessor import clean_seal_time, parse_board_height, calc_amount_ratio
from data.preprocessor import is_bullish_alignment, calc_relative_position
from backtest.rules import can_buy, can_sell
from backtest.broker import execute_buy, execute_sell
from backtest.metrics import calc_metrics
from decision.trade_rules import calc_position_size


def run_backtest(config: BacktestConfig = None, weights: FactorWeights = None) -> dict:
    if config is None:
        config = BacktestConfig()
    if weights is None:
        weights = FactorWeights()

    trade_dates = _get_trade_dates(config.start_date, config.end_date)
    if not trade_dates:
        return {"error": "无交易日数据"}

    # K线内存缓存 —— 避免逐日HTTP
    kline_full_cache = {}
    def _get_kline_row(code, date):
        if len(code) != 6:
            return None
        if code not in kline_full_cache:
            kline_full_cache[code] = fetch_daily_kline(code, config.start_date, config.end_date)
        df = kline_full_cache[code]
        if df.empty:
            return None
        matched = df[df["日期"] == date]
        return matched.iloc[0] if not matched.empty else None

    positions = []
    trades = []
    equity_curve = [config.initial_capital]
    cash = config.initial_capital
    pending_candidates = []  # T日扫描结果 → T+1日买入

    for i, date in enumerate(trade_dates):
        # ================================================================
        # 1. 卖出：T+1买入的持仓，持有了>=1天后在T+2日开盘卖出
        # ================================================================
        to_sell = []
        for pos in positions:
            hold_days = _count_trade_days(pos["buy_date"], date, trade_dates)
            if hold_days >= 1:
                row = _get_kline_row(pos["code"], date)
                if row is None:
                    continue
                open_price = float(row["开盘"])
                pre_close_val = float(row["昨收"]) if pd.notna(row.get("昨收")) else open_price

                if can_sell(pos["code"], open_price, pre_close_val,
                            float(row["最高"]), float(row["最低"])):
                    # 止盈/止损逻辑
                    pnl_pct = (open_price - pos["buy_price"]) / pos["buy_price"]
                    sell_price = open_price

                    result = execute_sell(pos["code"], sell_price, pos["quantity"])
                    if result:
                        cash += result["net_proceed"]
                        pnl = result["net_proceed"] - (pos["buy_price"] * pos["quantity"])
                        trades.append({
                            "code": pos["code"], "name": pos["name"],
                            "buy_date": pos["buy_date"], "sell_date": date,
                            "buy_price": pos["buy_price"], "sell_price": sell_price,
                            "pnl": pnl, "pnl_pct": pnl_pct,
                        })
                        to_sell.append(pos)

        for pos in to_sell:
            positions.remove(pos)

        # ================================================================
        # 2. 买入：消费T-1日的候选池（penging_candidates），用T日开盘价
        #   这是修复未来函数的关键——今天买的是昨天扫描出来的票
        # ================================================================
        available_slots = config.max_positions - len(positions)
        if available_slots > 0 and pending_candidates:
            bought = 0
            for cand in pending_candidates:
                if bought >= available_slots:
                    break
                row = _get_kline_row(cand.code, date)
                if row is None:
                    continue
                open_price = float(row["开盘"])
                pre_close_val = float(row["昨收"]) if pd.notna(row.get("昨收")) else open_price

                if not can_buy(cand.code, open_price, pre_close_val,
                               float(row["最高"]), float(row["最低"])):
                    continue

                # 小资金：按手数计算仓位
                quantity, _ = calc_position_size(cash, open_price)
                if quantity < 100:
                    continue

                result_buy = execute_buy(cand.code, open_price, quantity)
                if result_buy and result_buy["total_cost"] <= cash:
                    cash -= result_buy["total_cost"]
                    positions.append({
                        "code": cand.code, "name": cand.name,
                        "buy_price": open_price, "buy_date": date,
                        "quantity": quantity,
                    })
                    bought += 1

        # ================================================================
        # 3. 扫描：获取今日涨停结果 → 留给T+1日买入
        # ================================================================
        result = daily_scan(date, weights=weights, top_n=config.max_positions)
        pending_candidates = result.get("candidates", [])

        # ================================================================
        # 4. 记录净值
        # ================================================================
        position_value = 0
        for pos in positions:
            row = _get_kline_row(pos["code"], date)
            if row is not None:
                position_value += float(row["收盘"]) * pos["quantity"]
            else:
                position_value += pos["buy_price"] * pos["quantity"]

        equity_curve.append(cash + position_value)

    # 强制平仓
    for pos in list(positions):
        last_date = trade_dates[-1]
        row = _get_kline_row(pos["code"], last_date)
        if row is not None:
            close_price = float(row["收盘"])
            result = execute_sell(pos["code"], close_price, pos["quantity"])
            if result:
                cash += result["net_proceed"]
                pnl = result["net_proceed"] - (pos["buy_price"] * pos["quantity"])
                pnl_pct = (close_price - pos["buy_price"]) / pos["buy_price"]
                trades.append({
                    "code": pos["code"], "name": pos["name"],
                    "buy_date": pos["buy_date"], "sell_date": last_date,
                    "buy_price": pos["buy_price"], "sell_price": close_price,
                    "pnl": pnl, "pnl_pct": pnl_pct,
                })
        positions.remove(pos)

    metrics = calc_metrics(trades, equity_curve, config.initial_capital)
    return {"trades": trades, "equity_curve": equity_curve, "metrics": metrics}


def _get_trade_dates(start: str, end: str) -> list[str]:
    """获取交易日列表（使用 akshare 交易日历 + 周末回退）"""
    from data.fetcher import fetch_trade_calendar
    dates = fetch_trade_calendar(start, end)
    if dates:
        return dates
    # 回退：排除周末
    start_dt = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    dates = []
    current = start_dt
    while current <= end_dt:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def _count_trade_days(buy_date: str, current_date: str, all_dates: list[str]) -> int:
    try:
        return all_dates.index(current_date) - all_dates.index(buy_date)
    except ValueError:
        return 1
