"""买入候选过滤: 排除ST/一字板/尾盘/次新，排序取Top N"""

import pandas as pd
from data.preprocessor import is_yizi_board, is_tail_attack, is_st_stock
from signals.scorer import ScoredStock


def filter_and_rank(
    scored: list[ScoredStock],
    zt_pool: pd.DataFrame,
    top_n: int = 20,
    min_market_cap: float = 10,
    max_market_cap: float = 500,
    min_days_listed: int = 60,
) -> list[ScoredStock]:
    """过滤后排序返回Top N"""

    zt_index = {}
    for _, row in zt_pool.iterrows():
        code = str(row.get("代码", ""))
        if code:
            zt_index[code] = row

    filtered = []
    for s in scored:
        row = zt_index.get(s.code)
        if row is None:
            continue

        if is_st_stock(s.code, s.name):
            continue

        seal_time = int(row.get("seal_time_cleaned", 999999))
        open_count = int(row.get("打开次数", row.get("open_count", 0)))

        if is_yizi_board(seal_time, open_count):
            continue

        if is_tail_attack(seal_time):
            continue

        try:
            cap = float(row.get("流通市值", row.get("float_market_cap", 0))) / 1e8
            if cap < min_market_cap or cap > max_market_cap:
                continue
        except (ValueError, TypeError):
            pass

        filtered.append(s)

    filtered.sort(key=lambda s: s.composite, reverse=True)
    return filtered[:top_n]
