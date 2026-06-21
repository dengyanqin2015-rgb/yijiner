"""一进二 · 决策系统后端 + AI策略引擎"""
import json, os, sys, requests
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="一进二AI决策系统")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_DIR = Path("/tmp/yijiner_data") if os.name != "nt" else Path("data/persist")
DATA_DIR.mkdir(parents=True, exist_ok=True)
HOLDINGS_FILE = DATA_DIR / "holdings.json"
TRADES_FILE = DATA_DIR / "trades.json"
CACHE_FILE = DATA_DIR / "scan_cache.json"
AI_MEMORY_FILE = DATA_DIR / "ai_memory.json"

# 静态文件
STATIC_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "static"
STATIC_DIR.mkdir(exist_ok=True)


# ============================================================
# 工具函数
# ============================================================
def _read_json(path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            return json.load(open(path, "r", encoding="utf-8"))
        except:
            return default
    return default


def _write_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _sina_code(stock_code: str) -> str:
    """002809 → sz002809, 600067 → sh600067"""
    code = str(stock_code).zfill(6)
    return f"sh{code}" if code.startswith("6") else f"sz{code}"


def _fetch_quotes(codes: list[str]) -> dict:
    """批量获取实时行情（新浪源，约3秒延迟）"""
    if not codes:
        return {}
    sina_list = ",".join(_sina_code(c) for c in codes)
    try:
        resp = requests.get(f"http://hq.sinajs.cn/list={sina_list}",
                            headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
        resp.encoding = "gbk"
        results = {}
        for line in resp.text.strip().split("\n"):
            if "=" not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            data = line.split('"')[1].split(",") if '"' in line else []
            if len(data) < 32:
                continue
            results[key] = {
                "name": data[0], "open": float(data[1]), "close_yest": float(data[2]),
                "price": float(data[3]), "high": float(data[4]), "low": float(data[5]),
                "change_pct": round((float(data[3]) - float(data[2])) / float(data[2]) * 100, 2),
                "volume": int(data[8]) if data[8] else 0,
                "amount": float(data[9]) / 1e8 if data[9] else 0,
            }
        return results
    except Exception as e:
        return {"_error": str(e)}


# ============================================================
# API: 实时行情
# ============================================================
@app.get("/api/quote")
async def api_quote(codes: str = ""):
    if not codes:
        return {"quotes": {}}
    return {"quotes": _fetch_quotes(codes.split(",")), "time": datetime.now().strftime("%H:%M:%S")}


# ============================================================
# API: 策略扫描（打板 + 尾盘确认）
# ============================================================
@app.get("/api/scan")
async def api_scan():
    try:
        from signals.proven_scorer import scan as scan_zt
        from data.fetcher import fetch_daily_zt_pool, fetch_breakout_pool, fetch_previous_zt_performance
        from data.preprocessor import parse_board_height, clean_seal_time

        now = datetime.now()
        today = now.strftime("%Y%m%d")
        hour = now.hour
        is_market_open = 9 <= hour <= 15 and now.weekday() < 5

        result = {"date": today, "time": now.strftime("%H:%M:%S"), "market_open": is_market_open}

        # 市场情绪
        zt_pool = fetch_daily_zt_pool(today)
        breakout = fetch_breakout_pool(today)
        total_zt = len(zt_pool) if not zt_pool.empty else 0
        total_br = len(breakout) if not breakout.empty else 0
        br_rate = round(total_br / (total_zt + total_br) * 100, 1) if (total_zt + total_br) > 0 else 0

        prev_perf = fetch_previous_zt_performance(today)
        prev_premium = 0.0
        if not prev_perf.empty:
            for col in ["今日涨幅", "涨跌幅"]:
                if col in prev_perf.columns:
                    prev_premium = float(prev_perf[col].mean())
                    break

        result["market"] = {
            "total_zt": total_zt, "break_rate": br_rate,
            "prev_premium": round(prev_premium, 2),
            "sentiment": "暖" if total_zt >= 80 else ("温" if total_zt >= 40 else "冷"),
            "warning": "炸板率高" if br_rate > 40 else ("打板环境恶劣" if prev_premium < -1 else ""),
        }

        # 打板精选
        try:
            zt_picks = scan_zt(today, top_n=10)
            result["zt_picks"] = [
                {"code": p.code, "name": p.name, "score": p.score,
                 "expected": p.expected_return, "price": p.price,
                 "buy_price": p.buy_price, "sell_price": p.sell_price,
                 "board": p.board_height, "seal": p.seal_time,
                 "turnover": p.turnover} for p in zt_picks
            ]
        except:
            result["zt_picks"] = []

        # 尾盘精选（14:30后或非交易时间用缓存）
        if hour >= 14 or not is_market_open:
            try:
                from signals.tail_market import scan as scan_tail
                tail_picks = scan_tail(capital=3000.0)
                result["tail_picks"] = [
                    {"code": t.code, "name": t.name, "score": t.score,
                     "pct": t.pct, "price": t.price,
                     "buy_price": t.buy_price, "sell_price": t.sell_price,
                     "stop_price": t.stop_price, "reason": t.reason} for t in tail_picks
                ]
            except:
                result["tail_picks"] = []
        else:
            result["tail_picks"] = []

        _write_json(CACHE_FILE, result)
        return result
    except Exception as e:
        # 返回缓存
        cached = _read_json(CACHE_FILE)
        if cached:
            cached["cached"] = True
            return cached
        return {"error": str(e), "time": datetime.now().strftime("%H:%M:%S")}


# ============================================================
# API: 持仓管理
# ============================================================
@app.get("/api/holdings")
async def api_holdings():
    data = _read_json(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
    # 补充实时行情
    if data.get("positions"):
        codes = [p["code"] for p in data["positions"]]
        quotes = _fetch_quotes(codes)
        total_value = 0
        for p in data["positions"]:
            q = quotes.get(p["code"], {})
            cur_price = q.get("price", p["buy_price"])
            p["current_price"] = cur_price
            p["pnl"] = round((cur_price - p["buy_price"]) * p["quantity"], 2)
            p["pnl_pct"] = round((cur_price - p["buy_price"]) / p["buy_price"] * 100, 2)
            total_value += cur_price * p["quantity"]
        data["total_value"] = total_value
        data["total_return"] = round((total_value + data["cash"] - data["initial"]) / data["initial"] * 100, 2)
    return data


class HoldingAdd(BaseModel):
    code: str = ""
    name: str = ""
    price: float = 0
    quantity: int = 0


@app.post("/api/holdings/add")
async def api_add_holding(body: HoldingAdd):
    data = _read_json(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
    if data.get("initial") == 0:
        data["initial"] = 3000
    cost = body.price * body.quantity
    data["positions"].append({
        "code": str(body.code).zfill(6), "name": body.name, "buy_price": body.price,
        "quantity": body.quantity, "buy_date": datetime.now().strftime("%Y%m%d"),
        "cost": round(cost, 2),
    })
    total_invested = sum(p["cost"] for p in data["positions"])
    data["cash"] = round(data["initial"] - total_invested, 2)
    _write_json(HOLDINGS_FILE, data)

    # 交易日志
    trades = _read_json(TRADES_FILE, [])
    trades.append({"type": "buy", "code": body.code, "name": body.name, "price": body.price,
                   "quantity": body.quantity, "date": datetime.now().strftime("%Y%m%d"),
                   "time": datetime.now().strftime("%H:%M")})
    _write_json(TRADES_FILE, trades)
    return {"status": "ok"}


@app.post("/api/holdings/sell")
async def api_sell_holding(code: str = ""):
    data = _read_json(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
    if data.get("initial") == 0:
        data["initial"] = 3000
    sold, remaining = None, []
    for p in data["positions"]:
        if p["code"] == str(code).zfill(6):
            sold = p
        else:
            remaining.append(p)
    if not sold:
        return {"status": "not_found"}

    # 用实时价格
    quotes = _fetch_quotes([code])
    sell_price = quotes.get(code, {}).get("price", sold["buy_price"])
    pnl = round((sell_price - sold["buy_price"]) * sold["quantity"], 2)
    pnl_pct = round((sell_price - sold["buy_price"]) / sold["buy_price"] * 100, 2)

    data["positions"] = remaining
    total_invested = sum(p["cost"] for p in remaining)
    data["cash"] = round(data["initial"] - total_invested + sell_price * sold["quantity"], 2)
    _write_json(HOLDINGS_FILE, data)

    trades = _read_json(TRADES_FILE, [])
    trades.append({"type": "sell", "code": code, "name": sold["name"],
                   "price": sell_price, "quantity": sold["quantity"],
                   "buy_price": sold["buy_price"],
                   "date": datetime.now().strftime("%Y%m%d"),
                   "time": datetime.now().strftime("%H:%M"),
                   "pnl": pnl, "pnl_pct": pnl_pct})
    _write_json(TRADES_FILE, trades)
    return {"status": "ok", "pnl": pnl, "pnl_pct": pnl_pct, "sell_price": sell_price}


# ============================================================
# API: 交易记录 + 收益统计
# ============================================================
@app.get("/api/trades")
async def api_trades():
    return _read_json(TRADES_FILE, [])


@app.get("/api/stats")
async def api_stats():
    trades = _read_json(TRADES_FILE, [])
    sells = [t for t in trades if t.get("type") == "sell"]
    if not sells:
        return {"total_trades": 0}
    wins = [s for s in sells if s["pnl"] > 0]
    return {
        "total_trades": len(sells),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(sells) * 100, 1),
        "total_pnl": round(sum(s["pnl"] for s in sells), 2),
        "avg_win": round(sum(s["pnl"] for s in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(s["pnl"] for s in sells if s["pnl"] < 0) / len([s for s in sells if s["pnl"] < 0]), 2) if len([s for s in sells if s["pnl"] < 0]) > 0 else 0,
    }


# ============================================================
# AI 对话引擎
# ============================================================
AI_RULES = {
    "打板策略": "主板 + 非ST + 10:00前封板 + 换手率<8% + 非一字板 → 次日竞价买入 → 次日开盘卖出",
    "尾盘确认": "涨3-7% + 收于最高价2%内 + 成交额>5千万 + 主板非ST + 价格3-30 → 14:50买入 → 次日开盘卖出",
    "风控规则": "单笔止损-5% | 连续亏损3笔停1天 | 日亏损>5%清仓 | 周亏损>10%停一周",
}


@app.get("/api/ai")
async def api_ai_chat(msg: str = ""):
    """AI策略助手"""
    if not msg:
        return {"reply": "你好！我是AI策略助手。可以问我：今天买什么 / 怎么看持仓 / 策略规则 / 分析某只股票"}

    msg_lower = msg.lower()
    memory = _read_json(AI_MEMORY_FILE, {"conversations": []})

    # ---- 买入推荐 ----
    if any(w in msg_lower for w in ["买", "推荐", "选股", "今天买什么", "机会"]):
        try:
            cached = _read_json(CACHE_FILE)
            zt = cached.get("zt_picks", [])
            tail = cached.get("tail_picks", [])
            market = cached.get("market", {})

            lines = [f"📊 市场: {market.get('sentiment','N/A')} | 涨停{market.get('total_zt',0)}只 | 炸板率{market.get('break_rate',0)}%"]

            if market.get("warning"):
                lines.append(f"⚠️ {market['warning']}")

            if zt:
                best = zt[0]
                lots = max(1, int(1500 / (best["price"] * 100)))
                lines.append(f"\n⭐ 打板首选: {best['code']} {best['name']}")
                lines.append(f"   评分{best['score']:.0f} | 预期+{best['expected']:.1f}% | {best['board']}板")
                lines.append(f"   建议{lots}手 × {best['buy_price']}元 | 止盈{best['sell_price']}元")

            if tail:
                best_t = tail[0]
                lines.append(f"\n⭐ 尾盘首选: {best_t['code']} {best_t['name']}")
                lines.append(f"   评分{best_t['score']:.0f} | 涨幅{best_t['pct']}% | {best_t['reason']}")
                lines.append(f"   买入{best_t['buy_price']}元 | 止盈{best_t['sell_price']}元 | 止损{best_t['stop_price']}元")

            if not zt and not tail:
                lines.append("\n暂无推荐，可能原因：非交易日/数据未更新/无优质标的")

            return {"reply": "\n".join(lines)}
        except Exception as e:
            return {"reply": f"扫描出错: {e}"}

    # ---- 持仓查询 ----
    if any(w in msg_lower for w in ["持仓", "股票", "持有"]):
        data = _read_json(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
        if not data.get("positions"):
            return {"reply": "当前无持仓"}
        codes = [p["code"] for p in data["positions"]]
        quotes = _fetch_quotes(codes)
        lines = ["📋 当前持仓:"]
        for p in data["positions"]:
            q = quotes.get(p["code"], {})
            price = q.get("price", p["buy_price"])
            chg = q.get("change_pct", 0)
            pnl = (price - p["buy_price"]) * p["quantity"]
            lines.append(f"  {p['code']} {p['name']} | 成本{p['buy_price']} 现价{price} ({chg:+.1f}%) | 盈亏{pnl:+.0f}元")
        return {"reply": "\n".join(lines)}

    # ---- 分析个股 ----
    for kw in ["分析", "怎么看", "如何"]:
        if kw in msg_lower:
            for code_candidate in msg.split():
                code_candidate = code_candidate.strip().zfill(6)
                if code_candidate.isdigit() and len(code_candidate) == 6:
                    try:
                        from data.fetcher import fetch_daily_kline
                        kline = fetch_daily_kline(code_candidate, (datetime.now() - timedelta(days=60)).strftime("%Y%m%d"), datetime.now().strftime("%Y%m%d"))
                        if kline.empty:
                            return {"reply": f"未找到{code_candidate}的K线数据"}
                        recent = kline.tail(5)
                        lines = [f"📈 {code_candidate} 近5日:"]
                        for _, r in recent.iterrows():
                            chg = (r["收盘"] - r["开盘"]) / r["开盘"] * 100
                            lines.append(f"  {r['日期']} 开{r['开盘']:.2f} 收{r['收盘']:.2f} ({chg:+.1f}%)")
                        return {"reply": "\n".join(lines)}
                    except:
                        pass

    # ---- 策略规则 ----
    if any(w in msg_lower for w in ["策略", "规则", "怎么玩", "怎么操作"]):
        lines = ["📖 当前策略体系:\n"]
        for name, rule in AI_RULES.items():
            lines.append(f"【{name}】\n{rule}\n")
        return {"reply": "\n".join(lines)}

    # ---- 卖/止损 ----
    if any(w in msg_lower for w in ["卖", "止损", "止盈", "出"]):
        data = _read_json(HOLDINGS_FILE, {"positions": [], "cash": 3000, "initial": 3000})
        if not data.get("positions"):
            return {"reply": "当前无持仓"}
        codes = [p["code"] for p in data["positions"]]
        quotes = _fetch_quotes(codes)
        lines = ["📤 卖出建议:"]
        for p in data["positions"]:
            q = quotes.get(p["code"], {})
            price = q.get("price", p["buy_price"])
            pnl_pct = (price - p["buy_price"]) / p["buy_price"] * 100
            if pnl_pct >= 3:
                lines.append(f"  {p['code']} {p['name']}: 盈利{pnl_pct:+.1f}% → ⭐建议止盈")
            elif pnl_pct <= -5:
                lines.append(f"  {p['code']} {p['name']}: 亏损{pnl_pct:+.1f}% → 🔴建议止损")
            else:
                lines.append(f"  {p['code']} {p['name']}: {pnl_pct:+.1f}% → 持有观察")
        return {"reply": "\n".join(lines)}

    # ---- 默认回复 ----
    memory["conversations"].append({"q": msg, "time": datetime.now().isoformat()})
    if len(memory["conversations"]) > 100:
        memory["conversations"] = memory["conversations"][-50:]
    _write_json(AI_MEMORY_FILE, memory)

    return {"reply": f"收到。你可以问我：\n• 今天买什么\n• 怎么看持仓\n• 策略规则\n• 分析XXXXXX（股票代码）\n• 什么时候卖"}


# ============================================================
# 静态文件
# ============================================================
@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8899)))
