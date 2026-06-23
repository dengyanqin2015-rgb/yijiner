"""一进二 v7 — 后台持续更新数据，网页直接读缓存"""
import json, os, sys, threading, time, requests
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "static"
DATA_DIR = Path("/tmp/yijiner") if os.name != "nt" else Path("data/persist")
DATA_DIR.mkdir(parents=True, exist_ok=True)
HOLDINGS_FILE = DATA_DIR / "holdings.json"
TRADES_FILE = DATA_DIR / "trades.json"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"

# ============ 内存缓存（后台线程更新） ============
CACHE = {"market": {}, "yijiner": [], "double_peak": [], "time": ""}
CACHE_LOCK = threading.Lock()


def _scode(c):
    c = str(c).zfill(6)
    return f"sh{c}" if c.startswith("6") else f"sz{c}"


def _update_cache():
    """后台线程：一进二快刷新、双峰慢刷新"""
    dp_counter = 0
    while True:
        try:
            import akshare as ak
            today = datetime.now().strftime("%Y%m%d")

            # --- 市场数据 ---
            zt = ak.stock_zt_pool_em(date=today)
            br = ak.stock_zt_pool_zbgc_em(date=today)
            prev = ak.stock_zt_pool_previous_em(date=today)

            total_zt = len(zt) if not zt.empty else 0
            total_br = len(br) if not br.empty else 0
            break_rate = round(total_br / (total_zt + total_br) * 100, 1) if (total_zt + total_br) > 0 else 0
            prev_premium = 0
            if not prev.empty:
                for c in ["今日涨幅", "涨跌幅"]:
                    if c in prev.columns:
                        prev_premium = round(float(prev[c].mean()), 2); break

            # --- 一进二扫描（每次跑） ---
            from signals.proven_scorer import scan as scan_zt
            zt_picks = scan_zt(today, top_n=30)

            # --- 双峰扫描（每20次=5分钟跑一次） ---
            dp_picks = CACHE.get("double_peak", [])  # 保持上次结果
            if dp_counter % 20 == 0:
                from signals.double_peak import scan as scan_dp
                dp_picks = scan_dp(3000.0)
            dp_counter += 1

            # --- 批量新浪实时价（一进二+双峰所有股票） ---
            all_codes = []
            for p in zt_picks:
                all_codes.append(p.code)
            for p in dp_picks:
                if p.code not in all_codes:
                    all_codes.append(p.code)

            quotes = {}
            if all_codes:
                for i in range(0, len(all_codes), 50):
                    batch = all_codes[i:i + 50]
                    try:
                        sina = ",".join(_scode(c) for c in batch)
                        resp = requests.get(f"http://hq.sinajs.cn/list={sina}",
                                            headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
                        resp.encoding = "gbk"
                        for line in resp.text.strip().split("\n"):
                            if "=" not in line: continue
                            key = line.split("=")[0].split("_")[-1]
                            d = line.split('"')[1].split(",") if '"' in line else []
                            if len(d) >= 4:
                                quotes[key] = {
                                    "name": d[0], "price": float(d[3]),
                                    "open": float(d[1]), "close_yest": float(d[2]),
                                    "high": float(d[4]), "low": float(d[5]),
                                    "pct": round((float(d[3]) - float(d[2])) / float(d[2]) * 100, 2),
                                }
                        time.sleep(0.3)
                    except:
                        pass

            # --- 写入缓存 ---
            yj_rows = []
            for p in zt_picks:
                q = quotes.get(p.code, {})
                yj_rows.append({
                    "code": p.code, "name": p.name or q.get("name", ""),
                    "score": p.score, "price": q.get("price", p.price),
                    "pct": q.get("pct", 0), "seal": p.seal_time,
                    "turnover": p.turnover, "board": p.board_height,
                    "open_count": p.open_count,
                    "buy_price": p.buy_price, "sell_price": p.sell_price,
                })

            dp_rows = []
            for p in dp_picks:
                q = quotes.get(p.code, {})
                dp_rows.append({
                    "code": p.code, "name": p.name or q.get("name", ""),
                    "score": p.score, "price": q.get("price", p.price),
                    "buy_price": p.buy_price, "target_price": p.target_price,
                    "stop_price": p.stop_price,
                    "low_1": p.low_1, "low_2": p.low_2,
                    "ma60": p.ma60, "reason": p.reason,
                })

            with CACHE_LOCK:
                CACHE["market"] = {"total_zt": total_zt, "break_rate": break_rate,
                                   "prev_premium": prev_premium, "time": datetime.now().strftime("%H:%M:%S")}
                CACHE["yijiner"] = yj_rows
                CACHE["double_peak"] = dp_rows
                CACHE["time"] = datetime.now().strftime("%H:%M:%S")

        except Exception as e:
            print(f"Cache update error: {e}")

        time.sleep(15)  # 一进二每15秒刷新，双峰维持上次结果


# 启动后台更新线程
threading.Thread(target=_update_cache, daemon=True).start()


# ============ API：只读缓存，0延迟 ============

@app.get("/api/market")
async def api_market():
    with CACHE_LOCK:
        return dict(CACHE["market"])


@app.get("/api/pool")
async def api_pool(strategy: str = "yijiner"):
    with CACHE_LOCK:
        rows = CACHE["yijiner"] if strategy == "yijiner" else CACHE["double_peak"]
        return {"strategy": strategy, "count": len(rows), "rows": rows, "time": CACHE["time"]}


@app.get("/api/chart/{code}")
async def api_chart(code: str, days: int = 60):
    """K线数据 - 按需加载（新浪历史）"""
    try:
        import akshare as ak
        import numpy as np
        code = str(code).zfill(6)
        prefix = "sh" if code.startswith("6") else "sz"
        start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")

        raw = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", start_date=start, end_date=end, adjust="qfq")
        if raw.empty: return {"error": "无数据"}
        kline = raw.rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                                     "high": "最高", "low": "最低", "volume": "成交量"})

        if len(kline) > days + 5: kline = kline.iloc[-(days + 5):]

        close = kline["收盘"].values.astype(float)
        high = kline["最高"].values.astype(float)
        low = kline["最低"].values.astype(float)
        opens = kline["开盘"].values.astype(float)

        dates = kline["日期"].astype(str).tolist()
        ohlc = [[round(float(o), 2), round(float(c), 2), round(float(l), 2), round(float(h), 2)]
                for o, c, l, h in zip(opens, close, low, high)]
        vols = [int(v) for v in kline["成交量"].values]

        # 均线
        def ma(arr, n):
            r = []
            for i in range(len(arr)):
                r.append(round(float(np.mean(arr[i - n + 1:i + 1])), 2) if i >= n - 1 else None)
            return r

        # 区间统计
        n = min(days, len(close))
        seg_close, seg_high, seg_low = close[-n:], high[-n:], low[-n:]

        return {"code": code, "days": days, "dates": dates, "ohlc": ohlc,
                "ma5": ma(close, 5), "ma10": ma(close, 10), "ma20": ma(close, 20), "ma60": ma(close, 60),
                "volumes": vols, "latest_price": round(float(close[-1]), 2),
                "stats": {"period_high": round(float(max(seg_high)), 2),
                          "period_low": round(float(min(seg_low)), 2),
                          "period_avg": round(float(np.mean(seg_close)), 2), "days": n}}
    except Exception as e:
        return {"error": str(e)}


# ============ 静态页 ============
@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
async def health():
    with CACHE_LOCK:
        return {"status": "ok", "cache_time": CACHE["time"]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8899)))
