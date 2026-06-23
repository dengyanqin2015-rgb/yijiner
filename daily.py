"""每天收盘后跑一次，生成今日推荐HTML"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run():
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")

    print("拉市场数据...")
    from data.fetcher import fetch_daily_zt_pool, fetch_breakout_pool, fetch_previous_zt_performance
    zt = fetch_daily_zt_pool(today)
    br = fetch_breakout_pool(today)
    prev = fetch_previous_zt_performance(today)

    total_zt = len(zt) if not zt.empty else 0
    total_br = len(br) if not br.empty else 0
    break_rate = round(total_br/(total_zt+total_br)*100,1) if total_zt+total_br>0 else 0

    prev_prem = 0
    if not prev.empty:
        for c in ["今日涨幅","涨跌幅"]:
            if c in prev.columns: prev_prem = round(float(prev[c].mean()),2); break

    print("一进二扫描...")
    from signals.proven_scorer import scan as scan_zt
    picks = scan_zt(today, top_n=20)

    print("双峰扫描...")
    from signals.double_peak import scan as scan_dp
    dp_picks = scan_dp(3000.0)

    print("生成报告...")
    # 纯静态HTML，嵌所有数据
    html = f'''<!DOCTYPE html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>一进二 {today}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:PingFang SC,Microsoft YaHei,sans-serif;background:#0d1117;color:#c8cdd3;font-size:13px;max-width:600px;margin:auto;padding:10px}}
h1{{text-align:center;padding:10px;color:#f0c060;font-size:18px}}h2{{font-size:14px;color:#f0c060;margin:10px 0 6px}}
.bar{{background:#161b22;border-radius:8px;padding:10px;margin-bottom:10px;display:flex;gap:10px;flex-wrap:wrap;font-size:12px}}.bar b{{color:#58a6ff}}
.card{{background:#161b22;border-radius:8px;padding:10px;margin-bottom:8px;border-left:3px solid #30363d}}
.card.top{{border-left-color:#f0c060}}.card .h{{display:flex;justify-content:space-between;margin-bottom:4px}}
.card .code{{font-size:14px;color:#58a6ff;font-weight:bold}}.card .score{{font-size:18px;color:#f0c060;font-weight:bold}}
.card .info{{font-size:11px;color:#8b949e;margin:4px 0}}.card .price{{font-size:12px}}
.price .buy{{color:#58a6ff}}.price .tp{{color:#3fb950;margin:0 8px}}.price .sl{{color:#f85149}}
.empty{{text-align:center;padding:20px;color:#8b949e}}.footer{{text-align:center;padding:20px;color:#8b949e;font-size:11px}}
</style></head><body>
<h1>一进二·每日决策 {today[:4]}-{today[4:6]}-{today[6:]}</h1>

<div class=bar>涨停<b>{total_zt}</b>只 | 炸板率<b>{break_rate}%</b> | 昨日涨停溢价<b>{prev_prem:+.1f}%</b></div>

<h2>🎯 一进二精选</h2>
'''
    for i, p in enumerate(picks):
        top = 'top' if i == 0 else ''
        lots = max(1, int(1500 / (p.buy_price * 100)))
        html += f'''<div class="card {top}"><div class=h><div><span class=code>{p.code}</span> {p.name}</div><div class=score>{p.score:.0f}</div></div>
<div class=info>封板{p.seal_time} | {p.board_height}板 | 换手{p.turnover:.1f}% | 炸板{p.open_count}次</div>
<div class=price><span class=buy>买{p.buy_price}</span><span class=tp>止盈{p.sell_price}</span><span class=sl>止损{p.price*0.95:.2f}</span><span>买{lots}手≈{p.buy_price*lots*100:.0f}元</span></div></div>\n'''

    html += '<h2>🔍 双峰抄底</h2>'
    if dp_picks:
        for p in dp_picks:
            html += f'''<div class=card><div class=h><div><span class=code>{p.code}</span> {p.name}</div><div class=score>{p.score:.0f}</div></div>
<div class=info>{p.reason} | 60日线{p.ma60}</div>
<div class=price><span class=buy>买{p.buy_price}</span><span class=tp>目标{p.target_price}</span><span class=sl>止损{p.stop_price}</span></div></div>\n'''
    else:
        html += '<div class=empty>今日无符合条件的双底标的</div>'

    html += f'<div class=footer>生成时间 {datetime.now().strftime("%H:%M:%S")} | 每天收盘后运行一次</div></body></html>'

    path = f"output/{today}.html"
    os.makedirs("output", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"完成: {path}")

if __name__ == "__main__":
    run()
