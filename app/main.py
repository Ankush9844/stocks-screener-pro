"""StockPro: personal stock research app (FastAPI). Run: python run.py"""
from __future__ import annotations
import json, math, os, threading, time, traceback, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analysis, data, mf, supa, universe

ACCESS_COOKIE, REFRESH_COOKIE = "sp_at", "sp_rt"


def gate(request: Request):
    """With Supabase configured, every /api route except status and the login endpoints needs a signed-in user;
    handlers read it from request.state.user. Without Supabase the app is open and user is None (single-user, files on disk)."""
    request.state.user = None
    path = request.url.path
    if not supa.enabled() or not path.startswith("/api/") or path == "/api/status" or path.startswith("/api/auth/"):
        return
    user = supa.user_for(request.cookies.get(ACCESS_COOKIE))
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    request.state.user = user


app = FastAPI(title="StockPro (personal)", dependencies=[Depends(gate)])


@app.exception_handler(supa.SupaError)
def _supa_error(request: Request, e: supa.SupaError):
    return JSONResponse({"detail": e.message}, status_code=e.status)


STATIC = Path(__file__).parent / "static"
_store_lock = threading.Lock()
_mem: dict = {}
_jobs: dict = {}            # background screener runs: name -> {"running", "done", "total", "rows", "failed", "started"}
_jobs_lock = threading.Lock()
HORIZONS = ("long", "medium", "short")
WORKERS = 6
# Serverless hosts (Vercel sets VERCEL=1) freeze the process after each response, so background threads cannot be relied on.
# There the screener advances a few seconds per poll request instead; the client keeps polling until it is done.
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("STOCKPRO_SERVERLESS"))
STEP_SECONDS = float(os.environ.get("STOCKPRO_STEP_SECONDS", "8"))


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
        try:
            return _analyze(sym)
        except HTTPException:
            raise
        except Exception as e:
            if data.rate_limited(e):
                raise HTTPException(503, "Yahoo Finance is rate-limiting requests right now. Wait a minute and try again.")
            raise
    return cached(f"an:{sym}", 1800, run)


def _analyze(sym: str) -> dict:
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
    s, fv, i, u = a["signals"], a["fair_value"], a["info"], universe.lookup(sym) or {}
    return {"symbol": sym, "name": a["name"], "sector": i.get("sector") or u.get("sector"), "industry": u.get("industry") or i.get("industry"),
            "group": u.get("group"), "price": a["price"], "change_1d": a["change_1d"],
            "pe": i.get("pe"), "pb": i.get("pb"), "roe": i.get("roe"), "market_cap": i.get("market_cap"), "dividend_yield": i.get("dividend_yield"),
            "debt_to_equity": i.get("debt_to_equity"), "revenue_growth": i.get("revenue_growth"), "earnings_growth": i.get("earnings_growth"),
            "consensus": i.get("consensus"), "analysts": i.get("analysts"), "fundamentals_ok": i.get("fundamentals_ok"),
            "fair_value": fv.get("value"), "upside": fv.get("upside"), "fv_confidence": fv.get("confidence"),
            "score": a["overall"], "ret_6m": a["tech"].get("ret_6m"), "ret_1y": a["tech"].get("ret_1y"), "rsi": a["tech"].get("rsi"), "trend": a["tech"]["trend"],
            "signals": {k: {"signal": v["signal"], "score": v["score"]} for k, v in s.items()}}


# ------------------------------------------------------------------ background screening
def _work(job: dict, batch: list[str], workers: int = WORKERS):
    """Analyse `batch` into job["rows"] / job["failed"] (blocking)."""
    def one(sym):
        try:
            r = row(sym, analyze(sym))
        except Exception as e:
            r = {"symbol": sym, "error": str(e)}
        with _jobs_lock:
            (job["failed"] if "error" in r else job["rows"]).append(r["symbol"] if "error" in r else r)
            job["done"] += 1
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, batch))


def _finish(name: str, job: dict, syms: list[str], extra):
    order = {s: k for k, s in enumerate(syms)}
    rows = sorted(job["rows"], key=lambda r: order.get(r["symbol"], 1e9))
    res = {"rows": rows, "failed": list(job["failed"]), "changes": _record_signals(rows), "mode": data.mode(),
           "as_of": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - job["started"])}
    if extra:
        res.update(extra(rows))
    _mem[name] = (time.time(), res)
    job["running"] = False


def _retry_failed(job: dict) -> bool:
    """Queue the failed symbols for one more pass (usually Yahoo throttling near the end of a big run). False if already done."""
    if not job["failed"] or job.get("retried") or data.mode() != "live":
        return False
    job["retried"], job["pending"], job["failed"] = True, list(job["failed"]), []
    job["done"] -= len(job["pending"])
    return True


def _run_screen(name: str, syms: list[str], job: dict, extra):
    """Thread mode: analyse everything, retry the failures once after a pause, then publish."""
    try:
        _work(job, syms)
        if _retry_failed(job):
            time.sleep(10)
            pending, job["pending"] = job["pending"], []
            _work(job, pending, max(2, WORKERS // 2))
        _finish(name, job, syms, extra)
    except Exception as e:
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["running"] = False


def _step(name: str, job: dict, syms: list[str], extra, budget: float):
    """Serverless mode: do up to `budget` seconds of the job inside this request."""
    t0 = time.time()
    try:
        while time.time() - t0 < budget:
            with _jobs_lock:
                batch, job["pending"] = job["pending"][:WORKERS], job["pending"][WORKERS:]
            if not batch:
                if _retry_failed(job):
                    continue
                _finish(name, job, syms, extra)
                return
            _work(job, batch)
    except Exception as e:
        job["error"], job["running"] = f"{type(e).__name__}: {e}", False


def _screen(name: str, syms: list[str], refresh: bool = False, extra=None, ttl: int = 1800) -> dict:
    """Screener results for `syms`. The first call starts a job and returns straight away with the rows done so far plus a
    `progress` block; the client polls until `progress.running` is false. Finished results are kept for `ttl` seconds.
    Refresh drops cached prices (ratios and statements keep their 24h cache: refetching those for 500 stocks is what gets
    a client throttled by Yahoo)."""
    with _jobs_lock:
        job = _jobs.get(name)
        running = bool(job and job["running"])
    if refresh and not running:
        _mem.pop(name, None)
        for k in [k for k in _mem if k.startswith("peers:")] + [f"an:{s}" for s in syms]:
            _mem.pop(k, None)
        data.invalidate(syms + [data.BENCH], kinds=("hist",))
    hit = _mem.get(name)
    if hit and time.time() - hit[0] < ttl:
        return {**hit[1], "progress": {"done": len(syms), "total": len(syms), "running": False}}
    with _jobs_lock:
        job = _jobs.get(name)
        if not job or not job["running"]:
            job = _jobs[name] = {"running": True, "done": 0, "total": len(syms), "rows": [], "failed": [], "pending": list(syms), "started": time.time()}
            if not SERVERLESS:
                threading.Thread(target=_run_screen, args=(name, syms, job, extra), daemon=True).start()
    if SERVERLESS and job["running"]:
        _step(name, job, syms, extra, STEP_SECONDS)
        hit = _mem.get(name)
        if hit and not job["running"] and not job.get("error"):
            return {**hit[1], "progress": {"done": len(syms), "total": len(syms), "running": False}}
    with _jobs_lock:
        snapshot = {"rows": list(job["rows"]), "failed": list(job["failed"]),
                    "progress": {"done": job["done"], "total": job["total"], "running": job["running"],
                                 "elapsed": round(time.time() - job["started"]), "error": job.get("error")}}
    return {**snapshot, "changes": [], "mode": data.mode(), "as_of": time.strftime("%Y-%m-%d %H:%M"), "partial": True}


# ------------------------------------------------------------------ status / search
@app.get("/api/status")
def status():
    return ok({"mode": data.mode(), "reason": data.mode_reason(), "universe_size": len(universe.load_universe()),
               "groups": universe.groups(), "etf_count": len(universe.load_etfs()), "home": str(data.HOME),
               "serverless": SERVERLESS, "ephemeral": data.EPHEMERAL, "auth": supa.enabled()})


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
    hist = _state_load(name, {"last": {}, "changes": []})
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
    _state_save(name, hist)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    return [c for c in hist["changes"] if c["date"] >= cutoff]


def _symbol_changes(sym: str) -> list[dict]:
    hist = _state_load(f"signal_history_{data.mode()}", {}, max_age=300)
    return [c for c in hist.get("changes", []) if c["symbol"] == sym][:10]


@app.get("/api/screener")
def screener(refresh: bool = False):
    """All stocks in the universe. Runs in the background: poll until progress.running is false."""
    syms = [u["symbol"] for u in universe.load_universe()]
    return ok(_screen("screener", syms, refresh))


@app.get("/api/etfs")
def etfs(refresh: bool = False):
    """ETF screener: returns, risk, liquidity and trend signals for every ETF in the universe (background job, poll like /api/screener)."""
    syms = [e["symbol"] for e in universe.load_etfs()]
    res = _screen("etfs", syms, refresh, extra=lambda rows: {"categories": sorted({r["category"] for r in rows})})
    res.setdefault("categories", sorted({r["category"] for r in res["rows"]}))
    return ok(res)


# ------------------------------------------------------------------ stock detail
@app.get("/api/stock/{symbol}")
def stock(symbol: str, days: int = Query(504, ge=30, le=1260)):
    sym = data.normalize(symbol)
    a = analyze(sym)
    df = data.get_history(sym)
    u = universe.lookup(sym)
    return ok({**a, "chart": analysis.chart_series(df, days), "mode": data.mode(), "group": (u or {}).get("group"),
               "in_universe": u is not None, "history": _symbol_changes(sym)})


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


@app.get("/api/extras/{symbol}")
def extras(symbol: str):
    """Earnings dates and surprises, dividend record, holders, analyst consensus, extra ratios and news."""
    sym = data.normalize(symbol)
    return ok({**data.get_extras(sym), "kind": "etf" if universe.lookup_etf(sym) else "stock", "mode": data.mode()})


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
        cands = [u["symbol"] for u in pool if u[key] == sector and u["symbol"] != sym][:10 if SERVERLESS else 30]
        def one(s):
            try:
                return row(s, analyze(s))
            except Exception:
                return None
        with ThreadPoolExecutor(WORKERS) as ex:
            rows = [r for r in ex.map(one, cands) if r]
        ind, label = me["info"].get("industry"), sector
        if ind and not etf:   # Yahoo's industry is finer than the sector (e.g. "Banks - Regional"): prefer it when there are enough
            same = [r for r in rows if (_mem.get(f"an:{r['symbol']}") or (0, {}))[1].get("info", {}).get("industry") == ind]
            if len(same) >= 3:
                rows, label = same, ind
        rows = rows[:15]
        def med(k):
            v = [r[k] for r in rows if r.get(k) is not None]
            return float(np.median(v)) if v else None
        return {"sector": label, "kind": "etf" if etf else "stock", "peers": sorted(rows, key=lambda r: -(r["score"] or 0)),
                "pool": len(cands),
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
def backtest_momentum(top_n: int = Query(10, ge=3, le=30), lookback_months: int = Query(6, ge=1, le=12), group: str = ""):
    """Cross-sectional momentum over one index group (default: the largest group present, i.e. Nifty 50), since 500 ten-year histories are slow."""
    groups = universe.groups()
    group = group if group in groups else (groups[0] if groups else "")

    def run():
        syms = [u["symbol"] for u in universe.load_universe() if not group or u["group"] == group]
        with ThreadPoolExecutor(WORKERS) as ex:
            hs = list(ex.map(lambda s: (s, data.get_history(s, 10)), syms))
        closes = pd.DataFrame({s: h["Close"] for s, h in hs if h is not None and not h.empty})
        res = analysis.backtest_momentum(closes, data.get_history(data.BENCH, 10)["Close"], top_n, lookback_months)
        res["universe_size"] = closes.shape[1]
        res["group"], res["groups"], res["mode"] = group, groups, data.mode()
        return res
    return ok(cached(f"bt:{group}:{top_n}:{lookback_months}", 3600, run))


# ------------------------------------------------------------------ login (Supabase Auth; the session lives in HttpOnly cookies)
def _origin(request: Request) -> str:
    """This deployment's public address: where Supabase's confirmation and password-reset emails should link back to
    (Supabase only honours it if it is listed under Authentication > URL Configuration)."""
    h = request.headers
    proto = (h.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{h.get('x-forwarded-host') or h.get('host') or request.url.netloc}"


def _public(u: dict) -> dict:
    return {"id": u.get("id"), "email": u.get("email")}


def _with_session(request: Request, resp: JSONResponse, s: dict) -> JSONResponse:
    secure = _origin(request).startswith("https://")
    ttl = max(int(s.get("expires_in") or 3600) - 60, 30)      # drop the cookie a little before the token itself expires
    resp.set_cookie(ACCESS_COOKIE, s["access_token"], max_age=ttl, httponly=True, samesite="lax", secure=secure, path="/")
    if s.get("refresh_token"):
        resp.set_cookie(REFRESH_COOKIE, s["refresh_token"], max_age=60 * 86400, httponly=True, samesite="lax", secure=secure, path="/api/auth")
    return resp


def _without_session(resp: JSONResponse) -> JSONResponse:
    resp.delete_cookie(ACCESS_COOKIE, path="/")
    resp.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return resp


def _credentials(body: dict, password: bool = True) -> tuple[str, str]:
    email, pw = str(body.get("email") or "").strip(), str(body.get("password") or "")
    if not supa.enabled():
        raise HTTPException(404, "Login is not configured on this server.")
    if "@" not in email or (password and not pw):
        raise HTTPException(400, "Enter your email and password." if password else "Enter your email.")
    return email, pw


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = supa.user_for(request.cookies.get(ACCESS_COOKIE)) if supa.enabled() else None
    if not user:
        raise HTTPException(401, "Not signed in.")
    return ok({"user": _public(user)})


@app.post("/api/auth/login")
def auth_login(request: Request, body: dict = Body(...)):
    s = supa.sign_in(*_credentials(body))
    return _with_session(request, ok({"user": _public(s.get("user") or {})}), s)


@app.post("/api/auth/signup")
def auth_signup(request: Request, body: dict = Body(...)):
    s = supa.sign_up(*_credentials(body), redirect_to=_origin(request))
    if not s.get("access_token"):       # the project asks new users to confirm their email first
        return ok({"confirm": True})
    return _with_session(request, ok({"user": _public(s.get("user") or {})}), s)


@app.post("/api/auth/refresh")
def auth_refresh(request: Request):
    rt = request.cookies.get(REFRESH_COOKIE)
    try:
        if not rt or not supa.enabled():
            raise supa.SupaError(401, "Not signed in.")
        s = supa.refresh(rt)
    except supa.SupaError as e:
        if e.status >= 500:
            raise                       # Supabase unreachable: keep the cookies, the next attempt may work
        return _without_session(JSONResponse({"detail": "Your session has ended. Sign in again."}, status_code=401))
    return _with_session(request, ok({"user": _public(s.get("user") or {})}), s)


@app.post("/api/auth/session")
def auth_session(request: Request, body: dict = Body(...)):
    """Adopt the session Supabase puts in the address after an emailed link (confirm sign-up, reset password) is opened."""
    at = str(body.get("access_token") or "")
    user = supa.user_for(at) if supa.enabled() else None
    if not user:
        raise HTTPException(401, "That email link is invalid or has expired.")
    s = {"access_token": at, "refresh_token": str(body.get("refresh_token") or ""), "expires_in": supa.token_expiry(at) - time.time()}
    return _with_session(request, ok({"user": _public(user)}), s)


@app.post("/api/auth/recover")
def auth_recover(request: Request, body: dict = Body(...)):
    supa.recover(_credentials(body, password=False)[0], redirect_to=_origin(request))
    return ok({"sent": True})


@app.post("/api/auth/password")
def auth_password(request: Request, body: dict = Body(...)):
    user = supa.user_for(request.cookies.get(ACCESS_COOKIE)) if supa.enabled() else None
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    supa.update_password(user["token"], str(body.get("password") or ""))
    return ok({"updated": True})


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    at = request.cookies.get(ACCESS_COOKIE)
    if at and supa.enabled():
        supa.sign_out(at)
    return _without_session(ok({"signed_out": True}))


# ------------------------------------------------------------------ persistence (watchlist, portfolio)
# Signed-in users (Supabase configured): rows in Supabase, private to each user. Otherwise: JSON files in data.HOME.
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


def _state_load(name, default, max_age: float = 0):
    """App-wide state (signal history): Supabase when a service-role key is set, else a JSON file. `max_age` > 0 accepts a
    copy this instance read that recently, which saves a round trip on every stock page."""
    if not supa.shared():
        return _load(name, default)
    hit = _mem.get("state:" + name)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    try:
        val = supa.state_get(name, default)
    except supa.SupaError:
        traceback.print_exc()           # a screener run must not fail because the history could not be read
        return _load(name, default)
    _mem["state:" + name] = (time.time(), val)
    return val


def _state_save(name, obj):
    if supa.shared():
        try:
            supa.state_put(name, obj)
            _mem["state:" + name] = (time.time(), obj)
            return
        except supa.SupaError:
            traceback.print_exc()
    _save(name, obj)


@app.get("/api/watchlist")
def get_watchlist(request: Request):
    user = request.state.user
    syms = supa.watchlist(user) if user else _load("watchlist", [])

    def one(s):
        try:
            return row(s, analyze(s))
        except Exception:
            return {"symbol": s, "name": s, "error": True}
    with ThreadPoolExecutor(WORKERS) as ex:
        return ok(list(ex.map(one, syms)))


@app.post("/api/watchlist/{symbol}")
def add_watch(symbol: str, request: Request):
    sym, user = data.normalize(symbol), request.state.user
    if user:
        supa.watch_add(user, sym)
        return ok(supa.watchlist(user))
    wl = _load("watchlist", [])
    if sym not in wl:
        wl.append(sym)
        _save("watchlist", wl)
    return ok(wl)


@app.delete("/api/watchlist/{symbol}")
def del_watch(symbol: str, request: Request):
    sym, user = data.normalize(symbol), request.state.user
    if user:
        supa.watch_del(user, sym)
        return ok(supa.watchlist(user))
    wl = [s for s in _load("watchlist", []) if s != sym]
    _save("watchlist", wl)
    return ok(wl)


@app.get("/api/portfolio")
def get_portfolio(request: Request):
    user = request.state.user
    items = supa.holdings(user) if user else _load("portfolio", [])
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
def add_holding(request: Request, body: dict = Body(...)):
    try:
        h = {"id": uuid.uuid4().hex[:8], "symbol": data.normalize(body["symbol"]), "qty": float(body["qty"]),
             "price": float(body["price"]), "date": str(body["date"])[:10], "note": str(body.get("note", ""))[:500]}
        assert pd.Timestamp(h["date"]) <= pd.Timestamp.today().normalize()
        assert h["qty"] > 0 and h["price"] > 0
    except Exception:
        raise HTTPException(400, "Need symbol, qty > 0, price > 0 and a valid past date (YYYY-MM-DD).")
    user = request.state.user
    if user:
        return ok(supa.holding_add(user, h))        # Supabase assigns the id
    items = _load("portfolio", []) + [h]
    _save("portfolio", items)
    return ok(h)


@app.delete("/api/portfolio/{hid}")
def del_holding(hid: str, request: Request):
    user = request.state.user
    if user:
        try:
            supa.holding_del(user, str(uuid.UUID(hid)))
        except ValueError:
            raise HTTPException(404, "No such holding.")
        return ok({"deleted": hid})
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
        traceback.print_exc()          # the host's runtime log then shows the real cause, not just the 502 text
        raise HTTPException(502, f"Could not load funds: {type(e).__name__}: {e}")


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
