"""一进二 · 量化决策系统 v3 — 专业交易终端"""
import json, os, sys, requests, time, threading
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="一进二v3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_DIR = Path("/tmp/yijiner_data") if os.name != "nt" else Path("data/persist")
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "static"
STATIC_DIR.mkdir(exist_ok=True)

HOLDINGS_FILE = DATA_DIR / "holdings.json"
TRADES_FILE = DATA_DIR / "trades.json"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
WEBHOOK_URL_FILE = DATA_DIR / "webhook_url.txt"
ALERTS_FILE = DATA_DIR / "alerts.json"
CACHE_TTL = 30  # 缓存30秒

# ===================== 工具函数 =====================

def _rjson(path, default=None):
    if path.exists():
        try: return json.load(open(path, "r", encoding="utf-8"))
        except: pass
    return default or {}

def _wjson(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def _scode(c):
    c = str(c).zfill(6)
    return f"sh{c}" if c.startswith("6") else f"sz{c}"

def _today():
    return datetime.now().strftime("%Y%m%d")

def _now():
    return datetime.now().strftime("%H:%M:%S")

def _validate_price(v):
    return 0.01 < float(v) < 10000

def _validate_pct(v):
    return -21 < float(v) < 21

_mcache = {}
def _cached(key, fn, ttl=CACHE_TTL):
    if key in _mcache:
        val, ts = _mcache[key]
        if time.time() - ts < ttl: return val
    val = fn()
    _mcache[key] = (val, time.time())
    return val


# ===================== /api/market =====================

def _fetch_market():
    import akshare as ak
    dp = {}

    # 上证
    sh = ak.stock_zh_index_daily(symbol="sh000001")
    if not sh.empty:
        r = sh.iloc[-1]; dp["sh"] = {"price": round(float(r["close"]), 2), "pct": round(float(r.get("pct_chg", 0)), 2)}
        sh5 = sh.tail(5)
        dp["sh"]["trend5"] = round((float(sh5.iloc[-1]["close"]) - float(sh5.iloc[0]["close"])) / float(sh5.iloc[0]["close"]) * 100, 2)

    # 深证
    sz = ak.stock_zh_index_daily(symbol="sz399001")
    if not sz.empty:
        r = sz.iloc[-1]; dp["sz"] = {"price": round(float(r["close"]), 2), "pct": round(float(r.get("pct_chg", 0)), 2)}

    # 涨停情绪
    today = _today()
    zt = ak.stock_zt_pool_em(date=today)
    br = ak.stock_zt_pool_zbgc_em(date=today)
    prev = ak.stock_zt_pool_previous_em(date=today)

    total_zt = len(zt) if not zt.empty else 0
    total_br = len(br) if not br.empty else 0
    dp["total_zt"] = total_zt
    dp["break_rate"] = round(total_br / (total_zt + total_br) * 100, 1) if (total_zt + total_br) > 0 else 0

    prev_prem = 0
    if not prev.empty:
        for c in ["今日涨幅", "涨跌幅"]:
            if c in prev.columns:
                prev_prem = round(float(prev[c].mean()), 2); break
    dp["prev_premium"] = prev_prem

    # 市场广度
    try:
        spot = ak.stock_zh_a_spot_em()
        if not spot.empty:
            spot["涨跌幅"] = pd.to_numeric(spot["涨跌幅"], errors="coerce")
            dp["up_count"] = int((spot["涨跌幅"] > 0).sum())
            dp["down_count"] = int((spot["涨跌幅"] < 0).sum())
    except: pass

    # 市场状态判断
    sh_trend = dp.get("sh", {}).get("trend5", 0)
    if sh_trend > 1 and total_zt >= 80 and dp["break_rate"] < 25 and prev_prem > 0:
        dp["state"] = "进攻"; dp["state_color"] = "green"
    elif sh_trend < -2 or total_zt < 40 or dp["break_rate"] > 35 or prev_prem < -1:
        dp["state"] = "空仓"; dp["state_color"] = "red"
    else:
        dp["state"] = "防守"; dp["state_color"] = "yellow"

    dp["time"] = _now()
    return dp


@app.get("/api/market")
async def api_market():
    try:
        import pandas as pd
        return _cached("market", _fetch_market)
    except Exception as e:
        return {"error": str(e), "time": _now()}


# ===================== /api/pool =====================

def _fetch_pool_yijiner():
    from signals.proven_scorer import scan as scan_zt
    picks = scan_zt(_today(), top_n=50)
    rows = []
    for p in picks:
        row = {"code": p.code, "name": p.name, "price": p.price, "pct": p.expected_return,
               "seal": p.seal_time, "open_count": p.open_count, "turnover": p.turnover,
               "board": p.board_height, "score": p.score,
               "buy_price": p.buy_price, "sell_price": p.sell_price}
        # 补资金流
        try:
            ff = _fetch_fund_flow(p.code)
            row["main_inflow"] = ff.get("main_net", 0)
        except: row["main_inflow"] = 0
        rows.append(row)

    # 多信号融合：检查双峰池是否有重合
    try:
        dp_codes = {r["code"] for r in _fetch_pool_double_peak().get("rows", [])}
        for row in rows:
            if row["code"] in dp_codes:
                row["score"] = min(100, row["score"] + 8)
                row["fusion"] = True
    except: pass

    return {"strategy": "一进二", "count": len(rows), "rows": rows, "time": _now()}


def _fetch_pool_double_peak():
    from signals.double_peak import scan as scan_dp
    picks = scan_dp(capital=3000.0)
    rows = []
    for p in picks:
        row = {"code": p.code, "name": p.name, "price": p.price, "score": p.score,
               "buy_price": p.buy_price, "target_price": p.target_price,
               "stop_price": p.stop_price, "low_1": p.low_1, "low_2": p.low_2,
               "ma60": p.ma60, "volume_shrink": p.volume_shrink,
               "days_between": p.days_between_lows, "reason": p.reason}
        try:
            ff = _fetch_fund_flow(p.code)
            row["main_inflow"] = ff.get("main_net", 0)
        except: row["main_inflow"] = 0
        rows.append(row)

    # 多信号融合
    try:
        yj_codes = {r["code"] for r in _fetch_pool_yijiner().get("rows", [])}
        for row in rows:
            if row["code"] in yj_codes:
                row["score"] = min(100, row["score"] + 8)
                row["fusion"] = True
    except: pass

    return {"strategy": "双峰抄底", "count": len(rows), "rows": rows, "time": _now()}


def _fetch_pool_all():
    import akshare as ak
    from data.preprocessor import clean_seal_time, parse_board_height
    zt = ak.stock_zt_pool_em(date=_today())
    if zt.empty: return {"strategy": "全量涨停池", "count": 0, "rows": [], "time": _now()}
    zt = zt.copy()
    zt["代码"] = zt["代码"].astype(str).str.zfill(6)
    zt = zt[~zt["代码"].str.startswith(("300", "301", "688", "689", "920"))]
    zt = zt[~zt["名称"].str.contains("ST|退", na=False)]
    rows = []
    for _, r in zt.iterrows():
        rows.append({"code": str(r["代码"]).zfill(6), "name": str(r["名称"]),
                     "price": float(r.get("最新价", 0)), "pct": float(r.get("涨跌幅", 0)),
                     "seal": str(r.get("首次封板时间", "")), "turnover": float(r.get("换手率", 0)),
                     "board": parse_board_height(r.get("涨停统计", "")),
                     "open_count": int(r.get("炸板次数", 0)),
                     "amount": float(r.get("成交额", 0)) / 1e8})
    return {"strategy": "全量涨停池", "count": len(rows), "rows": rows, "time": _now()}


@app.get("/api/pool")
async def api_pool(strategy: str = "yijiner"):
    try:
        if strategy == "yijiner": return _cached("pool_yijiner", _fetch_pool_yijiner)
        elif strategy == "double_peak": return _cached("pool_dp", _fetch_pool_double_peak)
        elif strategy == "all": return _cached("pool_all", _fetch_pool_all)
        return {"error": "unknown strategy"}
    except Exception as e:
        return {"error": str(e), "time": _now()}


# ===================== /api/chart/{code} =====================

def _fetch_fund_flow(code):
    """个股主力资金"""
    try:
        import akshare as ak
        mkt = "sh" if str(code).zfill(6).startswith("6") else "sz"
        df = ak.stock_individual_fund_flow(stock=str(code).zfill(6), market=mkt)
        if df.empty: return {}
        r = df.iloc[0]
        return {"date": str(r.get("日期", "")), "main_net": float(r.get("主力净流入", r.get("主力净流入-净额", 0))),
                "super_large": float(r.get("超大单净流入", r.get("超大单净流入-净额", 0))),
                "large": float(r.get("大单净流入", r.get("大单净流入-净额", 0)))}
    except: return {}


@app.get("/api/chart/{code}")
async def api_chart(code: str, days: int = 60):
    try:
        import akshare as ak
        import numpy as np
        code = str(code).zfill(6)
        prefix = "sh" if code.startswith("6") else "sz"
        start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
        end = _today()
        kline = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", start_date=start, end=end, adjust="qfq")
        if kline.empty: return {"error": "无数据", "code": code}

        if len(kline) > days + 5:
            kline = kline.iloc[-(days + 5):]

        close = kline["收盘"].values.astype(float)
        high = kline["最高"].values.astype(float)
        low = kline["最低"].values.astype(float)
        opens = kline["开盘"].values.astype(float)
        volume = kline["成交量"].values

        dates = kline["日期"].astype(str).tolist()
        ohlc = [[round(float(o), 2), round(float(c), 2), round(float(l), 2), round(float(h), 2)]
                for o, c, l, h in zip(opens, close, low, high)]

        ma5 = _ma(close, 5); ma10 = _ma(close, 10)
        ma20 = _ma(close, 20); ma60 = _ma(close, 60)
        vols = [int(v) for v in volume]

        # 区间统计
        n_actual = min(days, len(close))
        seg_close = close[-n_actual:] if n_actual else close
        seg_high = high[-n_actual:] if n_actual else high
        seg_low = low[-n_actual:] if n_actual else low
        stats = {"period_high": round(float(max(seg_high)), 2),
                 "period_low": round(float(min(seg_low)), 2),
                 "period_avg": round(float(np.mean(seg_close)), 2),
                 "days": n_actual}

        return {"code": code, "days": days, "dates": dates, "ohlc": ohlc,
                "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
                "volumes": vols, "stats": stats,
                "latest_price": round(float(close[-1]), 2)}
    except Exception as e:
        return {"error": str(e), "code": code}


def _ma(arr, n):
    import numpy as np
    res = []
    for i in range(len(arr)):
        if i < n - 1: res.append(None)
        else: res.append(round(float(np.mean(arr[i-n+1:i+1])), 2))
    return res


# ===================== /api/stock/{code} =====================

@app.get("/api/stock/{code}")
async def api_stock(code: str):
    try:
        import akshare as ak
        import numpy as np
        code = str(code).zfill(6)
        prefix = "sh" if code.startswith("6") else "sz"
        start = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        kline = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", start_date=start, end_date=_today(), adjust="qfq")
        if kline.empty: return {"error": "无数据"}

        close = kline["收盘"].values.astype(float)
        high_arr = kline["最高"].values.astype(float)
        low_arr = kline["最低"].values.astype(float)
        latest = float(close[-1])
        ma5v = round(float(np.mean(close[-5:])), 2) if len(close) >= 5 else latest
        ma20v = round(float(np.mean(close[-20:])), 2) if len(close) >= 20 else latest
        ma60v = round(float(np.mean(close[-60:])), 2) if len(close) >= 60 else latest
        high_60 = round(float(high_arr[-60:].max()), 2); low_60 = round(float(low_arr[-60:].min()), 2)
        pos = round((latest - low_60) / (high_60 - low_60) * 100, 1) if high_60 != low_60 else 50

        trend = "上升" if ma5v > ma20v > ma60v else ("下跌" if ma5v < ma20v < ma60v else "震荡")

        from signals.double_peak import find_double_bottom
        db = find_double_bottom(kline)

        # 资金流
        fund = _fetch_fund_flow(code)

        # 策略匹配
        strategies = []
        if 3 <= latest <= 25:
            strategies.append("一进二")
        if db:
            strategies.append("双峰抄底")
        if not strategies:
            if latest < ma60v and pos < 30: strategies.append("超跌反弹")
            else: strategies.append("暂无匹配策略")

        # ATR动态止损
        from signals.risk_utils import calc_atr, dynamic_stop_loss, dynamic_take_profit, score_to_position, fuse_signals
        atr_val = calc_atr(high_arr, low_arr, close)

        # 建议
        if trend == "上升":
            buy_p = round(latest * 0.99, 2); sell_p = round(latest * 1.05, 2)
            action = "趋势向上，可现价买入"
        elif trend == "下跌":
            buy_p = round(low_60, 2); sell_p = round(ma20v, 2)
            action = "趋势向下，等待企稳"
        elif db:
            buy_p = round(latest, 2); sell_p = round(ma60v, 2)
            action = "双底确认，可现价买入博反弹"
        else:
            buy_p = round(ma20v, 2) if ma20v < latest else round(latest, 2)
            sell_p = round(ma60v if latest < ma60v else latest * 1.03, 2)
            action = "震荡区间，低吸高抛"

        stop_loss = dynamic_stop_loss(buy_p, atr_val)
        sell_p = dynamic_take_profit(buy_p, atr_val, strategy_target=sell_p)

        # 仓位计算
        est_score = 75 if trend == "上升" else (65 if db else 55)
        position_amount, position_pct = score_to_position(est_score, 3000)

        return {"code": code, "latest_price": latest, "ma5": ma5v, "ma20": ma20v, "ma60": ma60v,
                "high_60": high_60, "low_60": low_60, "position_60": pos, "trend": trend,
                "atr": atr_val,
                "fund_flow": fund,
                "double_bottom": {"found": db is not None, "low_1": db["low_1"] if db else None,
                                  "low_2": db["low_2"] if db else None,
                                  "days_between": db["days_between"] if db else None,
                                  "volume_shrink": round(db["volume_shrink"] * 100, 1) if db else None},
                "strategies": strategies,
                "position": {"amount": int(position_amount), "pct": int(position_pct * 100)},
                "advice": {"action": action, "buy_price": buy_p, "sell_price": sell_p, "stop_price": stop_loss}}
    except Exception as e:
        return {"error": str(e)}


# ===================== 自选股 =====================

@app.get("/api/watchlist")
async def api_wl():
    return _rjson(WATCHLIST_FILE, {"stocks": []})

@app.post("/api/watchlist/add")
async def api_wl_add(code: str = "", name: str = ""):
    data = _rjson(WATCHLIST_FILE, {"stocks": []})
    c = str(code).zfill(6)
    if not any(s["code"] == c for s in data["stocks"]):
        data["stocks"].append({"code": c, "name": name, "added": _today()})
        _wjson(WATCHLIST_FILE, data)
    return {"status": "ok", "count": len(data["stocks"])}

@app.post("/api/watchlist/remove")
async def api_wl_remove(code: str = ""):
    data = _rjson(WATCHLIST_FILE, {"stocks": []})
    c = str(code).zfill(6)
    data["stocks"] = [s for s in data["stocks"] if s["code"] != c]
    _wjson(WATCHLIST_FILE, data)
    return {"status": "ok", "count": len(data["stocks"])}


# ===================== 分析 =====================

@app.post("/api/analyze/watchlist")
async def api_analyze_wl():
    wl = _rjson(WATCHLIST_FILE, {"stocks": []})
    results = []
    for s in wl.get("stocks", []):
        try:
            import urllib.request
            resp = requests.get(f"http://localhost:{os.environ.get('PORT', 8899)}/api/stock/{s['code']}", timeout=30)
            results.append(resp.json())
        except: results.append({"code": s["code"], "error": "分析失败"})
    return {"count": len(results), "results": results, "time": _now()}


@app.post("/api/analyze/holdings")
async def api_analyze_holdings():
    data = _rjson(HOLDINGS_FILE, {"positions": []})
    results = []
    for p in data.get("positions", []):
        try:
            resp = requests.get(f"http://localhost:{os.environ.get('PORT', 8899)}/api/stock/{p['code']}", timeout=30)
            analysis = resp.json()
            analysis["holding"] = p
            if "advice" in analysis:
                price = analysis["latest_price"]
                bp = p["buy_price"]
                pnl_pct = (price - bp) / bp * 100
                if pnl_pct >= 3: analysis["advice"]["action"] = "建议止盈"
                elif pnl_pct <= -5: analysis["advice"]["action"] = "建议止损"
                else: analysis["advice"]["action"] = "继续持有"
            results.append(analysis)
        except: results.append({"code": p["code"], "error": "分析失败"})
    return {"count": len(results), "results": results, "time": _now()}


# ===================== 持仓管理 =====================

class HoldingAdd(BaseModel):
    code: str = ""; name: str = ""; price: float = 0; quantity: int = 0

@app.get("/api/holdings")
async def api_holdings():
    data = _rjson(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
    if data.get("positions"):
        codes = [p["code"] for p in data["positions"]]
        try:
            sina = ",".join(_scode(c) for c in codes)
            resp = requests.get(f"http://hq.sinajs.cn/list={sina}", headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
            resp.encoding = "gbk"
            quotes = {}
            for line in resp.text.strip().split("\n"):
                if "=" not in line: continue
                key = line.split("=")[0].split("_")[-1]
                d = line.split('"')[1].split(",") if '"' in line else []
                if len(d) >= 4: quotes[key] = float(d[3])
            total_v = 0
            for p in data["positions"]:
                cur = quotes.get(p["code"], p["buy_price"])
                p["current_price"] = cur; p["pnl"] = round((cur - p["buy_price"]) * p["quantity"], 2)
                p["pnl_pct"] = round((cur - p["buy_price"]) / p["buy_price"] * 100, 2)
                total_v += cur * p["quantity"]
            data["total_value"] = round(total_v, 2)
            data["total_return"] = round((total_v + data["cash"] - data["initial"]) / data["initial"] * 100, 2)
        except: pass
    return data

@app.post("/api/holdings/add")
async def api_holdings_add(body: HoldingAdd):
    data = _rjson(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
    if data.get("initial") == 0: data["initial"] = 3000
    data["positions"].append({"code": body.code.zfill(6), "name": body.name, "buy_price": body.price,
                              "quantity": body.quantity, "buy_date": _today(),
                              "cost": round(body.price * body.quantity, 2)})
    invested = sum(p["cost"] for p in data["positions"])
    data["cash"] = round(data["initial"] - invested, 2)
    _wjson(HOLDINGS_FILE, data)
    trades = _rjson(TRADES_FILE, [])
    trades.append({"type": "buy", "code": body.code, "price": body.price, "quantity": body.quantity,
                   "date": _today(), "time": _now()})
    _wjson(TRADES_FILE, trades)
    return {"status": "ok"}

@app.post("/api/holdings/sell")
async def api_holdings_sell(code: str = ""):
    data = _rjson(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
    sold, remaining = None, []
    for p in data["positions"]:
        if p["code"] == code.zfill(6): sold = p
        else: remaining.append(p)
    if not sold: return {"status": "not_found"}
    sell_price = sold["buy_price"]
    try:
        resp = requests.get(f"http://hq.sinajs.cn/list={_scode(code)}",
                            headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        resp.encoding = "gbk"
        d = resp.text.split('"')[1].split(",") if '"' in resp.text else []
        if len(d) >= 4: sell_price = float(d[3])
    except: pass
    pnl = round((sell_price - sold["buy_price"]) * sold["quantity"], 2)
    data["positions"] = remaining
    invested = sum(p["cost"] for p in remaining)
    data["cash"] = round(data["initial"] - invested + sell_price * sold["quantity"], 2)
    _wjson(HOLDINGS_FILE, data)
    trades = _rjson(TRADES_FILE, [])
    trades.append({"type": "sell", "code": code, "price": round(sell_price, 2), "quantity": sold["quantity"],
                   "buy_price": sold["buy_price"], "pnl": pnl,
                   "pnl_pct": round((sell_price - sold["buy_price"]) / sold["buy_price"] * 100, 2),
                   "date": _today(), "time": _now()})
    _wjson(TRADES_FILE, trades)
    return {"status": "ok", "pnl": pnl}

@app.get("/api/trades")
async def api_trades(): return _rjson(TRADES_FILE, [])

@app.get("/api/stats")
async def api_stats():
    trades = _rjson(TRADES_FILE, [])
    sells = [t for t in trades if t.get("type") == "sell"]
    if not sells: return {"total_trades": 0}
    wins = [s for s in sells if s["pnl"] > 0]
    return {"total_trades": len(sells), "wins": len(wins),
            "win_rate": round(len(wins) / len(sells) * 100, 1),
            "total_pnl": round(sum(s["pnl"] for s in sells), 2),
            "avg_win": round(sum(s["pnl"] for s in wins) / len(wins), 2) if wins else 0}


# ===================== Webhook =====================

@app.post("/api/webhook/test")
async def api_webhook_test():
    url = _read_webhook_url()
    if not url: return {"status": "no_url", "msg": "未配置webhook URL"}
    try:
        resp = requests.post(url, json={"msgtype": "text", "text": {"content": "🔔 一进二系统 · 测试推送 " + _now()}}, timeout=10)
        return {"status": "ok", "response": resp.status_code}
    except Exception as e: return {"status": "error", "msg": str(e)}

@app.post("/api/webhook/url")
async def api_webhook_set_url(url: str = ""):
    WEBHOOK_URL_FILE.write_text(url, encoding="utf-8")
    return {"status": "ok"}

def _read_webhook_url():
    if WEBHOOK_URL_FILE.exists(): return WEBHOOK_URL_FILE.read_text(encoding="utf-8").strip()
    return ""


# ===================== 预警 =====================

@app.get("/api/alerts")
async def api_alerts():
    alerts = []
    data = _rjson(HOLDINGS_FILE, {"positions": []})
    if not data.get("positions"): return alerts
    codes = [p["code"] for p in data["positions"]]
    try:
        sina = ",".join(_scode(c) for c in codes)
        resp = requests.get(f"http://hq.sinajs.cn/list={sina}", headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        resp.encoding = "gbk"
        quotes = {}
        for line in resp.text.strip().split("\n"):
            if "=" not in line: continue
            key = line.split("=")[0].split("_")[-1]
            d = line.split('"')[1].split(",") if '"' in line else []
            if len(d) >= 4: quotes[key] = float(d[3])
        for p in data["positions"]:
            cur = quotes.get(p["code"], p["buy_price"])
            pnl_pct = (cur - p["buy_price"]) / p["buy_price"] * 100
            if pnl_pct >= 3: alerts.append({"code": p["code"], "name": p["name"], "type": "止盈", "pnl_pct": round(pnl_pct, 1), "price": cur})
            elif pnl_pct <= -5: alerts.append({"code": p["code"], "name": p["name"], "type": "止损", "pnl_pct": round(pnl_pct, 1), "price": cur})
    except: pass
    return alerts


# ===================== 定时推送任务（延迟启动） =====================

def _scheduled_push():
    """后台定时推送 - 启动后等5分钟再开始"""
    time.sleep(300)  # 先等5分钟让服务器完全就绪
    while True:
        time.sleep(1800)
        url = _read_webhook_url()
        if not url: continue
        try:
            wl = _rjson(WATCHLIST_FILE, {"stocks": []})
            if not wl.get("stocks"): continue
            lines = ["📊 定时分析 " + _now()]
            for s in wl["stocks"][:5]:
                try:
                    resp = requests.get(f"http://localhost:{os.environ.get('PORT', 8899)}/api/stock/{s['code']}", timeout=30)
                    a = resp.json()
                    adv = a.get("advice", {})
                    lines.append(f"{s['code']} {a.get('latest_price', '?')} {a.get('trend', '?')} → {adv.get('action', '?')}")
                except: pass
            requests.post(url, json={"msgtype": "text", "text": {"content": "\n".join(lines)}}, timeout=10)
        except: pass

_scheduled_started = False


# ===================== 主页 =====================

@app.get("/")
async def root(): return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/health")
async def health(): return {"status": "ok", "time": datetime.now().isoformat()}

@app.on_event("startup")
async def startup():
    global _scheduled_started
    if not _scheduled_started:
        _scheduled_started = True
        threading.Thread(target=_scheduled_push, daemon=True).start()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8899)))
