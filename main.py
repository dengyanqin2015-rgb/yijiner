"""一进二 · A股打板/次日冲高策略系统

Usage:
    python main.py daily                       扫描今日
    python main.py daily --date 20260601       扫描指定日期
    python main.py daily --top 30              输出前30只
    python main.py update                      每日数据更新管道（仅扫描模式）
    python main.py update --live               每日数据更新 + 模拟实盘交易
    python main.py update --date 20260609 --live
    python main.py trade --date 20260609       当日交易决策（买入+卖出）
    python main.py status                      查看当前持仓和风控状态
    python main.py backtest --start 20240101 --end 20251231
    python main.py optimize --method genetic
    python main.py optimize --method bayesian --trials 100
"""

import argparse
import json
import sys
import os

# Windows 终端 UTF-8 编码修复
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import FactorWeights, BacktestConfig
from decision.daily_scan import run as daily_run
from output.console import print_summary, print_sell_advice
from output.report import save_candidates, save_market_overview, save_sell_advice


def cmd_daily(args):
    """每日精选推荐 —— A股打板 + ETF T+0"""
    from signals.proven_scorer import scan as scan_stocks
    from signals.etf_scanner import scan as scan_etf
    from data.fetcher import fetch_daily_zt_pool, fetch_breakout_pool, fetch_previous_zt_performance
    from data.preprocessor import parse_board_height, clean_seal_time

    date = args.date or _today()
    print(f"\n╔══════════════════════════════════════════╗")
    print(f"║  一进二 · 每日精选推荐                    ║")
    print(f"║  日期: {date}  本金: 3000元               ║")
    print(f"╚══════════════════════════════════════════╝")

    # === 市场情绪 ===
    zt_pool = fetch_daily_zt_pool(date)
    breakout = fetch_breakout_pool(date)
    prev_perf = fetch_previous_zt_performance(date)

    total_zt = len(zt_pool) if not zt_pool.empty else 0
    total_break = len(breakout) if not breakout.empty else 0
    break_rate = total_break / (total_zt + total_break) * 100 if (total_zt + total_break) > 0 else 0
    max_board = int(zt_pool["连板数"].max()) if not zt_pool.empty and "连板数" in zt_pool.columns else 0

    # 昨日溢价
    prev_premium = 0.0
    if not prev_perf.empty:
        for col in ["今日涨幅", "涨跌幅"]:
            if col in prev_perf.columns:
                prev_premium = float(prev_perf[col].mean())
                break

    sentiment = "暖" if total_zt >= 80 else ("温" if total_zt >= 40 else "冷")
    if break_rate > 40:
        sentiment += "⚠️炸板率高"
    print(f"\n  市场: {sentiment} | 涨停{total_zt}只 | 炸板率{break_rate:.0f}% | 最高{max_board}板 | 昨日溢价{prev_premium:+.1f}%")

    # === A股精选 ===
    print(f"\n  ┌─ A股打板精选 (主板+早封+非ST+非一字板) ─┐")
    picks = scan_stocks(date, top_n=args.top)

    if not picks:
        print(f"  │ 今日无符合条件的标的                      │")
    else:
        print(f"  │ {'代码':<8} {'名称':<8} {'评分':>5} {'预期':>6} {'现价':>7} {'建议买入':>8} {'止盈':>7} {'连板':>4} │")
        print(f"  │ {'-'*60} │")
        for p in picks[:10]:
            lots = int(3000 * 0.55 / (p.buy_price * 100))
            tag = "←买" if p.score >= 60 else ""
            print(f"  │ {p.code:<8} {p.name:<8} {p.score:>5.0f} {p.expected_return:>+5.1f}% {p.price:>7.2f} {p.buy_price:>8.2f} {p.sell_price:>7.2f} {p.board_height:>3}板 {tag}")

        # 最佳推荐
        best = picks[0]
        lots = max(1, int(1500 / (best.buy_price * 100)))
        cost = best.buy_price * lots * 100
        print(f"  └{'─'*60}┘")
        print(f"\n  ★ 首选: {best.code} {best.name}")
        print(f"    明早9:25竞价买入 {lots}手({lots*100}股) × {best.buy_price}元 ≈ {cost:.0f}元")
        print(f"    后天开盘卖出 目标{best.sell_price}元 (+3%)  止损{best.price*0.95:.2f}元 (-5%)")
        print(f"    封板{best.seal_time} | 换手{best.turnover:.1f}% | 炸板{best.open_count}次 | {best.board_height}板")

    # === 尾盘确认 ===
    from signals.tail_market import scan as scan_tail

    print(f"\n  ┌─ 尾盘确认策略 (涨3-7%+收最高+资金活跃) ───┐")
    tail_picks = scan_tail(capital=3000.0)

    if not tail_picks:
        print(f"  │ 今日无符合条件的标的（需14:30后运行）      │")
    else:
        print(f"  │ {'代码':<8} {'名称':<8} {'评分':>5} {'涨幅':>6} {'现价':>7} {'建议买':>7} {'止盈':>7} {'止损':>7} │")
        print(f"  │ {'-'*60} │")
        for t in tail_picks[:8]:
            lots = max(1, int(1500 / (t.buy_price * 100)))
            print(f"  │ {t.code:<8} {t.name:<8} {t.score:>5.0f} {t.pct:>+5.1f}% {t.price:>7.2f} {t.buy_price:>7.2f} {t.sell_price:>7.2f} {t.stop_price:>7.2f}  ←{lots}手")

        best_t = tail_picks[0]
        lots = max(1, int(1500 / (best_t.buy_price * 100)))
        print(f"  └{'─'*60}┘")
        print(f"\n  ★ 尾盘首选: {best_t.code} {best_t.name}  评分{best_t.score:.0f}")
        print(f"    理由: {best_t.reason}")
        print(f"    14:50买入 {lots}手×{best_t.buy_price}元 | 明早9:30{best_t.sell_price}元卖(+2.5%) | 止损{best_t.stop_price}元(-1.5%)")

    # === ETF T+0 ===
    print(f"\n  ┌─ T+0 ETF日内波段 (可当天买卖) ──────────┐")
    etf_picks = scan_etf()

    if not etf_picks:
        print(f"  │ ETF数据获取失败                            │")
    else:
        print(f"  │ {'代码':<8} {'名称':<14} {'现价':>7} {'涨跌':>7} {'信号':>4} {'买入价':>8} {'止盈':>7} {'止损':>7} │")
        print(f"  │ {'-'*60} │")
        for e in etf_picks[:5]:
            sig = "★" if e.signal == "买入" else " "
            print(f"  │ {e.code:<8} {e.name:<14} {e.price:>7.3f} {e.change_pct:>+6.2f}% {e.signal:>4} {e.buy_price:>8.3f} {e.sell_price:>7.3f} {e.stop_loss:>7.3f} {sig}")

        buys = [e for e in etf_picks if e.signal == "买入"]
        if buys:
            best_etf = buys[0]
            shares = int(3000 / best_etf.price / 100) * 100
            print(f"  └{'─'*60}┘")
            print(f"\n  ★ ETF首选: {best_etf.code} {best_etf.name}")
            print(f"    现价{best_etf.price:.3f}元 × {shares}份 ≈ {best_etf.price*shares:.0f}元  T+0随时买卖")
            print(f"    日内+2%止盈={best_etf.sell_price:.3f}  -2%止损={best_etf.stop_loss:.3f}")

    # 风控提醒
    if break_rate > 40:
        print(f"\n  ⚠️ 今日炸板率{break_rate:.0f}%偏高，建议减少仓位或只做ETF防守")
    if prev_premium < -1:
        print(f"  ⚠️ 昨日涨停股今日溢价{prev_premium:+.1f}%，打板环境恶劣")

    print(f"\n  → 完整数据: output/{date}/candidates.csv")
    print(f"  → 实盘模式: python main.py trade --date {date}\n")


def cmd_backtest(args):
    """回测"""
    from backtest.engine import run_backtest
    from rich.console import Console
    from rich.table import Table

    console = Console()
    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        max_positions=args.max_positions,
    )

    print(f"\n回测区间: {args.start} ~ {args.end}, 初始资金: {args.capital:,.0f}")
    print("回测运行中...")

    result = run_backtest(config=config)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])

    table = Table(title="回测结果", style="cyan")
    table.add_column("指标", style="bold")
    table.add_column("数值")
    for k, v in metrics.items():
        table.add_row(k, str(v))
    console.print(table)

    print(f"\n共 {len(trades)} 笔交易")
    if trades:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        print(f"盈利: {wins} 笔, 亏损: {len(trades) - wins} 笔")


def cmd_optimize(args):
    """参数优化"""
    if args.method == "genetic":
        from optimizer.genetic import run as genetic_run
        params, score = genetic_run(
            start_date=args.start, end_date=args.end,
            pop_size=args.pop_size, generations=args.generations,
        )
    else:
        from optimizer.bayesian import run as bayesian_run
        params, score = bayesian_run(
            start_date=args.start, end_date=args.end,
            n_trials=args.trials,
        )

    print("\n最优参数:")
    for k, v in sorted(params.items()):
        print(f"  {k}: {v:.4f}")

    # 写入 settings.py
    import json
    best_path = os.path.join(os.path.dirname(__file__), "output", "best_params.json")
    os.makedirs(os.path.dirname(best_path), exist_ok=True)
    with open(best_path, "w") as f:
        json.dump(params, f, indent=2)
    print(f"\n参数已保存: {best_path}")


def cmd_update(args):
    """每日数据更新管道"""
    from data.daily_update import run_pipeline

    date = args.date or _today()

    # 加载权重（如果有优化结果）
    weights = FactorWeights()
    best_params_path = os.path.join(os.path.dirname(__file__), "output", "best_params.json")
    if os.path.exists(best_params_path):
        from optimizer.objective import params_to_weights
        with open(best_params_path, "r") as f:
            best = json.load(f)
        weights = params_to_weights(best)
        print(f"[INFO] 使用优化权重: {best_params_path}")

    result = run_pipeline(date=date, live=args.live, weights=weights)

    if result.get("status") == "ok":
        market = result.get("market", {})
        print(f"\n市场情绪: {market.get('sentiment_level', 'N/A')}")
        print(f"涨停 {market.get('total_zt', 0)} | 炸板率 {market.get('break_rate', 0) * 100:.1f}%")
        print(f"候选 {len(result.get('candidates', []))} 只 | 耗时 {result.get('elapsed', 0)}s")
        if args.live:
            print(f"今日交易 {result.get('trades_today', 0)} 笔")


def cmd_trade(args):
    """当日交易决策"""
    from data.daily_update import run_pipeline

    date = args.date or _today()

    weights = FactorWeights()
    best_params_path = os.path.join(os.path.dirname(__file__), "output", "best_params.json")
    if os.path.exists(best_params_path):
        from optimizer.objective import params_to_weights
        with open(best_params_path, "r") as f:
            best = json.load(f)
        weights = params_to_weights(best)

    print(f"\n=== 一进二 实盘交易决策 ===")
    print(f"日期: {date} | 本金: 3000元")
    print()

    result = run_pipeline(date=date, live=True, weights=weights)

    if result.get("status") == "ok":
        # 显示持仓摘要
        _print_position_summary()


def cmd_positions(args):
    """当前持仓建议"""
    _print_position_summary()


def cmd_status(args):
    """查看当前持仓和风控状态"""
    _print_position_summary()
    _print_risk_status()


def _print_position_summary():
    """打印当前持仓摘要"""
    positions_file = os.path.join(os.path.dirname(__file__), "output", "positions.json")
    if not os.path.exists(positions_file):
        print("暂无持仓数据。运行 python main.py update --live 开启实盘交易。")
        return

    with open(positions_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    positions = data.get("positions", [])
    print(f"持仓日期: {data.get('date', 'N/A')}")
    print(f"持仓数量: {len(positions)} 只")

    if positions:
        print(f"{'代码':<8} {'名称':<8} {'买入价':>8} {'数量':>6} {'仓位%':>8} {'买入信号':<12}")
        print("-" * 60)
        for p in positions:
            print(f"{p['code']:<8} {p['name']:<8} {p['buy_price']:>8.2f} "
                  f"{p['quantity']:>6} {p['position_pct'] * 100:>7.1f}% "
                  f"{p['buy_signal']:<12}")


def _print_risk_status():
    """打印风控状态"""
    risk_file = os.path.join(os.path.dirname(__file__), "output", "risk_state.json")
    if not os.path.exists(risk_file):
        print("\n风控状态: 无历史记录")
        return

    with open(risk_file, "r", encoding="utf-8") as f:
        risk = json.load(f)

    print(f"\n风控状态 (截至 {risk.get('date', 'N/A')}):")
    print(f"  连续亏损: {risk.get('consecutive_losses', 0)} 笔")
    print(f"  当日盈亏: {risk.get('daily_pnl', 0) * 100:+.2f}%")
    print(f"  当周盈亏: {risk.get('weekly_pnl', 0) * 100:+.2f}%")

    paused = risk.get('paused_until', '')
    if paused:
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        if today <= paused:
            print(f"  [暂停] 暂停交易至 {paused}")
        else:
            print(f"  暂停期已过，可恢复交易")
    else:
        print(f"  交易状态: 正常")


def _today() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d")


def main():
    parser = argparse.ArgumentParser(description="一进二 · A股打板策略系统")
    sub = parser.add_subparsers(dest="command")

    # daily
    p_daily = sub.add_parser("daily", help="每日扫描")
    p_daily.add_argument("--date", help="日期 YYYYMMDD")
    p_daily.add_argument("--top", type=int, default=20, help="输出前N只")
    p_daily.add_argument("--save", action="store_true", default=True, help="保存CSV报告")

    # update (数据管道)
    p_up = sub.add_parser("update", help="每日数据更新管道")
    p_up.add_argument("--date", help="日期 YYYYMMDD（默认今日）")
    p_up.add_argument("--live", action="store_true", help="模拟实盘交易模式")

    # trade (实盘决策)
    p_trade = sub.add_parser("trade", help="当日实盘交易决策")
    p_trade.add_argument("--date", help="日期 YYYYMMDD（默认今日）")

    # status
    sub.add_parser("status", help="查看持仓和风控状态")

    # backtest
    p_bt = sub.add_parser("backtest", help="回测")
    p_bt.add_argument("--start", default="20240101")
    p_bt.add_argument("--end", default="20251231")
    p_bt.add_argument("--capital", type=float, default=1_000_000)
    p_bt.add_argument("--max-positions", type=int, default=5)

    # optimize
    p_opt = sub.add_parser("optimize", help="参数优化")
    p_opt.add_argument("--method", default="genetic", choices=["genetic", "bayesian"])
    p_opt.add_argument("--start", default="20240101")
    p_opt.add_argument("--end", default="20250601")
    p_opt.add_argument("--pop-size", type=int, default=50)
    p_opt.add_argument("--generations", type=int, default=30)
    p_opt.add_argument("--trials", type=int, default=100)

    # positions (保留兼容)
    sub.add_parser("positions", help="持仓建议")

    args = parser.parse_args()
    if args.command == "daily":
        cmd_daily(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "trade":
        cmd_trade(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "optimize":
        cmd_optimize(args)
    elif args.command == "positions":
        cmd_positions(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
