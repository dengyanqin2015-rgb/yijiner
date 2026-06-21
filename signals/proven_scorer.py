"""数据验证评分引擎 —— 基于522笔真实交易，4条硬规则"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from data.preprocessor import clean_seal_time, is_yizi_board, is_st_stock, is_tail_attack


@dataclass
class Pick:
    code: str
    name: str
    score: float          # 0-100
    expected_return: float  # 预期次日开盘收益%
    seal_time: str
    turnover: float
    open_count: int
    board_height: int
    price: float
    buy_price: float      # 次日建议买入价
    sell_price: float     # 次日建议卖出价


def score_stock(row: pd.Series) -> tuple[float, float]:
    """
    对一只涨停股评分，返回 (score, expected_return%)
    基于522笔真实交易的回测结论
    """
    score = 0.0
    exp_ret = 0.0

    # === 规则1: 封板时间 (权重40%，最强预测因子) ===
    # 时间格式为 HHMMSS (如 92501 = 9:25:01, 100530 = 10:05:30)
    seal_sec = row.get("seal_time_cleaned", 999999)
    if seal_sec <= 93000:       # 9:30前封板
        score += 40
        exp_ret += 4.81 * 0.40
    elif seal_sec <= 100000:    # 9:30-10:00
        score += 30
        exp_ret += 1.64 * 0.40
    elif seal_sec <= 103000:    # 10:00-10:30
        score += 18
        exp_ret += 1.13 * 0.40
    else:
        score += 5
        exp_ret += 1.04 * 0.40

    # === 规则2: 换手率 (权重30%) ===
    turnover = float(row.get("换手率", row.get("turnover", 15)))
    if turnover <= 3:
        score += 30
        exp_ret += 2.87 * 0.30
    elif turnover <= 8:
        score += 22
        exp_ret += 1.26 * 0.30
    elif turnover <= 15:
        score += 12
        exp_ret += 1.44 * 0.30
    elif turnover <= 25:
        score += 5
        exp_ret += 0.79 * 0.30
    else:
        score += 2
        exp_ret += 0.85 * 0.30

    # === 规则3: 炸板次数 (权重20%) ===
    open_count = int(row.get("打开次数", row.get("炸板次数", row.get("open_count", 0))))
    if open_count == 0:
        score += 14
        exp_ret += 1.64 * 0.20
    elif open_count == 1:
        score += 20  # 烂板回封加分!
        exp_ret += 1.87 * 0.20
    else:
        score += 4
        exp_ret += 0.98 * 0.20

    # === 规则4: 连板高度 (权重10%，首板二板加分) ===
    board_h = int(row.get("board_height", 1))
    if board_h == 1:
        score += 10
        exp_ret += 0.5
    elif board_h == 2:
        score += 8
        exp_ret += 0.3
    elif board_h <= 4:
        score += 4
        exp_ret += 0.1
    else:
        score += 1

    return round(score, 1), round(exp_ret, 2)


def scan(date: str, top_n: int = 10) -> list[Pick]:
    """每日精选扫描 —— 应用4条硬规则，输出Top N"""
    from data.fetcher import fetch_daily_zt_pool, fetch_breakout_pool
    from data.preprocessor import clean_seal_time, parse_board_height

    zt = fetch_daily_zt_pool(date)
    if zt.empty:
        return []

    # === 预处理 ===
    zt = zt.copy()

    # 封板时间清洗
    if "首次封板时间" in zt.columns:
        zt["seal_time_cleaned"] = zt["首次封板时间"].apply(clean_seal_time)

    # 连板解析
    if "涨停统计" in zt.columns:
        zt["board_height"] = zt["涨停统计"].apply(parse_board_height)
    else:
        zt["board_height"] = 1

    # === 硬过滤 ===
    picks = []
    for _, row in zt.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        name = str(row.get("名称", ""))

        # 主板only
        if code.startswith(("300", "301", "688", "689", "920", "8", "4")):
            continue
        # 非ST
        if is_st_stock(code, name):
            continue
        # 一字板排除
        seal_sec = int(row.get("seal_time_cleaned", 999999))
        open_count = int(row.get("打开次数", row.get("炸板次数", 0)))
        if is_yizi_board(seal_sec, open_count):
            continue
        # 尾盘排除
        if is_tail_attack(seal_sec):
            continue
        # 封板时间早于10:30 (HHMMSS格式: 103000=10:30)
        if seal_sec > 103000:
            continue
        # 换手率合理
        turnover = float(row.get("换手率", 99))
        if turnover > 25:
            continue

        # 价格 < 30元 (3000元可买1手)
        price = float(row.get("最新价", 999))
        if price > 30 or price < 3:
            continue

        score, exp_ret = score_stock(row)

        # 计算建议买卖价
        buy_price = round(price * 1.002, 2)    # 次日开盘+0.2%滑点
        sell_price = round(price * 1.03, 2)    # +3%止盈

        picks.append(Pick(
            code=code, name=name, score=score,
            expected_return=exp_ret,
            seal_time=str(row.get("首次封板时间", "")),
            turnover=turnover,
            open_count=open_count,
            board_height=int(row.get("board_height", 1)),
            price=price, buy_price=buy_price, sell_price=sell_price,
        ))

    picks.sort(key=lambda p: p.score, reverse=True)
    return picks[:top_n]
