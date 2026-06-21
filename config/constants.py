"""A股交易常量 —— 不可变规则"""

# 涨跌停幅度 (按代码前缀)
LIMIT_RATE_MAP = {
    "3": 0.10,  # 沪市主板 600/601/603
    "6": 0.10,  # 沪市主板
    "0": 0.10,  # 深市主板 000/001/002
    "8": 0.20,  # 科创板 688
    "4": 0.20,  # 创业板 300/301
}

# 交易成本
COMMISSION_RATE = 0.00025   # 佣金 万2.5
STAMP_TAX_RATE = 0.00100    # 印花税 卖单边 0.1%
MIN_COMMISSION = 5.0        # 最低佣金5元
SLIPPAGE_RATE = 0.0010      # 滑点 0.1%

# 科创板/创业板代码前缀
KECHUANG_PREFIX = "688"
CHUANGYE_PREFIX = ("300", "301")


def get_limit_rate(code: str) -> float:
    """根据股票代码返回涨跌停幅度"""
    if code.startswith(KECHUANG_PREFIX) or code.startswith(CHUANGYE_PREFIX):
        return 0.20
    return 0.10
