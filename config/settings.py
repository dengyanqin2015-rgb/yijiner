"""全局参数配置 —— 权重、阈值、回测参数（可被优化器覆盖）"""

from dataclasses import dataclass, field


@dataclass
class SubWeights:
    """一级因子内的子因子权重"""
    sub1: float = 0.35
    sub2: float = 0.25
    sub3: float = 0.25
    sub4: float = 0.15


@dataclass
class FactorWeights:
    limit_up_quality: float = 0.30   # F1 涨停板质量
    sector_effect: float = 0.25      # F2 板块效应
    fund_strength: float = 0.20      # F3 资金强度
    technical_form: float = 0.15     # F4 技术形态
    market_sentiment: float = 0.10   # F5 市场情绪

    # 子因子权重
    f1_sub: SubWeights = field(default_factory=lambda: SubWeights(0.35, 0.25, 0.25, 0.15))
    f2_sub: SubWeights = field(default_factory=lambda: SubWeights(0.35, 0.30, 0.20, 0.15))
    f3_sub: SubWeights = field(default_factory=lambda: SubWeights(0.35, 0.25, 0.20, 0.20))
    f4_sub: SubWeights = field(default_factory=lambda: SubWeights(0.25, 0.30, 0.25, 0.20))
    f5_sub: SubWeights = field(default_factory=lambda: SubWeights(0.30, 0.25, 0.25, 0.20))


@dataclass
class BacktestConfig:
    start_date: str = "20240101"
    end_date: str = "20251231"
    initial_capital: float = 1_000_000.0
    max_positions: int = 5
    position_size: float = 0.20   # 单票仓位 20%
    stop_loss: float = -0.05      # 次日低开超5%止损
    take_profit: float = 0.03     # 次日高开3%+止盈卖出


# 候选过滤阈值
FILTER_MIN_MARKET_CAP = 10   # 流通市值下限 (亿)
FILTER_MAX_MARKET_CAP = 500  # 流通市值上限 (亿)
FILTER_MIN_DAYS_LISTED = 60  # 上市最少天数
TOP_N_CANDIDATES = 20        # 默认输出前20只

# 缓存
CACHE_DIR = "data/cache"
CACHE_TTL_HOURS = 24
