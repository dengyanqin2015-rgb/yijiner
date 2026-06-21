"""严格交易规则引擎 — BUY / SELL / 仓位 / 风控

3000本金 · 一进二打板 · 止盈3% / 止损5% / 时间止损2天
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from datetime import datetime, timedelta

from config.constants import get_limit_rate
from data.preprocessor import is_st_stock, is_yizi_board


# ============================================================
# 全局阈值常量
# ============================================================

# --- 买入 ---
BUY_MIN_COMPOSITE = 55.0           # 总分下限
MAX_POSITIONS = 2                  # 最大持仓数
POSITION_CAPITAL = 3000.0          # 总本金
SINGLE_POSITION_MAX_PCT = 0.55     # 单票最大仓位 55%（预留手续费）

# --- 卖出 ---
TAKE_PROFIT_OPEN = 0.03            # 次日开盘涨幅 >3% 止盈
STOP_LOSS_OPEN = -0.05             # 次日开盘跌幅 >5% 止损
TIME_STOP_DAYS = 2                 # 持有2天必须卖（不管盈亏）
TRAILING_PULLBACK = 0.02           # 盘中冲高回落 2% 锁定

# --- 风控 ---
MAX_CONSECUTIVE_LOSSES = 3         # 连续亏损3笔 → 停1天
DAILY_LOSS_LIMIT = -0.05           # 当日亏损 >5% → 清仓休息
WEEKLY_LOSS_LIMIT = -0.10          # 周亏损 >10% → 本周停止交易


# ============================================================
# 信号枚举
# ============================================================

class BuySignal:
    SCORE_PASS = "评分>55"
    FIRST_BOARD = "首板优先"
    SECTOR_LEADER = "板块龙头"
    STRONG_FUND = "资金强势"
    BULLISH_FORM = "多头形态"

class SellSignal:
    TAKE_PROFIT = "开盘止盈"
    STOP_LOSS = "开盘止损"
    TIME_STOP = "时间止损"
    TRAILING_STOP = "移动止盈"
    RISK_CONTROL = "风控清仓"


# ============================================================
# 核心数据类
# ============================================================

@dataclass
class TradeRecord:
    """单笔交易记录"""
    buy_date: str
    sell_date: str = ""
    code: str = ""
    name: str = ""
    buy_price: float = 0.0
    sell_price: float = 0.0
    quantity: int = 0
    position_pct: float = 0.0           # 仓位占比
    pnl: float = 0.0                    # 盈亏金额
    pnl_pct: float = 0.0                # 盈亏比例
    hold_days: int = 0
    buy_signal: str = ""
    sell_signal: str = ""
    score: float = 0.0                  # 买入时评分
    is_closed: bool = False             # 是否已平仓


@dataclass
class Position:
    """当前持仓"""
    code: str
    name: str
    buy_price: float
    buy_date: str
    quantity: int
    position_pct: float
    score: float
    buy_signal: str


@dataclass
class RiskState:
    """风控状态机"""
    consecutive_losses: int = 0
    last_trade_pnl: float = 0.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    paused_until: str = ""              # 空=可交易, 日期=停到该日
    daily_trades_today: list = field(default_factory=list)
    weekly_trades: list = field(default_factory=list)


# ============================================================
# BUY 规则
# ============================================================

def can_buy(
    code: str,
    name: str,
    score: float,
    board_height: int,
    seal_time_cleaned: int,
    open_count: int,
    positions: list[Position],
    risk: RiskState,
) -> tuple[bool, Optional[str]]:
    """
    判断是否可以买入。
    返回: (是否可买, 拒绝原因或买入信号)
    """
    # ---- 风控检查 ----
    if risk.paused_until:
        today = datetime.now().strftime("%Y%m%d")
        if today <= risk.paused_until:
            return False, f"风控暂停至 {risk.paused_until}"

    # ---- 仓位检查 ----
    if len(positions) >= MAX_POSITIONS:
        return False, f"仓位已满 ({len(positions)}/{MAX_POSITIONS})"

    # ---- ST 过滤 ----
    if is_st_stock(code, name):
        return False, "ST品种"

    # ---- 一字板过滤 ----
    if is_yizi_board(seal_time_cleaned, open_count):
        return False, "一字板"

    # ---- 尾盘板过滤 ----
    from data.preprocessor import is_tail_attack
    if is_tail_attack(seal_time_cleaned):
        return False, "尾盘偷袭"

    # ---- 评分过滤 ----
    if score < BUY_MIN_COMPOSITE:
        return False, f"评分不足 ({score:.1f} < {BUY_MIN_COMPOSITE})"

    # ---- 确定买入信号 ----
    signals = []
    signals.append(BuySignal.SCORE_PASS)

    if board_height == 1:
        signals.append(BuySignal.FIRST_BOARD)

    # 单一信号选最重要的
    if board_height == 1:
        buy_signal = BuySignal.FIRST_BOARD
    elif score >= 75:
        buy_signal = BuySignal.STRONG_FUND
    elif score >= 65:
        buy_signal = BuySignal.SECTOR_LEADER
    else:
        buy_signal = BuySignal.BULLISH_FORM

    return True, buy_signal


def calc_position_size(available_capital: float, price: float) -> tuple[int, float]:
    """
    计算买入数量（整手）。
    3000本金 → 最多分2仓，每仓约1500。
    返回: (股数, 实际仓位占比)
    """
    per_slot = min(available_capital * SINGLE_POSITION_MAX_PCT, available_capital * 0.55)
    # 至少留50元付手续费
    per_slot = min(per_slot, available_capital - 50.0)

    quantity = int(per_slot / price / 100) * 100
    if quantity < 100:
        # 钱不够买1手 → 全仓买1手试试
        quantity = 100
        if price * 100 > available_capital * 0.95:
            return 0, 0.0
    elif quantity > int(available_capital * 0.55 / price / 100) * 100:
        quantity = int(available_capital * 0.55 / price / 100) * 100

    if quantity < 100:
        return 0, 0.0

    actual_pct = (price * quantity) / POSITION_CAPITAL
    return quantity, actual_pct


# ============================================================
# SELL 规则
# ============================================================

def should_sell(
    pos: Position,
    open_price: float,
    pre_close: float,
    high: float,
    low: float,
    current_price: float,
    hold_days: int,
) -> tuple[bool, Optional[str], float]:
    """
    判断是否应该卖出，以及以什么价格卖出。
    返回: (是否卖出, 卖出信号, 建议卖出价)

    卖出优先级:
    1. 止损 (开盘跌5%+)  → 以开盘价卖出
    2. 止盈 (开盘涨3%+)  → 以开盘价卖出
    3. 时间止损 (持有2天) → 以当前价卖出
    4. 移动止盈 (冲高回落2%) → 以当前价卖出
    5. 跌停无法卖 → 不卖，明天再说
    """
    buy_price = pos.buy_price
    limit_rate = get_limit_rate(pos.code)
    limit_down = pre_close * (1 - limit_rate)

    # ---- 跌停无法卖出 ----
    if open_price <= limit_down and low >= limit_down:
        return False, "跌停封死", 0.0

    open_pnl = (open_price - buy_price) / buy_price
    day_high_pnl = (high - buy_price) / buy_price
    current_pnl = (current_price - buy_price) / buy_price

    # ---- 优先级 1: 止损 ----
    if open_pnl <= STOP_LOSS_OPEN:
        return True, SellSignal.STOP_LOSS, open_price

    # ---- 优先级 2: 止盈 ----
    if open_pnl >= TAKE_PROFIT_OPEN:
        return True, SellSignal.TAKE_PROFIT, open_price

    # ---- 优先级 3: 时间止损 ----
    if hold_days >= TIME_STOP_DAYS:
        # 持有满2天，收盘前卖出
        return True, SellSignal.TIME_STOP, current_price

    # ---- 优先级 4: 移动止盈 (盘中冲高回落2%) ----
    if day_high_pnl >= 0.05:
        # 盘中最高涨超5%，当前回落超过2% → 锁定利润
        pullback = (high - current_price) / high
        if pullback >= TRAILING_PULLBACK:
            return True, SellSignal.TRAILING_STOP, current_price

    # ---- 持有 ----
    return False, None, 0.0


def should_sell_at_open(pos: Position, open_price: float, pre_close: float) -> tuple[bool, Optional[str], float]:
    """
    开盘集合竞价阶段判断（简化：只看开盘价vs买入价）。
    返回: (是否卖出, 信号, 价格)
    """
    buy_price = pos.buy_price
    limit_rate = get_limit_rate(pos.code)
    limit_down = pre_close * (1 - limit_rate)

    if open_price <= limit_down:
        return False, "跌停无法卖", 0.0

    open_pnl = (open_price - buy_price) / buy_price

    if open_pnl <= STOP_LOSS_OPEN:
        return True, SellSignal.STOP_LOSS, open_price

    if open_pnl >= TAKE_PROFIT_OPEN:
        return True, SellSignal.TAKE_PROFIT, open_price

    return False, None, 0.0


# ============================================================
# 风控规则
# ============================================================

def check_risk_controls(risk: RiskState, closed_trades_today: list[TradeRecord]) -> RiskState:
    """
    收盘后检查风控状态，更新 risk state。
    返回更新后的 RiskState。
    """
    today = datetime.now().strftime("%Y%m%d")

    # 统计当日已平仓盈亏
    daily_pnl = sum(t.pnl for t in closed_trades_today)
    daily_pnl_pct = daily_pnl / POSITION_CAPITAL if POSITION_CAPITAL > 0 else 0
    risk.daily_pnl = daily_pnl_pct

    # 统计当周盈亏（简化：逐笔累加本周所有已平仓交易）
    weekly_pnl = sum(
        t.pnl for t in risk.weekly_trades
        if t.sell_date and t.sell_date >= _week_start(today)
    )
    # 加上今天的
    weekly_pnl += daily_pnl
    weekly_pnl_pct = weekly_pnl / POSITION_CAPITAL if POSITION_CAPITAL > 0 else 0
    risk.weekly_pnl = weekly_pnl_pct

    # 检查连续亏损
    for t in closed_trades_today:
        if t.pnl_pct < 0:
            risk.consecutive_losses += 1
        else:
            risk.consecutive_losses = 0
        risk.last_trade_pnl = t.pnl_pct

    # ---- 风控触发 ----

    # 当日亏损 > 5% → 清仓，明天停一天
    if daily_pnl_pct <= DAILY_LOSS_LIMIT:
        tomorrow = (datetime.strptime(today, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        risk.paused_until = tomorrow
        risk.consecutive_losses = 0  # 重置，已强制休息
        return risk

    # 周亏损 > 10% → 本周剩余时间停止交易
    if weekly_pnl_pct <= WEEKLY_LOSS_LIMIT:
        # 找到本周五
        dt = datetime.strptime(today, "%Y%m%d")
        days_until_friday = (4 - dt.weekday()) % 7
        risk.paused_until = (dt + timedelta(days=days_until_friday)).strftime("%Y%m%d")
        return risk

    # 连续亏损 3 笔 → 停 1 天
    if risk.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
        tomorrow = (datetime.strptime(today, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        risk.paused_until = tomorrow
        risk.consecutive_losses = 0
        return risk

    risk.paused_until = ""
    return risk


def _week_start(date: str) -> str:
    """返回本周一的日期字符串"""
    dt = datetime.strptime(date, "%Y%m%d")
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y%m%d")


# ============================================================
# 交易日志
# ============================================================

def log_buy(record: TradeRecord, log_path: str = "output/trade_log.csv"):
    """追加买入记录到交易日志"""
    import csv
    import os
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    file_exists = os.path.exists(log_path)
    with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["买入日期", "卖出日期", "代码", "名称", "买入价", "卖出价",
                         "数量", "仓位%", "盈亏", "盈亏%", "持仓天数",
                         "买入信号", "卖出信号", "买入评分", "状态"])
        w.writerow([
            record.buy_date, record.sell_date, record.code, record.name,
            f"{record.buy_price:.2f}", f"{record.sell_price:.2f}" if record.sell_price else "",
            record.quantity, f"{record.position_pct * 100:.1f}",
            f"{record.pnl:.2f}" if record.is_closed else "",
            f"{record.pnl_pct * 100:+.2f}%" if record.is_closed else "",
            record.hold_days if record.is_closed else "",
            record.buy_signal, record.sell_signal,
            f"{record.score:.1f}",
            "已平仓" if record.is_closed else "持仓中",
        ])


def log_sell_update(record: TradeRecord, log_path: str = "output/trade_log.csv"):
    """更新已平仓的交易记录（在 CSV 末尾追加新行，标记已平仓）"""
    log_buy(record, log_path)


def save_nav_snapshot(
    cash: float,
    positions: list[Position],
    prices: dict,
    date: str,
    log_path: str = "output/nav_history.csv",
):
    """保存每日净值快照"""
    import csv
    import os
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    position_value = 0.0
    for pos in positions:
        price = prices.get(pos.code, pos.buy_price)
        position_value += price * pos.quantity

    total_nav = cash + position_value
    total_pnl = total_nav - POSITION_CAPITAL
    total_pnl_pct = total_pnl / POSITION_CAPITAL if POSITION_CAPITAL > 0 else 0

    file_exists = os.path.exists(log_path)
    with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["日期", "现金", "持仓市值", "总净值", "总盈亏", "总盈亏%", "持仓数"])
        w.writerow([
            date, f"{cash:.2f}", f"{position_value:.2f}", f"{total_nav:.2f}",
            f"{total_pnl:+.2f}", f"{total_pnl_pct * 100:+.2f}%", len(positions),
        ])

    return total_nav, total_pnl, total_pnl_pct
