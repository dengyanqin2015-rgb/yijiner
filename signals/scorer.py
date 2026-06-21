"""综合打分引擎: 五因子加权合成 → composite_score"""

from dataclasses import dataclass, field
import pandas as pd
from config.settings import FactorWeights

import signals.limit_up_quality as limit_up_quality
import signals.sector_effect as sector_effect
import signals.fund_strength as fund_strength
import signals.technical_form as technical_form
import signals.market_sentiment as market_sentiment


@dataclass
class ScoredStock:
    code: str
    name: str
    composite: float = 0.0
    f1: float = 0.0
    f2: float = 0.0
    f3: float = 0.0
    f4: float = 0.0
    f5: float = 0.0
    board_height: int = 1
    sector_info: dict = field(default_factory=dict)


def compute_composite(
    zt_row: pd.Series,
    market: dict,
    kline_info: dict,
    sector_info: dict,
    weights: FactorWeights,
) -> ScoredStock:
    code = str(zt_row.get("代码", ""))
    name = str(zt_row.get("名称", ""))
    board_height = int(zt_row.get("board_height", 1))

    f1 = limit_up_quality.compute(zt_row, weights)
    f2 = sector_effect.compute(zt_row, sector_info, weights)
    f3 = fund_strength.compute(zt_row, weights)
    f4 = technical_form.compute(zt_row, kline_info, weights)
    f5 = market_sentiment.compute(market, weights)

    composite = (
        f1 * weights.limit_up_quality +
        f2 * weights.sector_effect +
        f3 * weights.fund_strength +
        f4 * weights.technical_form +
        f5 * weights.market_sentiment
    )

    return ScoredStock(
        code=code, name=name, composite=composite,
        f1=f1, f2=f2, f3=f3, f4=f4, f5=f5,
        board_height=board_height, sector_info=sector_info,
    )


def rank_candidates(scored: list[ScoredStock], top_n: int = 20) -> list[ScoredStock]:
    scored.sort(key=lambda s: s.composite, reverse=True)
    return scored[:top_n]
