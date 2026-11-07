"""StockPro: personal stock research app (FastAPI). Run: python run.py"""
from __future__ import annotations
import json, math, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analysis, data, mf, universe

app = FastAPI(title="StockPro (personal)")
STATIC = Path(__file__).parent / "static"
_store_lock = threading.Lock()
_mem: dict = {}
HORIZONS = ("long", "medium", "short")


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    return o


def ok(payload):
    return JSONResponse(clean(payload))


def cached(key, ttl, fn):
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _mem[key] = (time.time(), val)
    return val


def bench_df():
    return data.get_history(data.BENCH)


def analyze(sym: str) -> dict:
    def run():
        df = data.get_history(sym)
        if df is None or df.empty or len(df) < 30:
            raise HTTPException(404, f"No price data for '{sym}'. Check the ticker (NSE: RELIANCE.NS, BSE: RELIANCE.BO, US: us:AAPL).")
        info = data.get_info(sym)
        etf = universe.lookup_etf(sym)
        a = analysis.analyze_etf(df, info, bench_df(), etf) if etf else analysis.analyze_frame(df, info, bench_df())
        a["symbol"] = sym
        a["name"] = info.get("name") or (etf or {}).get("name") or sym
        a.setdefault("kind", "stock")
        return a
    return cached(f"an:{sym}", 1800, run)


def etf_row(sym: str, a: dict) -> dict:
    i, r, k, t = a["info"], a["returns"], a["risk"], a["tech"]
    b1 = (a.get("bench_returns") or {}).get("1Y")
    return {"symbol": sym, "name": a["name"], "kind": "etf", "sector": i.get("category"), "category": i.get("category"), "index": i.get("index"),
            "role": i.get("role"), "price": a["price"], "change_1d": a["change_1d"], "pe": i.get("pe"), "roe": None, "pb": None,
            "fair_value": None, "upside": None, "score": a["pillars"].get("Momentum"),
            "ret_1m": r.get("1M"), "ret_6m": r.get("6M"), "ret_1y": r.get("1Y"), "cagr_3y": r.get("3Y CAGR"), "cagr_5y": r.get("5Y CAGR"),
            "vs_nifty_1y": (r["1Y"] - b1) if (r.get("1Y") is not None and b1 is not None) else None,
            "volatility": k.get("volatility"), "max_drawdown": k.get("max_drawdown_3y"), "sharpe": k.get("sharpe_3y"),
            "avg_traded_value": k.get("avg_traded_value"), "thin": k.get("thin_liquidity"), "rsi": t.get("rsi"), "trend": t["trend"],
            "signals": {h: {"signal": v["signal"], "score": v["score"]} for h, v in a["signals"].items()}}


def row(sym: str, a: dict) -> dict:
    if a.get("kind") == "etf":
        return etf_row(sym, a)
    s, fv = a["signals"], a["fair_value"]
    return {"symbol": sym, "name": a["name"], "sector": a["info"].get("sector"), "price": a["price"], "change_1d": a["change_1d"],
            "pe": a["info"].get("pe"), "pb": a["info"].get("pb"), "roe": a["info"].get("roe"), "market_cap": a["info"].get("market_cap"),
            "fair_value": fv.get("value"), "upside": fv.get("upside"),
            "score": a["overall"], "ret_6m": a["tech"].get("ret_6m"), "rsi": a["tech"].get("rsi"), "trend": a["tech"]["trend"],
            "signals": {k: {"signal": v["signal"], "score": v["score"]} for k, v in s.items()}}


# ------------------------------------------------------------------ status / search
@app.get("/api/status")
def status():
    return ok({"mode": data.mode(), "reason": data.mode_reason(), "universe_size": len(universe.load_universe()),
               "etf_count": len(universe.load_etfs()), "home": str(data.HOME)})


@app.get("/api/search")
def search(q: str = ""):
    res = universe.search(q)
    if q and not res:
        res = [{"symbol": data.normalize(q), "short": q.upper(), "name": "Try this ticker", "sector": ""}]
    return ok(res)


# ------------------------------------------------------------------ screener
def _record_signals(rows: list[dict]) -> list[dict]:
    """Remember every stock's signals after each screener run and return the changes seen in the last 30 days (newest first).
    This is how you notice a stock flipping HOLD -> BUY (or BUY -> SELL) without re-reading the whole table."""
    name = f"signal_history_{data.mode()}"
    hist = _load(name, {"last": {}, "changes": []})
    today, last, new = time.strftime("%Y-%m-%d"), hist.get("last", {}), []
    for r in rows:
        cur = {h: r["signals"][h]["signal"] for h in HORIZONS}
        prev = last.get(r["symbol"])
        if prev:
            for h in HORIZONS:
                if prev.get(h) and prev[h] != cur[h]:
                    new.append({"date": today, "symbol": r["symbol"], "name": r["name"], "horizon": h, "from": prev[h], "to": cur[h]})
        last[r["symbol"]] = {**cur, "date": today}
    hist["last"], hist["changes"] = last, (new + hist.get("changes", []))[:500]
    _save(name, hist)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    return [c for c in hist["changes"] if c["date"] >= cutoff]


def _symbol_changes(sym: str) -> list[dict]:
    hist = _load(f"signal_history_{data.mode()}", {})
    return [c for c in hist.get("changes", []) if c["symbol"] == sym][:10]


@app.get("/api/screener")
def screener(refresh: bool = False):
    if refresh:   # drop both the in-memory analyses and the on-disk price/fundamentals cache, then refetch
        for k in [k for k in _mem if k.startswith(("an:", "screener", "peers:"))]:
            _mem.pop(k, None)
        data.invalidate([u["symbol"] for u in universe.load_universe()] + [data.BENCH])

    def run():
        syms = [u["symbol"] for u in universe.load_universe()]
        def one(s):
            try:
                return row(s, analyze(s))
            except Exception as e:  # skip tickers that fail
                return {"symbol": s, "error": str(e)}
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(one, syms))
        rows = [r for r in res if "error" not in r]
        return {"rows": rows, "failed": [r["symbol"] for r in res if "error" in r], "changes": _record_signals(rows),
                "mode": data.mode(), "as_of": time.strftime("%Y-%m-%d %H:%M")}
    return ok(cached("screener", 1800, run))


@app.get("/api/etfs")
def etfs(refresh: bool = False):
    """ETF screener: returns, risk, liquidity and trend signals for every ETF in the universe."""
    syms = [e["symbol"] for e in universe.load_etfs()]
    if refresh:
        for k in [k for k in _mem if k.startswith("etfs")] + [f"an:{s}" for s in syms]:
            _mem.pop(k, None)
        data.invalidate(syms + [data.BENCH])

    def run():
        def one(s):
            try:
                return row(s, analyze(s))
            except Exception as e:
                return {"symbol": s, "error": str(e)}
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(one, syms))
        rows = [r for r in res if "error" not in r]
        return {"rows": rows, "failed": [r["symbol"] for r in res if "error" in r], "changes": _record_signals(rows),
                "categories": sorted({r["category"] for r in rows}), "mode": data.mode(), "as_of": time.strftime("%Y-%m-%d %H:%M")}
    return ok(cached("etfs", 1800, run))


# ------------------------------------------------------------------ stock detail
@app.get("/api/stock/{symbol}")
def stock(symbol: str, days: int = Query(504, ge=30, le=1260)):
    sym = data.normalize(symbol)
    a = analyze(sym)
    df = data.get_history(sym)
    return ok({**a, "chart": analysis.chart_series(df, days), "mode": data.mode(),
               "in_universe": universe.lookup(sym) is not None, "history": _symbol_changes(sym)})


@app.get("/api/financials/{symbol}")
def financials(symbol: str):
    """Five years of annual statements + recent quarters, with plain-English trend takeaways."""
    sym = data.normalize(symbol)
    if universe.lookup_etf(sym):
        return ok({"annual": {"periods": []}, "quarterly": {"periods": []}, "takeaways": [],
                   "error": "ETFs don't publish company statements. Holdings, expense ratio and tracking error are on the fund's factsheet."})
    fin = data.get_financials(sym)
    sector = data.get_info(sym).get("sector") or ""
    return ok({**fin, "takeaways": analysis.financial_takeaways(fin, sector)})


@app.get("/api/peers/{symbol}")
def peers(symbol: str):
    """Same-sector stocks from the screener universe, with sector medians for context."""
    sym = data.normalize(symbol)
    me = analyze(sym)
    etf = universe.lookup_etf(sym)
    sector = etf["category"] if etf else ((universe.lookup(sym) or {}).get("sector") or me["info"].get("sector") or "")

    def run():
        pool = universe.load_etfs() if etf else universe.load_universe()
        key = "category" if etf else "sector"
        cands = [u["symbol"] for u in pool if u[key] == sector and u["symbol"] != sym][:15]
        def one(s):
            try:
                return row(s, analyze(s))
            except Exception:
                return None
        with ThreadPoolExecutor(8) as ex:
            rows = [r for r in ex.map(one, cands) if r]
        def med(k):
            v = [r[k] for r in rows if r.get(k) is not None]
            return float(np.median(v)) if v else None
        return {"sector": sector, "kind": "etf" if etf else "stock", "peers": sorted(rows, key=lambda r: -(r["score"] or 0)),
                "median": {k: med(k) for k in ("pe", "pb", "roe", "score", "ret_6m", "upside")}}
    return ok({**cached(f"peers:{sector}:{sym}", 1800, run), "self": row(sym, me)})


@app.get("/api/backtest/stock/{symbol}")
def backtest_stock(symbol: str):
    sym = data.normalize(symbol)
    df = data.get_history(sym, 10)
    if df.empty:
        raise HTTPException(404, "No data")
    return ok(analysis.backtest_trend(df, data.get_history(data.BENCH, 10)))


@app.get("/api/backtest/momentum")
def backtest_momentum(top_n: int = Query(10, ge=3, le=30), lookback_months: int = Query(6, ge=1, le=12)):
    def run():
        syms = [u["symbol"] for u in universe.load_universe()]
        with ThreadPoolExecutor(8) as ex:
            hs = list(ex.map(lambda s: (s, data.get_history(s, 10)), syms))
        closes = pd.DataFrame({s: h["Close"] for s, h in hs if h is not None and not h.empty})
        res = analysis.backtest_momentum(closes, data.get_history(data.BENCH, 10)["Close"], top_n, lookback_months)
        res["universe_size"] = closes.shape[1]
        res["mode"] = data.mode()
        return res
    return ok(cached(f"bt:{top_n}:{lookback_months}", 3600, run))


# ------------------------------------------------------------------ persistence (watchlist, portfolio)
def _load(name, default):
    p = data.HOME / f"{name}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return default
    return default


def _save(name, obj):
    with _store_lock:
        (data.HOME / f"{name}.json").write_text(json.dumps(obj, indent=2))


@app.get("/api/watchlist")
def get_watchlist():
    def one(s):
        try:
            return row(s, analyze(s))
        except Exception:
            return {"symbol": s, "name": s, "error": True}
    with ThreadPoolExecutor(8) as ex:
        return ok(list(ex.map(one, _load("watchlist", []))))


@app.post("/api/watchlist/{symbol}")
def add_watch(symbol: str):
    sym, wl = data.normalize(symbol), _load("watchlist", [])
    if sym not in wl:
        wl.append(sym)
        _save("watchlist", wl)
    return ok(wl)


@app.delete("/api/watchlist/{symbol}")
def del_watch(symbol: str):
    sym = data.normalize(symbol)
    wl = [s for s in _load("watchlist", []) if s != sym]
    _save("watchlist", wl)
    return ok(wl)


@app.get("/api/portfolio")
def get_portfolio():
    items = _load("portfolio", [])
    bench = bench_df()["Close"]
    bnow = float(bench.iloc[-1])
    rows, tot_inv, tot_val, tot_bench = [], 0.0, 0.0, 0.0
    sectors: dict = {}
    for h in items:
        try:
            a = analyze(h["symbol"])
        except Exception:
            rows.append({**h, "error": "No data"})
            continue
        price = a["price"]
        inv, val = h["qty"] * h["price"], h["qty"] * price
        b0s = bench[:pd.Timestamp(h["date"])]
        b0 = float(b0s.iloc[-1]) if len(b0s) else float(bench.iloc[0])
        bval = inv * bnow / b0
        pnl_pct, b_pct = val / inv - 1, bval / inv - 1
        sec = a["info"].get("sector") or "Other"
        sectors[sec] = sectors.get(sec, 0) + val
        tot_inv, tot_val, tot_bench = tot_inv + inv, tot_val + val, tot_bench + bval
        rows.append({**h, "name": a["name"], "sector": sec, "current": price, "invested": inv, "value": val, "pnl": val - inv,
                     "pnl_pct": pnl_pct, "nifty_pct": b_pct, "alpha": pnl_pct - b_pct,
                     "signals": {k: v["signal"] for k, v in a["signals"].items()}, "fair_value": a["fair_value"].get("value")})
    for r in rows:
        if "value" in r:
            r["weight"] = r["value"] / tot_val if tot_val else 0
    warnings = []
    for r in rows:
        if r.get("weight", 0) > 0.25:
            warnings.append(f"{r['symbol']} is {r['weight']*100:.0f}% of your portfolio: concentration risk (rule of thumb: keep a single stock under ~15-20%).")
    for sec, v in sectors.items():
        if tot_val and v / tot_val > 0.40:
            warnings.append(f"{sec} is {v/tot_val*100:.0f}% of your portfolio: heavy sector concentration.")
    if 0 < len(rows) < 10:
        warnings.append(f"Only {len(rows)} holdings: fewer than ~10-15 stocks leaves you exposed to single-company risk. An index fund fixes that.")
    return ok({"holdings": rows, "warnings": warnings,
               "totals": {"invested": tot_inv, "value": tot_val, "pnl": tot_val - tot_inv,
                          "pnl_pct": (tot_val / tot_inv - 1) if tot_inv else None,
                          "nifty_value": tot_bench, "nifty_pct": (tot_bench / tot_inv - 1) if tot_inv else None,
                          "alpha": ((tot_val - tot_bench) / tot_inv) if tot_inv else None},
               "sectors": {k: v / tot_val for k, v in sectors.items()} if tot_val else {},
               "note": "'Nifty' = what the same money would be worth had you bought the Nifty 50 index on each buy date (price index, excludes dividends)."})


@app.post("/api/portfolio")
def add_holding(body: dict = Body(...)):
    try:
        h = {"id": uuid.uuid4().hex[:8], "symbol": data.normalize(body["symbol"]), "qty": float(body["qty"]),
             "price": float(body["price"]), "date": str(body["date"])[:10], "note": str(body.get("note", ""))[:500]}
        assert pd.Timestamp(h["date"]) <= pd.Timestamp.today().normalize()
        assert h["qty"] > 0 and h["price"] > 0
    except Exception:
        raise HTTPException(400, "Need symbol, qty > 0, price > 0 and a valid past date (YYYY-MM-DD).")
    items = _load("portfolio", []) + [h]
    _save("portfolio", items)
    return ok(h)


@app.delete("/api/portfolio/{hid}")
def del_holding(hid: str):
    _save("portfolio", [h for h in _load("portfolio", []) if h["id"] != hid])
    return ok({"deleted": hid})


# ------------------------------------------------------------------ mutual funds
@app.get("/api/mf/search")
def mf_search(q: str = ""):
    try:
        return ok(mf.search(q))
    except Exception as e:
        raise HTTPException(502, f"Fund search failed: {e}")


@app.get("/api/mf/compare")
def mf_compare(codes: str):
    try:
        cl = [int(c) for c in codes.split(",") if c.strip()][:5]
        return ok(mf.compare(cl))
    except Exception as e:
        raise HTTPException(502, f"Could not load funds: {e}")


@app.get("/api/mf/sip")
def mf_sip(code: int, monthly: float = 10000, years: int = Query(5, ge=1, le=15)):
    try:
        return ok(mf.sip(code, monthly, years))
    except Exception as e:
        raise HTTPException(502, f"SIP calc failed: {e}")


# ------------------------------------------------------------------ static frontend
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
