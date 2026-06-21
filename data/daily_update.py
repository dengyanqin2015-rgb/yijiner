"""每日数据自动更新管道

功能:
  1. 15:30 自动拉取当日涨停板数据（含降级备选源）
  2. 自动更新所有持仓股的K线
  3. 自动计算市场情绪指标
  4. 数据完整性校验
  5. 交易信号生成 & 交易日志记录
  6. 结果推送到 output/YYYY-MM-DD/ 目录

用法:
  py data/daily_update.py                          # 更新当日
  py data/daily_update.py --date 20260609          # 更新指定日
  py data/daily_update.py --date 20260609 --live   # 模拟实盘交易决策
"""

import json
import os
import sys
import time
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

# 项目根路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import FactorWeights, FILTER_MIN_MARKET_CAP, FILTER_MAX_MARKET_CAP, TOP_N_CANDIDATES
from data.fetcher import (
    fetch_daily_zt_pool, fetch_breakout_pool, fetch_previous_zt_performance,
    fetch_daily_kline, fetch_concept_boards, fetch_concept_fund_flow,
    fetch_strong_zt_pool,
)
from data.preprocessor import (
    clean_seal_time, parse_board_height, calc_amount_ratio,
    is_bullish_alignment, calc_relative_position, is_st_stock,
    is_yizi_board, is_tail_attack,
)
from data.cache import DataCache
from signals.scorer import compute_composite, ScoredStock
from decision.buy_candidates import filter_and_rank
from decision.trade_rules import (
    Position, TradeRecord, RiskState, BuySignal, SellSignal,
    can_buy, calc_position_size, should_sell, should_sell_at_open,
    check_risk_controls, log_buy, log_sell_update, save_nav_snapshot,
    BUY_MIN_COMPOSITE, MAX_POSITIONS, POSITION_CAPITAL,
    TAKE_PROFIT_OPEN, STOP_LOSS_OPEN, TIME_STOP_DAYS, TRAILING_PULLBACK,
)


# ============================================================
# 配置
# ============================================================

OUTPUT_DIR = PROJECT_ROOT / "output"
POSITIONS_FILE = OUTPUT_DIR / "positions.json"
RISK_STATE_FILE = OUTPUT_DIR / "risk_state.json"
TRADE_LOG_FILE = OUTPUT_DIR / "trade_log.csv"
NAV_LOG_FILE = OUTPUT_DIR / "nav_history.csv"
DEFAULT_START_DATE = "20240101"

# 降级备选数据源（东方财富直接API）
FALLBACK_ZT_URL = (
    "https://push2ex.eastmoney.com/getTopicZTPool"
    "?ut=7eea3edcaed734bea9c2b1dee5ca1c6f"
    "&PageSize=500&pageNo=1"
)
FALLBACK_ZT_PARAMS = "&CBName=cb&fields=code,name,market,lbt,pct,amount,float_market_cap,turnover,volume_ratio,high,open,low,fbt,sbt,industry,zt_statistics,fd_amount,zbc"


class UpdatePipeline:
    """每日更新管道 —— 封装全部步骤，支持模拟实盘模式"""

    def __init__(self, date: str, live_mode: bool = False, weights: FactorWeights = None):
        self.date = date
        self.live_mode = live_mode          # True = 模拟实盘，应用仓位/风控
        self.weights = weights or FactorWeights()
        self.cache = DataCache()

        # 运行时状态
        self.zt_pool = pd.DataFrame()
        self.breakout = pd.DataFrame()
        self.prev_perf = pd.DataFrame()
        self.market: dict = {}
        self.scored: list[ScoredStock] = []
        self.candidates: list[ScoredStock] = []
        self.positions: list[Position] = []
        self.risk: RiskState = RiskState()
        self.trades_today: list[TradeRecord] = []
        self.cash = POSITION_CAPITAL
        self.log: list[str] = []     # 运行日志

    # ----------------------------------------------------------
    # Step 1: 数据获取 + 完整性校验
    # ----------------------------------------------------------

    def step_fetch_data(self) -> bool:
        """获取涨停板数据，如果主源失败则降级到备选源"""
        self._info("--- Step 1: 数据获取 ---")

        # 主数据源: akshare
        self.zt_pool = fetch_daily_zt_pool(self.date)
        self.breakout = fetch_breakout_pool(self.date)
        self.prev_perf = fetch_previous_zt_performance(self.date)

        # 完整性校验
        if self.zt_pool.empty:
            self._warn(f"主数据源 (akshare) 无涨停数据，尝试备选源...")
            self.zt_pool = self._fetch_fallback_zt()
            if self.zt_pool.empty:
                self._error("备选源也无数据，中断更新")
                return False
            self._info(f"备选源成功: {len(self.zt_pool)} 条涨停记录")

        zt_count = len(self.zt_pool)
        self._info(f"涨停板: {zt_count} 只 | 炸板: {len(self.breakout)} 只")

        # 市场数据校验
        if zt_count < 10:
            self._warn(f"涨停数仅 {zt_count}，可能数据不完整（非交易日或接口异常）")
            # 检查是否交易日
            if not self._is_trade_day():
                self._info(f"{self.date} 非交易日，跳过")
                return False

        return True

    def _fetch_fallback_zt(self) -> pd.DataFrame:
        """备选源: 东方财富涨停板API直连"""
        try:
            import requests
            url = FALLBACK_ZT_URL + FALLBACK_ZT_PARAMS
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            })
            if resp.status_code != 200:
                return pd.DataFrame()
            text = resp.text
            if text.startswith("cb("):
                text = text[3:-1]  # 去掉 JSONP 包装
            data = json.loads(text)
            rows = data.get("data", {}).get("pool", [])
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            # 列名映射到中文（与主源保持一致）
            col_map = {
                "c": "代码", "n": "名称", "m": "市场", "lbt": "连板天数",
                "p": "涨幅", "amount": "成交额", "float_market_cap": "流通市值",
                "turnover": "换手率", "volume_ratio": "量比",
                "high": "最高", "open": "开盘", "low": "最低",
                "fbt": "首次封板时间", "sbt": "最后封板时间",
                "industry": "所属行业", "zt_statistics": "涨停统计",
                "fd_amount": "封单金额", "zbc": "炸板次数",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            self._info(f"降级源获取 {len(df)} 条涨停记录")
            return df
        except Exception as e:
            self._warn(f"备选源获取失败: {e}")
            return pd.DataFrame()

    def _is_trade_day(self) -> bool:
        """判断是否为交易日（简单规则：工作日且非大假期）"""
        dt = datetime.strptime(self.date, "%Y%m%d")
        # 周末
        if dt.weekday() >= 5:
            return False
        # 简单排除元旦和五一/十一（可扩展）
        exclude_dates = {"0101", "0102", "0103", "0501", "0502", "0503", "1001", "1002", "1003", "1004", "1005"}
        mmdd = dt.strftime("%m%d")
        if mmdd in exclude_dates:
            return False
        return True

    # ----------------------------------------------------------
    # Step 2: 预处理 & 市场情绪
    # ----------------------------------------------------------

    def step_preprocess(self):
        """预处理涨停板数据，计算市场情绪"""
        self._info("--- Step 2: 预处理 & 市场情绪 ---")

        df = self.zt_pool.copy()

        # 封板时间
        if "首次封板时间" in df.columns:
            df["seal_time_cleaned"] = df["首次封板时间"].apply(clean_seal_time)
        elif "fbt" in df.columns:
            df["seal_time_cleaned"] = df["fbt"].apply(clean_seal_time)
        else:
            df["seal_time_cleaned"] = 999999

        # 连板高度
        if "涨停统计" in df.columns:
            df["board_height"] = df["涨停统计"].apply(parse_board_height)
        elif "zt_statistics" in df.columns:
            df["board_height"] = df["zt_statistics"].apply(parse_board_height)
        elif "lbt" in df.columns:
            df["board_height"] = df["lbt"].apply(lambda x: int(x) if pd.notna(x) else 1)
        else:
            df["board_height"] = 1

        # 封单比
        cap_col = self._find_col(df, ["流通市值", "float_market_cap"])
        amount_col = self._find_col(df, ["封单金额", "fd_amount", "seal_amount"])
        if cap_col and amount_col:
            df["seal_amount_ratio"] = df.apply(
                lambda r: calc_amount_ratio(r[amount_col], r[cap_col]), axis=1)
        else:
            df["seal_amount_ratio"] = 0.0

        self.zt_pool = df

        # 市场情绪指标
        total_zt = len(df)
        total_break = len(self.breakout) if not self.breakout.empty else 0
        break_rate = total_break / (total_zt + total_break) if (total_zt + total_break) > 0 else 0
        max_board = int(df["board_height"].max()) if not df.empty else 1
        prev_premium = self._calc_prev_premium()

        self.market = {
            "total_zt": total_zt,
            "break_rate": round(break_rate, 4),
            "max_board": max_board,
            "prev_premium": round(prev_premium, 2),
            "sentiment_level": self._sentiment_level(total_zt, break_rate, max_board),
        }

        self._info(f"涨停 {total_zt} | 炸板率 {break_rate * 100:.1f}% | "
                   f"最高 {max_board}板 | 昨日溢价 {prev_premium:.2f}% "
                   f"| 情绪: {self.market['sentiment_level']}")

    def _sentiment_level(self, total_zt: int, break_rate: float, max_board: int) -> str:
        """综合判断市场情绪等级"""
        if total_zt >= 80 and break_rate < 0.20 and max_board >= 5:
            return "极度亢奋"
        if total_zt >= 50 and break_rate < 0.30:
            return "偏暖"
        if total_zt >= 30:
            return "中性"
        if total_zt < 20:
            return "冰点"
        return "偏冷"

    def _calc_prev_premium(self) -> float:
        """计算昨日涨停今日溢价"""
        if self.prev_perf.empty:
            return 0.0
        for c in ["今日涨幅", "涨幅", "涨跌幅"]:
            if c in self.prev_perf.columns:
                try:
                    return float(self.prev_perf[c].mean())
                except (ValueError, TypeError):
                    pass
        return 0.0

    # ----------------------------------------------------------
    # Step 3: 五因子打分
    # ----------------------------------------------------------

    def step_score(self):
        """对每只涨停股进行五因子打分"""
        self._info("--- Step 3: 五因子打分 ---")

        kline_cache = {}
        scored_list = []
        total = len(self.zt_pool)

        for idx, (_, row) in enumerate(self.zt_pool.iterrows()):
            code = str(row.get("代码", "")).zfill(6)
            if not code or len(code) != 6:
                continue

            # K线缓存
            if code not in kline_cache:
                start = (datetime.strptime(self.date, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
                kline = fetch_daily_kline(code, start, self.date)
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

            scored = compute_composite(row, self.market, kline_info, sector_info, self.weights)
            scored_list.append(scored)

            if (idx + 1) % 50 == 0:
                self._info(f"  已打分 {idx + 1}/{total}")

        self.scored = scored_list
        self._info(f"完成打分: {len(scored_list)} 只")

    # ----------------------------------------------------------
    # Step 4: 过滤排序 → 买入候选
    # ----------------------------------------------------------

    def step_filter(self):
        """过滤ST/一字板/尾盘/市值不符，排序取Top股票"""
        self._info("--- Step 4: 过滤排序 ---")

        self.candidates = filter_and_rank(
            self.scored, self.zt_pool,
            top_n=TOP_N_CANDIDATES,
            min_market_cap=FILTER_MIN_MARKET_CAP,
            max_market_cap=FILTER_MAX_MARKET_CAP,
        )
        self._info(f"买入候选: {len(self.candidates)} 只")

        # 打印 Top 10
        for i, c in enumerate(self.candidates[:10], 1):
            self._info(f"  #{i} {c.code} {c.name} "
                       f"总分{c.composite:.1f} "
                       f"F1={c.f1:.0f} F2={c.f2:.0f} F3={c.f3:.0f} "
                       f"F4={c.f4:.0f} F5={c.f5:.0f} "
                       f"连板={c.board_height}")

    # ----------------------------------------------------------
    # Step 5: 实盘交易决策（仅 live 模式）
    # ----------------------------------------------------------

    def step_trade_decision(self):
        """应用交易规则引擎，生成买卖决策"""
        if not self.live_mode:
            self._info("--- Step 5: 跳过 (非 live 模式) ---")
            return

        self._info("--- Step 5: 交易决策 ---")

        # 加载当前持仓和风控状态
        self._load_positions()
        self._load_risk_state()
        self.cash = self._calc_cash()

        self._info(f"当前持仓: {len(self.positions)} 只 | 可用现金: {self.cash:.2f} | "
                   f"连续亏损: {self.risk.consecutive_losses} 笔")

        # ---- 检查持仓卖出 ----
        sell_decisions = self._check_sells()
        for decision in sell_decisions:
            self._execute_sell(decision)

        # ---- 检查买入候选 ----
        if self.risk.paused_until:
            self._info(f"[风控] 暂停交易至 {self.risk.paused_until}，跳过买入")
        else:
            self._check_buys()

        # ---- 保存持仓 ----
        self._save_positions()
        self._save_risk_state()

        # ---- 净值快照 ----
        prices = self._get_current_prices()
        nav, pnl, pnl_pct = save_nav_snapshot(
            self.cash, self.positions, prices, self.date,
            str(NAV_LOG_FILE),
        )
        self._info(f"当日净值: {nav:.2f} | 总盈亏: {pnl:+.2f} ({pnl_pct * 100:+.2f}%)")

    def _load_positions(self):
        if POSITIONS_FILE.exists():
            try:
                with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.positions = [Position(**p) for p in data.get("positions", [])]
                self._info(f"加载持仓: {len(self.positions)} 只")
            except Exception as e:
                self._warn(f"加载持仓失败: {e}")
                self.positions = []

    def _save_positions(self):
        os.makedirs(POSITIONS_FILE.parent, exist_ok=True)
        data = {
            "date": self.date,
            "positions": [
                {
                    "code": p.code, "name": p.name,
                    "buy_price": p.buy_price, "buy_date": p.buy_date,
                    "quantity": p.quantity, "position_pct": p.position_pct,
                    "score": p.score, "buy_signal": p.buy_signal,
                }
                for p in self.positions
            ],
        }
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_risk_state(self):
        if RISK_STATE_FILE.exists():
            try:
                with open(RISK_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.risk = RiskState(
                    consecutive_losses=data.get("consecutive_losses", 0),
                    last_trade_pnl=data.get("last_trade_pnl", 0.0),
                    daily_pnl=data.get("daily_pnl", 0.0),
                    weekly_pnl=data.get("weekly_pnl", 0.0),
                    paused_until=data.get("paused_until", ""),
                )
            except Exception as e:
                self._warn(f"加载风控状态失败: {e}")

    def _save_risk_state(self):
        os.makedirs(RISK_STATE_FILE.parent, exist_ok=True)
        data = {
            "date": self.date,
            "consecutive_losses": self.risk.consecutive_losses,
            "last_trade_pnl": self.risk.last_trade_pnl,
            "daily_pnl": self.risk.daily_pnl,
            "weekly_pnl": self.risk.weekly_pnl,
            "paused_until": self.risk.paused_until,
        }
        with open(RISK_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _calc_cash(self) -> float:
        locked = sum(p.buy_price * p.quantity for p in self.positions)
        # 估算手续费（万2.5双向 + 卖印花税0.1%）
        locked_with_fee = locked * 1.002  # 预留0.2%
        return POSITION_CAPITAL - locked_with_fee

    def _get_current_prices(self) -> dict:
        """获取持仓股票当前价格（用当日K线收盘价近似）"""
        prices = {}
        for pos in self.positions:
            kline = fetch_daily_kline(pos.code, self.date, self.date)
            if not kline.empty:
                prices[pos.code] = float(kline.iloc[-1]["收盘"])
            else:
                prices[pos.code] = pos.buy_price
        return prices

    def _check_sells(self) -> list[dict]:
        """检查所有持仓，返回需要卖出的决策列表"""
        decisions = []
        for pos in list(self.positions):
            buy_date = pos.buy_date
            hold_days = self._count_hold_days(buy_date, self.date)

            # 获取当日行情
            kline = fetch_daily_kline(pos.code, self.date, self.date)
            if kline.empty:
                self._warn(f"  无法获取 {pos.code} {pos.name} 当日行情，跳过")
                continue

            row = kline.iloc[-1]
            open_price = float(row["开盘"])
            pre_close = float(row.get("昨收", 0))
            high = float(row["最高"])
            low = float(row["最低"])
            current_price = float(row["收盘"])

            # 应用卖出规则
            sell_now, signal, sell_price = should_sell(
                pos, open_price, pre_close, high, low, current_price, hold_days,
            )

            pnl_pct = (sell_price - pos.buy_price) / pos.buy_price if sell_price > 0 else \
                       (current_price - pos.buy_price) / pos.buy_price

            status = f"持有{hold_days}天"
            if sell_now:
                status += f" → [{signal}]"
            else:
                status += f" 浮动盈亏 {pnl_pct * 100:+.2f}%"

            self._info(f"  {pos.code} {pos.name} | {status}")

            if sell_now:
                decisions.append({
                    "pos": pos,
                    "signal": signal,
                    "sell_price": sell_price,
                    "hold_days": hold_days,
                })

        return decisions

    def _execute_sell(self, decision: dict):
        """执行卖出，更新持仓、现金、交易记录"""
        pos = decision["pos"]
        signal = decision["signal"]
        sell_price = decision["sell_price"]
        hold_days = decision["hold_days"]

        amount = sell_price * pos.quantity
        commission = max(amount * 0.00025, 5.0)
        stamp_tax = amount * 0.001
        net_proceed = amount - commission - stamp_tax
        pnl = net_proceed - (pos.buy_price * pos.quantity)
        pnl_pct = (sell_price - pos.buy_price) / pos.buy_price

        self.cash += net_proceed
        self.positions.remove(pos)

        record = TradeRecord(
            buy_date=pos.buy_date, sell_date=self.date,
            code=pos.code, name=pos.name,
            buy_price=pos.buy_price, sell_price=sell_price,
            quantity=pos.quantity, position_pct=pos.position_pct,
            pnl=pnl, pnl_pct=pnl_pct, hold_days=hold_days,
            buy_signal=pos.buy_signal, sell_signal=signal,
            score=pos.score, is_closed=True,
        )
        self.trades_today.append(record)

        # 写交易日志
        log_sell_update(record, str(TRADE_LOG_FILE))

        self._info(f"  [卖出] {pos.code} {pos.name} "
                   f"成本{pos.buy_price:.2f} → 卖出{sell_price:.2f} "
                   f"盈亏{pnl:+.2f} ({pnl_pct * 100:+.2f}%) "
                   f"持有{hold_days}天 | 信号:{signal}")

    def _check_buys(self):
        """检查买入候选，按评分从高到低尝试买入"""
        available_slots = MAX_POSITIONS - len(self.positions)
        if available_slots <= 0:
            self._info("仓位已满，跳过买入")
            return

        self._info(f"可用仓位: {available_slots} 个")

        zt_index = {}
        for _, row in self.zt_pool.iterrows():
            code = str(row.get("代码", ""))
            if code:
                zt_index[code] = row

        bought = 0
        for cand in self.candidates:
            if bought >= available_slots:
                break

            row = zt_index.get(cand.code)
            if row is None:
                continue

            seal_time = int(row.get("seal_time_cleaned", 999999))
            open_count = int(row.get("打开次数", row.get("open_count", 0)))

            ok, signal = can_buy(
                cand.code, cand.name, cand.composite, cand.board_height,
                seal_time, open_count, self.positions, self.risk,
            )

            if not ok:
                continue

            # 获取预估买入价（用当日收盘价近似次日开盘价）
            kline = fetch_daily_kline(cand.code, self.date, self.date)
            if kline.empty:
                continue

            buy_price = float(kline.iloc[-1]["收盘"])

            # 检查次日可买性（不能是一字板）
            pre_close = float(kline.iloc[-1].get("昨收", buy_price))
            limit_rate = 0.20 if cand.code.startswith(("300", "301", "688")) else 0.10
            limit_up = pre_close * (1 + limit_rate)
            if buy_price >= limit_up * 0.995:
                continue  # 接近涨停价，不追

            quantity, pct = calc_position_size(self.cash, buy_price)
            if quantity < 100 or quantity * buy_price > self.cash * 0.95:
                continue

            # 执行买入
            amount = buy_price * quantity
            commission = max(amount * 0.00025, 5.0)
            total_cost = amount + commission

            if total_cost > self.cash:
                continue

            self.cash -= total_cost

            pos = Position(
                code=cand.code, name=cand.name,
                buy_price=buy_price, buy_date=self.date,
                quantity=quantity, position_pct=pct,
                score=cand.composite, buy_signal=signal,
            )
            self.positions.append(pos)

            record = TradeRecord(
                buy_date=self.date, code=cand.code, name=cand.name,
                buy_price=buy_price, quantity=quantity,
                position_pct=pct, buy_signal=signal,
                score=cand.composite, is_closed=False,
            )
            self.trades_today.append(record)

            # 写交易日志
            log_buy(record, str(TRADE_LOG_FILE))

            self._info(f"  [买入] {cand.code} {cand.name} "
                       f"价格{buy_price:.2f} x{quantity}股 "
                       f"仓位{pct * 100:.1f}% | 评分{cand.composite:.1f} | 信号:{signal}")
            bought += 1

        if bought == 0:
            self._info("无符合条件的买入")

    def _count_hold_days(self, buy_date: str, current_date: str) -> int:
        """计算持仓交易日数（简化：自然日除以7*5近似）"""
        try:
            buy_dt = datetime.strptime(buy_date, "%Y%m%d")
            cur_dt = datetime.strptime(current_date, "%Y%m%d")
            days = (cur_dt - buy_dt).days
            # 粗略：排除周末
            trade_days = max(1, int(days * 5 / 7))
            return trade_days
        except ValueError:
            return 1

    # ----------------------------------------------------------
    # Step 6: 风控检查（仅 live 模式）
    # ----------------------------------------------------------

    def step_risk_check(self):
        """收盘后检查风控阈值"""
        if not self.live_mode:
            self._info("--- Step 6: 跳过 (非 live 模式) ---")
            return

        self._info("--- Step 6: 风控检查 ---")

        self.risk = check_risk_controls(self.risk, self.trades_today)

        if self.risk.paused_until:
            self._info(f"[风控] 触发暂停! 暂停至 {self.risk.paused_until}")
            self._info(f"  连续亏损: {self.risk.consecutive_losses} 笔")
            self._info(f"  当日盈亏: {self.risk.daily_pnl * 100:+.2f}%")
            self._info(f"  当周盈亏: {self.risk.weekly_pnl * 100:+.2f}%")
        else:
            self._info(f"风控正常 | 连续亏损: {self.risk.consecutive_losses} 笔")

        self._save_risk_state()

    # ----------------------------------------------------------
    # Step 7: 结果输出
    # ----------------------------------------------------------

    def step_save_results(self):
        """保存扫描结果到 output/YYYY-MM-DD/"""
        self._info("--- Step 7: 结果保存 ---")

        date_dir = OUTPUT_DIR / self.date
        os.makedirs(date_dir, exist_ok=True)

        # 1. 市场情绪 JSON
        market_path = date_dir / "market_overview.json"
        with open(market_path, "w", encoding="utf-8") as f:
            json.dump({"date": self.date, **self.market}, f, ensure_ascii=False, indent=2)
        self._info(f"市场情绪: {market_path}")

        # 2. 买入候选 CSV
        cand_path = date_dir / "candidates.csv"
        with open(cand_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["排名", "代码", "名称", "总分", "F1质量", "F2板块", "F3资金", "F4形态", "F5情绪", "连板"])
            for i, s in enumerate(self.candidates, 1):
                w.writerow([i, s.code, s.name, f"{s.composite:.1f}",
                            f"{s.f1:.0f}", f"{s.f2:.0f}", f"{s.f3:.0f}",
                            f"{s.f4:.0f}", f"{s.f5:.0f}", s.board_height])
        self._info(f"买入候选: {cand_path} ({len(self.candidates)} 只)")

        # 3. 全量打分 CSV（含所有涨停股）
        full_path = date_dir / "full_scores.csv"
        with open(full_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["代码", "名称", "总分", "F1质量", "F2板块", "F3资金", "F4形态", "F5情绪", "连板", "是否候选"])
            candidate_codes = {c.code for c in self.candidates}
            for s in sorted(self.scored, key=lambda x: x.composite, reverse=True):
                w.writerow([s.code, s.name, f"{s.composite:.1f}",
                            f"{s.f1:.0f}", f"{s.f2:.0f}", f"{s.f3:.0f}",
                            f"{s.f4:.0f}", f"{s.f5:.0f}", s.board_height,
                            "是" if s.code in candidate_codes else ""])
        self._info(f"全量打分: {full_path} ({len(self.scored)} 只)")

        # 4. 交易决策（仅 live 模式）
        if self.live_mode and self.trades_today:
            trade_path = date_dir / "trade_decisions.csv"
            with open(trade_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["类型", "代码", "名称", "价格", "数量", "仓位%", "盈亏", "盈亏%",
                             "持有天数", "评分", "买入信号", "卖出信号"])
                for t in self.trades_today:
                    t_type = "卖出" if t.is_closed else "买入"
                    w.writerow([
                        t_type, t.code, t.name,
                        f"{t.sell_price:.2f}" if t.is_closed else f"{t.buy_price:.2f}",
                        t.quantity, f"{t.position_pct * 100:.1f}",
                        f"{t.pnl:+.2f}" if t.is_closed else "",
                        f"{t.pnl_pct * 100:+.2f}%" if t.is_closed else "",
                        t.hold_days if t.is_closed else "",
                        f"{t.score:.1f}", t.buy_signal, t.sell_signal,
                    ])
            self._info(f"交易决策: {trade_path} ({len(self.trades_today)} 笔)")

        # 5. 今日操作摘要
        summary_path = date_dir / "summary.txt"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(f"=== 一进二 每日扫描摘要 ===\n")
            f.write(f"日期: {self.date}\n")
            f.write(f"市场情绪: {self.market.get('sentiment_level', 'N/A')}\n")
            f.write(f"涨停: {self.market.get('total_zt', 0)} | ")
            f.write(f"炸板率: {self.market.get('break_rate', 0) * 100:.1f}% | ")
            f.write(f"最高板: {self.market.get('max_board', 0)} | ")
            f.write(f"昨日溢价: {self.market.get('prev_premium', 0):.2f}%\n")
            f.write(f"买入候选: {len(self.candidates)} 只\n\n")

            if self.candidates:
                f.write("--- Top 10 候选 ---\n")
                for i, c in enumerate(self.candidates[:10], 1):
                    f.write(f"  #{i} {c.code} {c.name} 总分{c.composite:.1f} "
                            f"F1={c.f1:.0f} F2={c.f2:.0f} F3={c.f3:.0f} "
                            f"F4={c.f4:.0f} F5={c.f5:.0f} 连板{c.board_height}\n")

            if self.live_mode:
                f.write(f"\n--- 交易决策 ---\n")
                buys = [t for t in self.trades_today if not t.is_closed]
                sells = [t for t in self.trades_today if t.is_closed]
                if buys:
                    f.write(f"买入 {len(buys)} 笔:\n")
                    for t in buys:
                        f.write(f"  {t.code} {t.name} @{t.buy_price:.2f} x{t.quantity}股 ({t.buy_signal})\n")
                if sells:
                    f.write(f"卖出 {len(sells)} 笔:\n")
                    for t in sells:
                        f.write(f"  {t.code} {t.name} {t.buy_price:.2f}→{t.sell_price:.2f} "
                                f"盈亏{t.pnl_pct * 100:+.2f}% 持有{t.hold_days}天 ({t.sell_signal})\n")
                if not buys and not sells:
                    f.write("今日无交易\n")

                f.write(f"\n当前持仓: {len(self.positions)} 只\n")
                for p in self.positions:
                    f.write(f"  {p.code} {p.name} @{p.buy_price:.2f} x{p.quantity}股 ({p.buy_signal})\n")
                f.write(f"风控状态: {'暂停至' + self.risk.paused_until if self.risk.paused_until else '正常'}\n")

        self._info(f"摘要: {summary_path}")

    # ----------------------------------------------------------
    # 主流程
    # ----------------------------------------------------------

    def run(self) -> dict:
        """执行完整更新管道"""
        t_start = time.time()

        self._info(f"{'='*50}")
        self._info(f"一进二每日更新 | 日期: {self.date} | 模式: {'实盘' if self.live_mode else '扫描'}")
        self._info(f"{'='*50}")

        # Step 1: 数据获取
        if not self.step_fetch_data():
            return {"status": "skipped", "date": self.date, "reason": "无数据或非交易日"}

        # Step 2: 预处理 & 市场情绪
        self.step_preprocess()

        # Step 3: 五因子打分
        self.step_score()

        # Step 4: 过滤排序
        self.step_filter()

        # Step 5: 交易决策（仅 live 模式）
        self.step_trade_decision()

        # Step 6: 风控检查（仅 live 模式）
        self.step_risk_check()

        # Step 7: 结果保存
        self.step_save_results()

        elapsed = time.time() - t_start
        self._info(f"\n{'='*50}")
        self._info(f"更新完成! 耗时 {elapsed:.1f}s")
        self._info(f"{'='*50}")

        return {
            "status": "ok",
            "date": self.date,
            "market": self.market,
            "candidates": self.candidates,
            "total_scored": len(self.scored),
            "trades_today": len(self.trades_today),
            "elapsed": round(elapsed, 1),
        }

    # ----------------------------------------------------------
    # 日志辅助
    # ----------------------------------------------------------

    def _info(self, msg: str):
        """带时间戳的日志"""
        ts = datetime.now().strftime("%H:%M:%S")
        text = f"[{ts}] {msg}"
        print(text)
        self.log.append(text)

    def _warn(self, msg: str):
        self._info(f"[WARN] {msg}")

    def _error(self, msg: str):
        self._info(f"[ERROR] {msg}")

    @staticmethod
    def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
        for c in candidates:
            if c in df.columns:
                return c
        return None


# ============================================================
# CLI 入口
# ============================================================

def run_pipeline(date: str = None, live: bool = False, weights: FactorWeights = None) -> dict:
    """外部调用入口（与 daily_scan.run 保持一致的接口风格）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    pipeline = UpdatePipeline(date, live_mode=live, weights=weights)
    return pipeline.run()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="一进二 · 每日数据自动更新管道")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD，默认今日")
    parser.add_argument("--live", action="store_true", help="模拟实盘交易决策模式")
    parser.add_argument("--weights", default=None, help="权重 JSON 文件路径（可选）")
    args = parser.parse_args()

    w = FactorWeights()
    if args.weights:
        with open(args.weights, "r", encoding="utf-8") as f:
            w_data = json.load(f)
            # 覆盖默认权重
            for k, v in w_data.items():
                if hasattr(w, k):
                    setattr(w, k, v)

    result = run_pipeline(date=args.date, live=args.live, weights=w)
    status = result.get("status", "error")
    sys.exit(0 if status == "ok" else 1)
