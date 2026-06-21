"""模拟券商: 滑点 + 手续费 + 印花税"""

from config.constants import COMMISSION_RATE, STAMP_TAX_RATE, MIN_COMMISSION, SLIPPAGE_RATE


def apply_slippage(price: float, side: str) -> float:
    """买入时加滑点(买贵了), 卖出时减滑点(卖便宜了)"""
    if side == "buy":
        return price * (1 + SLIPPAGE_RATE)
    else:
        return price * (1 - SLIPPAGE_RATE)


def calc_commission(amount: float, side: str) -> float:
    """计算手续费 (佣金 + 卖出印花税)"""
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    if side == "sell":
        commission += amount * STAMP_TAX_RATE
    return commission


def execute_buy(code: str, price: float, quantity: int) -> dict | None:
    """执行买入"""
    exec_price = apply_slippage(price, "buy")
    amount = exec_price * quantity
    fee = calc_commission(amount, "buy")
    total_cost = amount + fee

    return {
        "code": code,
        "action": "buy",
        "price": round(exec_price, 2),
        "quantity": quantity,
        "amount": amount,
        "fee": round(fee, 2),
        "total_cost": round(total_cost, 2),
    }


def execute_sell(code: str, price: float, quantity: int) -> dict | None:
    """执行卖出"""
    exec_price = apply_slippage(price, "sell")
    amount = exec_price * quantity
    fee = calc_commission(amount, "sell")
    net_proceed = amount - fee

    return {
        "code": code,
        "action": "sell",
        "price": round(exec_price, 2),
        "quantity": quantity,
        "amount": amount,
        "fee": round(fee, 2),
        "net_proceed": round(net_proceed, 2),
    }
