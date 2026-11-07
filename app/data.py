"""Market-data layer.
LIVE mode  -> Yahoo Finance via yfinance (free, no key; NSE/BSE tickers like RELIANCE.NS).
DEMO mode  -> deterministic synthetic data so the app can be tried offline. Never use it to invest.
Mode is chosen once at start-up (set STOCKPRO_MODE=live|demo|auto)."""
from __future__ import annotations
import os, re, time, pickle, zlib, threading
from pathlib import Path
import numpy as np
import pandas as pd

HOME = Path(os.environ.get("STOCKPRO_HOME", Path.home() / ".stockpro"))
CACHE_DIR = HOME / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BENCH = "^NSEI"  # Nifty 50 (price index)
PRICE_TTL = 6 * 3600
INFO_TTL = 24 * 3600
FIN_TTL = 24 * 3600
EXTRAS_TTL = 6 * 3600   # earnings dates, dividends, holders, news

_mode = {"value": None, "reason": ""}
_lock = threading.Lock()


def normalize(sym: str) -> str:
    s = sym.strip().upper()
    if s.startswith("US:"):
        return s[3:]
    if s.startswith("^") or "." in s or s.endswith("=X"):
        return s
    return s + ".NS"


def _cpath(key: str) -> Path:
    return CACHE_DIR / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".pkl")


def cache_get(key: str, ttl: float):
    p = _cpath(key)
    if p.exists() and time.time() - p.stat().st_mtime < ttl:
        try:
            with open(p, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None
    return None


def cache_put(key: str, val):
    try:
        with open(_cpath(key), "wb") as f:
            pickle.dump(val, f)
    except Exception:
        pass


def invalidate(symbols) -> int:
    """Delete cached prices, fundamentals and statements for these symbols so the next call hits the source."""
    n = 0
    for s in symbols:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", s)
        for pat in (f"hist_*_{safe}_*.pkl", f"info_*_{safe}.pkl", f"fin_*_{safe}.pkl", f"extras_*_{safe}.pkl"):
            for p in CACHE_DIR.glob(pat):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
    return n


# ---------------------------------------------------------------- mode
def mode() -> str:
    with _lock:
        if _mode["value"] is None:
            forced = os.environ.get("STOCKPRO_MODE", "auto").lower()
            if forced == "demo":
                _mode.update(value="demo", reason="STOCKPRO_MODE=demo")
            else:
                try:
                    df = _live_history("^NSEI", 1, period="1mo")   # quick reachability probe
                    if len(df) > 5:
                        _mode.update(value="live", reason="Yahoo Finance reachable")
                    else:
                        raise RuntimeError("empty response")
                except Exception as e:  # noqa
                    if forced == "live":
                        raise
                    _mode.update(value="demo", reason=f"Live data unreachable ({type(e).__name__}); using synthetic demo data")
        return _mode["value"]


def mode_reason() -> str:
    mode()
    return _mode["reason"]


# ---------------------------------------------------------------- live
def rate_limited(e: BaseException) -> bool:
    return "RateLimit" in type(e).__name__ or "Too Many Requests" in str(e)


def _retry(fn, tries: int = 3, waits=(4, 12)):
    """Yahoo throttles bursts (a 500-stock screener run is one): back off and retry instead of failing the symbol."""
    for k in range(tries):
        try:
            return fn()
        except Exception as e:
            if not rate_limited(e) or k == tries - 1:
                raise
            time.sleep(waits[min(k, len(waits) - 1)])


def _live_history(sym: str, years: int, period: str | None = None) -> pd.DataFrame:
    import yfinance as yf
    df = _retry(lambda: yf.Ticker(sym).history(period=period or f"{years}y", auto_adjust=True))
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    return df[~df.index.duplicated(keep="last")]


def _num(x):
    try:
        x = float(x)
        return None if np.isnan(x) or np.isinf(x) else x
    except Exception:
        return None


def _live_info(sym: str, price: float | None) -> dict:
    import yfinance as yf
    i = _retry(lambda: yf.Ticker(sym).info) or {}
    price = _num(i.get("currentPrice")) or _num(i.get("regularMarketPrice")) or price
    de = _num(i.get("debtToEquity"))
    div_rate = _num(i.get("dividendRate"))
    return {
        "name": i.get("longName") or i.get("shortName") or sym, "sector": i.get("sector") or "",
        # False when Yahoo returned a bare quote with no financial ratios: the UI warns that scores are price-only
        "fundamentals_ok": bool(i.get("marketCap") or i.get("trailingEps") or i.get("trailingPE")),
        "industry": i.get("industry") or "", "currency": i.get("currency") or "INR",
        "price": price, "market_cap": _num(i.get("marketCap")),
        "pe": _num(i.get("trailingPE")), "forward_pe": _num(i.get("forwardPE")), "pb": _num(i.get("priceToBook")),
        "roe": _num(i.get("returnOnEquity")), "roa": _num(i.get("returnOnAssets")),
        "debt_to_equity": de / 100 if de is not None else None,  # Yahoo reports in %
        "current_ratio": _num(i.get("currentRatio")),
        "profit_margin": _num(i.get("profitMargins")), "op_margin": _num(i.get("operatingMargins")),
        "revenue_growth": _num(i.get("revenueGrowth")), "earnings_growth": _num(i.get("earningsGrowth")),
        "eps": _num(i.get("trailingEps")), "book_value": _num(i.get("bookValue")),
        "fcf": _num(i.get("freeCashflow")), "shares": _num(i.get("sharesOutstanding")),
        "dividend_yield": (div_rate / price) if (div_rate and price) else None,
        "target_mean": _num(i.get("targetMeanPrice")), "target_high": _num(i.get("targetHighPrice")),
        "target_low": _num(i.get("targetLowPrice")), "analysts": _num(i.get("numberOfAnalystOpinions")),
        "beta": _num(i.get("beta")), "avg_volume": _num(i.get("averageVolume")),
        "ps": _num(i.get("priceToSalesTrailing12Months")), "ev_ebitda": _num(i.get("enterpriseToEbitda")),
        "peg": _num(i.get("trailingPegRatio")) or _num(i.get("pegRatio")),
        "insiders": _num(i.get("heldPercentInsiders")), "institutions": _num(i.get("heldPercentInstitutions")),
        "consensus": i.get("recommendationKey") if i.get("recommendationKey") not in (None, "none") else None,
        "consensus_mean": _num(i.get("recommendationMean")),
        "next_earnings": _ts_date(i.get("earningsTimestamp")),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
    }


def _ts_date(ts):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts))) if ts else None
    except Exception:
        return None


# Yahoo's `info` ratios for Indian companies are often missing or stale. Anything we can rebuild from the statements, we do.
_DERIVABLE = ("profit_margin", "op_margin", "roe", "debt_to_equity", "current_ratio", "fcf", "eps", "book_value", "shares",
              "revenue_growth", "earnings_growth")


def _ttm(q: dict, key: str):
    """Trailing-12-month sum from the quarterly table, or None if fewer than 4 quarters."""
    v = [x for x in (q.get(key) or [])[-4:] if x is not None]
    return sum(v) if len(v) == 4 else None


def _latest(a: dict, key: str, back: int = 0):
    v = a.get(key) or []
    i = len(v) - 1 - back
    return v[i] if i >= 0 else None


def fill_from_statements(info: dict, fin: dict) -> dict:
    """Fill missing ratios (ROE, margins, D/E, current ratio, FCF, EPS, book value, growth) from the statements.
    Records which fields were derived in info["derived"] so the UI can say so."""
    a, q = fin.get("annual") or {}, fin.get("quarterly") or {}
    if not a.get("periods"):
        return info
    fin_sector = info.get("sector") in ("Financial Services",)
    ni, rev = _ttm(q, "net_income") or _latest(a, "net_income"), _ttm(q, "revenue") or _latest(a, "revenue")
    equity, debt = _latest(a, "equity"), _latest(a, "total_debt")
    shares = info.get("shares") or _latest(a, "shares_out")
    cand = {
        "profit_margin": (ni / rev) if (ni is not None and rev) else None,
        "op_margin": (_latest(a, "operating_income") / _latest(a, "revenue")) if (_latest(a, "operating_income") is not None and _latest(a, "revenue")) else None,
        "roe": (ni / equity) if (ni is not None and equity and equity > 0) else None,
        "debt_to_equity": None if fin_sector else ((debt / equity) if (debt is not None and equity and equity > 0) else None),
        "current_ratio": None if fin_sector else ((_latest(a, "current_assets") / _latest(a, "current_liabilities"))
                                                   if (_latest(a, "current_assets") and _latest(a, "current_liabilities")) else None),
        "fcf": None if fin_sector else (_latest(a, "free_cash_flow") if _latest(a, "free_cash_flow") is not None else
                                        ((_latest(a, "operating_cash_flow") - abs(_latest(a, "capex") or 0)) if _latest(a, "operating_cash_flow") is not None else None)),
        "eps": (ni / shares) if (ni is not None and shares) else None,
        "book_value": (equity / shares) if (equity and shares) else None,
        "shares": shares,
        "revenue_growth": (_latest(a, "revenue") / _latest(a, "revenue", 1) - 1) if (_latest(a, "revenue") and _latest(a, "revenue", 1) and _latest(a, "revenue", 1) > 0) else None,
        "earnings_growth": (_latest(a, "net_income") / _latest(a, "net_income", 1) - 1) if (_latest(a, "net_income") and _latest(a, "net_income", 1) and _latest(a, "net_income", 1) > 0) else None,
    }
    derived = []
    for k in _DERIVABLE:
        if info.get(k) is None and cand.get(k) is not None:
            info[k] = float(cand[k])
            derived.append(k)
    if derived:
        info["derived"] = derived
        if info.get("eps") and info.get("price") and info.get("pe") is None and info["eps"] > 0:
            info["pe"] = info["price"] / info["eps"]; derived.append("pe")
        if info.get("book_value") and info.get("price") and info.get("pb") is None and info["book_value"] > 0:
            info["pb"] = info["price"] / info["book_value"]; derived.append("pb")
        if not info.get("fundamentals_ok") and (info.get("roe") is not None or info.get("eps") is not None):
            info["fundamentals_ok"] = True
    return info


# ---------------------------------------------------------------- demo
def _rng(key: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(key.encode()))


def _market_factor(n: int) -> np.ndarray:
    return np.random.default_rng(42).normal(0.11 / 252, 0.14 / np.sqrt(252), n)


def _demo_history(sym: str, years: int) -> pd.DataFrame:
    rng = _rng(sym)
    n = 252 * 12                      # always build 12y, then slice, so different windows stay consistent
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    mkt = _market_factor(n)
    if sym == BENCH:
        r, start = mkt, 14000.0
    else:
        beta, mu, sig = rng.uniform(0.6, 1.5), rng.uniform(-0.03, 0.22), rng.uniform(0.15, 0.30)
        r = mu / 252 + beta * (mkt - 0.11 / 252) + rng.normal(0, sig / np.sqrt(252), n)
        start = float(rng.uniform(150, 3500))
    close = start * np.exp(np.cumsum(r))
    hi = close * (1 + np.abs(rng.normal(0, 0.007, n)))
    lo = close * (1 - np.abs(rng.normal(0, 0.007, n)))
    op = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(5e5, 5e6, n).astype(float)
    df = pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=idx)
    return df.iloc[-252 * years:]


def _demo_info(sym: str, price: float) -> dict:
    from . import universe
    rng = _rng(sym + "|info")
    e = universe.lookup_etf(sym)
    if e:   # ETFs: no company fundamentals, just a name, the index P/E and liquidity
        return {"name": e["name"] + " (demo)", "sector": e["category"], "industry": "", "currency": "INR", "fundamentals_ok": False,
                "price": price, "pe": float(rng.uniform(12, 30)) if e["category"] != "Commodity" else None,
                "avg_volume": float(rng.uniform(2e4, 2e7)), "dividend_yield": None}
    u = universe.lookup(sym) or {}
    sector = u.get("sector", "")
    pe = float(rng.uniform(9, 55))
    eps = price / pe
    roe = float(rng.uniform(0.05, 0.32))
    shares = float(rng.uniform(2e8, 3e9))
    fin = sector == "Financial Services"
    return {
        "name": u.get("name", sym) + " (demo)", "sector": sector, "industry": "", "currency": "INR", "fundamentals_ok": True,
        "price": price, "market_cap": price * shares, "pe": pe, "forward_pe": pe * float(rng.uniform(0.8, 1.05)),
        "pb": roe * pe, "roe": roe, "roa": roe / (8 if fin else 2.2),
        "debt_to_equity": None if fin else float(rng.uniform(0.05, 2.2)),
        "current_ratio": None if fin else float(rng.uniform(0.7, 2.6)),
        "profit_margin": float(rng.uniform(0.03, 0.28)), "op_margin": float(rng.uniform(0.05, 0.35)),
        "revenue_growth": float(rng.uniform(-0.06, 0.26)), "earnings_growth": float(rng.uniform(-0.15, 0.35)),
        "eps": eps, "book_value": price / (roe * pe), "fcf": None if fin else float(price * shares * rng.uniform(-0.005, 0.05)),
        "shares": shares, "dividend_yield": float(rng.uniform(0, 0.03)),
        "target_mean": price * float(rng.uniform(0.85, 1.3)), "target_high": None, "target_low": None,
        "analysts": float(rng.integers(5, 40)), "beta": float(rng.uniform(0.6, 1.5)),
    }


# ---------------------------------------------------------------- public API
def get_history(sym: str, years: int = 5) -> pd.DataFrame:
    key = f"hist_{mode()}_{sym}_{years}"
    hit = cache_get(key, PRICE_TTL)
    if hit is not None:
        return hit
    df = _demo_history(sym, years) if mode() == "demo" else _live_history(sym, years)
    if not df.empty:
        cache_put(key, df)
    return df


def get_info(sym: str) -> dict:
    key = f"info_{mode()}_{sym}"
    hit = cache_get(key, INFO_TTL)
    if hit is not None:
        return hit
    hist = get_history(sym)
    price = float(hist["Close"].iloc[-1]) if not hist.empty else None
    if mode() == "demo":
        info = _demo_info(sym, price or 100.0)
    else:
        try:
            info = _live_info(sym, price)
        except Exception as e:
            if rate_limited(e):
                raise
            info = {"name": sym, "price": price, "fundamentals_ok": False, "fetched_at": time.strftime("%Y-%m-%d %H:%M")}
        info["price"] = info.get("price") or price
        if any(info.get(k) is None for k in _DERIVABLE) and not sym.startswith("^"):
            fill_from_statements(info, _financials_raw(sym))
    info["price"] = info.get("price") or price
    cache_put(key, info)
    return info


# ---------------------------------------------------------------- financial statements (annual + quarterly)
# label -> candidate row names in Yahoo's statements (first match wins)
_INCOME_ROWS = {"revenue": ("Total Revenue", "Operating Revenue"), "operating_income": ("Operating Income", "EBIT"),
                "net_income": ("Net Income Common Stockholders", "Net Income"), "eps": ("Diluted EPS", "Basic EPS")}
_CASHFLOW_ROWS = {"operating_cash_flow": ("Operating Cash Flow",), "capex": ("Capital Expenditure",),
                  "free_cash_flow": ("Free Cash Flow",)}
_BALANCE_ROWS = {"total_debt": ("Total Debt",), "equity": ("Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"),
                 "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
                 "current_assets": ("Current Assets",), "current_liabilities": ("Current Liabilities",),
                 "shares_out": ("Ordinary Shares Number", "Share Issued")}
FIN_LABELS = {**_INCOME_ROWS, **_CASHFLOW_ROWS, **_BALANCE_ROWS}


def _period_label(c) -> str:
    return pd.Timestamp(c).strftime("%b %Y")


def _tabulate(frames) -> dict:
    """frames: [(statement DataFrame, {label: candidate rows})] -> {"periods": [...oldest..newest], label: [values]}."""
    series = {}
    for df, rows in frames:
        if df is None or getattr(df, "empty", True):
            continue
        for label, cands in rows.items():
            for c in cands:
                if c in df.index:
                    s = df.loc[c]
                    if isinstance(s, pd.DataFrame):      # duplicated row label
                        s = s.iloc[0]
                    series[label] = s
                    break
    if not series:
        return {"periods": []}
    cols = sorted({c for s in series.values() for c in s.index if pd.notna(c)})
    table = {label: [_num(s.get(c)) for c in cols] for label, s in series.items()}
    keep = [i for i in range(len(cols)) if any(v[i] is not None for v in table.values())]
    out = {"periods": [_period_label(cols[i]) for i in keep]}
    out.update({label: [v[i] for i in keep] for label, v in table.items()})
    return out


def _live_financials(sym: str) -> dict:
    import yfinance as yf
    t = yf.Ticker(sym)

    def grab(attr):
        try:
            return _retry(lambda: getattr(t, attr))
        except Exception as e:
            if rate_limited(e):
                raise
            return None
    annual = _tabulate([(grab("income_stmt"), _INCOME_ROWS), (grab("cashflow"), _CASHFLOW_ROWS), (grab("balance_sheet"), _BALANCE_ROWS)])
    quarterly = _tabulate([(grab("quarterly_income_stmt"), _INCOME_ROWS)])
    return {"annual": annual, "quarterly": quarterly, "source": "Yahoo Finance (consolidated, as reported)"}


def _demo_financials(sym: str) -> dict:
    info = get_info(sym)
    rng = _rng(sym + "|fin")
    shares, price = info.get("shares") or 1e9, info.get("price") or 100.0
    g, margin = info.get("revenue_growth") or 0.08, info.get("profit_margin") or 0.1
    fin = info.get("sector") == "Financial Services"
    today = pd.Timestamp.today()
    end = pd.Timestamp(year=today.year if today.month >= 4 else today.year - 1, month=3, day=31)
    cols = [end - pd.DateOffset(years=k) for k in range(4, -1, -1)]
    rev_now = price * shares * float(rng.uniform(0.3, 1.5))
    rev = [rev_now / (1 + g) ** k * (1 + float(rng.normal(0, 0.03))) for k in range(4, -1, -1)]
    ni = [r * margin * (1 + float(rng.normal(0, 0.12))) for r in rev]
    eq0 = price * shares / (info.get("pb") or 3)
    annual = {"periods": [_period_label(c) for c in cols], "revenue": rev, "operating_income": [r * margin * 1.4 for r in rev], "net_income": ni,
              "eps": [x / shares for x in ni], "operating_cash_flow": [x * float(rng.uniform(0.7, 1.3)) for x in ni],
              "capex": [-r * float(rng.uniform(0.02, 0.08)) for r in rev]}
    annual["free_cash_flow"] = [o + c for o, c in zip(annual["operating_cash_flow"], annual["capex"])]
    annual["equity"] = [eq0 * (1 - 0.1 * k) for k in range(4, -1, -1)]
    annual["total_debt"] = [None] * 5 if fin else [e * (info.get("debt_to_equity") or 0.5) * (1 + float(rng.normal(0, 0.1))) for e in annual["equity"]]
    annual["cash"] = [e * float(rng.uniform(0.05, 0.2)) for e in annual["equity"]]
    qcols = [end - pd.DateOffset(months=3 * k) for k in range(4, -1, -1)]
    qrev = [rev[-1] / 4 * (1 + g) ** (k / 4) * (1 + float(rng.normal(0, 0.04))) for k in range(-4, 1)]
    qni = [r * margin * (1 + float(rng.normal(0, 0.15))) for r in qrev]
    quarterly = {"periods": [_period_label(c) for c in qcols], "revenue": qrev, "net_income": qni, "eps": [x / shares for x in qni]}
    return {"annual": annual, "quarterly": quarterly, "source": "Synthetic demo data"}


def _financials_raw(sym: str) -> dict:
    key = f"fin_{mode()}_{sym}"
    hit = cache_get(key, FIN_TTL)
    if hit is not None:
        return hit
    try:
        fin = _demo_financials(sym) if mode() == "demo" else _live_financials(sym)
    except Exception as e:
        if rate_limited(e):
            raise
        return {"annual": {"periods": []}, "quarterly": {"periods": []}, "error": f"Could not load statements ({type(e).__name__})."}
    cache_put(key, fin)
    return fin


def get_financials(sym: str) -> dict:
    """Multi-year income statement, cash flow and balance sheet highlights. Values are absolute currency units."""
    fin = dict(_financials_raw(sym))
    fin["currency"] = get_info(sym).get("currency") or "INR"
    return fin


# ---------------------------------------------------------------- extras: earnings, dividends, holders, consensus, news
def _live_extras(sym: str, info: dict) -> dict:
    import yfinance as yf
    t = yf.Ticker(sym)
    price = info.get("price")

    def grab(attr):
        try:
            return getattr(t, attr)
        except Exception:
            return None
    out = {"earnings": {"next_date": info.get("next_earnings"), "history": []}, "dividends": {"history": []}, "news": []}
    cal = grab("calendar") or {}
    ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
    if ed:
        out["earnings"]["next_date"] = str(ed[0]) if isinstance(ed, (list, tuple)) else str(ed)
    edf = grab("earnings_dates")
    if edf is not None and not getattr(edf, "empty", True):
        today = pd.Timestamp.today().normalize()
        rows = []
        for ts, r in edf.iterrows():
            d = pd.Timestamp(ts).tz_localize(None).normalize() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).normalize()
            act = _num(r.get("Reported EPS"))
            if act is None or d > today:
                continue
            rows.append({"date": d.strftime("%Y-%m-%d"), "eps_est": _num(r.get("EPS Estimate")), "eps_actual": act, "surprise": _num(r.get("Surprise(%)"))})
        seen = set()
        out["earnings"]["history"] = [r for r in sorted(rows, key=lambda r: r["date"], reverse=True) if not (r["date"] in seen or seen.add(r["date"]))][:8]
    dv = grab("dividends")
    if dv is not None and len(dv):
        dv = dv[dv > 0]
        idx = pd.DatetimeIndex(dv.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        dv.index = idx
        cutoff = pd.Timestamp.today() - pd.DateOffset(years=5)
        recent = dv[dv.index >= cutoff]
        ttm = float(dv[dv.index >= pd.Timestamp.today() - pd.DateOffset(days=365)].sum())
        by_year = recent.groupby(recent.index.year).sum()
        growth = None
        if len(by_year) >= 3 and by_year.iloc[0] > 0:
            yrs = int(by_year.index[-1] - by_year.index[0])
            growth = float((by_year.iloc[-1] / by_year.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 else None
        out["dividends"] = {"history": [{"date": d.strftime("%Y-%m-%d"), "amount": float(v)} for d, v in recent.iloc[-12:].items()][::-1],
                            "ttm": ttm, "yield": (ttm / price) if (ttm and price) else None,
                            "years_paid": int((dv.groupby(dv.index.year).sum() > 0).sum()), "growth": growth,
                            "last_date": dv.index[-1].strftime("%Y-%m-%d")}
    news = grab("news") or []
    for n in news[:8]:
        c = n.get("content", n) if isinstance(n, dict) else {}
        url = (c.get("canonicalUrl") or c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("canonicalUrl") or c.get("clickThroughUrl"), dict) else c.get("link")
        if c.get("title") and url:
            out["news"].append({"title": c["title"], "url": url, "provider": (c.get("provider") or {}).get("displayName", ""),
                                "date": (c.get("pubDate") or "")[:10]})
    out["holders"] = {"insiders": info.get("insiders"), "institutions": info.get("institutions")}
    out["consensus"] = {"key": info.get("consensus"), "mean": info.get("consensus_mean"), "analysts": info.get("analysts"),
                        "target_mean": info.get("target_mean"), "target_high": info.get("target_high"), "target_low": info.get("target_low")}
    out["valuation"] = {"ps": info.get("ps"), "ev_ebitda": info.get("ev_ebitda"), "peg": info.get("peg"), "forward_pe": info.get("forward_pe")}
    return out


def _demo_extras(sym: str, info: dict) -> dict:
    rng = _rng(sym + "|extras")
    price, eps = info.get("price") or 100.0, info.get("eps") or 5.0
    today = pd.Timestamp.today().normalize()
    qdates = [today - pd.DateOffset(months=3 * k + 1) for k in range(6)]
    hist = []
    for d in qdates:
        est = eps / 4 * float(rng.uniform(0.9, 1.1)); act = est * float(rng.uniform(0.85, 1.15))
        hist.append({"date": d.strftime("%Y-%m-%d"), "eps_est": est, "eps_actual": act, "surprise": (act / est - 1) * 100})
    amt = price * float(rng.uniform(0.003, 0.025))
    divs = [{"date": (today - pd.DateOffset(years=k, months=2)).strftime("%Y-%m-%d"), "amount": amt / (1.08 ** k)} for k in range(5)]
    keys = ["strong_buy", "buy", "hold", "underperform"]
    mean = float(rng.uniform(1.5, 3.5))
    return {"earnings": {"next_date": (today + pd.DateOffset(days=int(rng.integers(5, 80)))).strftime("%Y-%m-%d"), "history": hist},
            "dividends": {"history": divs, "ttm": amt, "yield": amt / price, "years_paid": 5, "growth": 0.08, "last_date": divs[0]["date"]},
            "news": [{"title": f"{info.get('name', sym)}: synthetic headline {k + 1} (demo mode has no news feed)", "url": "#", "provider": "demo", "date": today.strftime("%Y-%m-%d")} for k in range(3)],
            "holders": {"insiders": float(rng.uniform(0.2, 0.75)), "institutions": float(rng.uniform(0.1, 0.5))},
            "consensus": {"key": keys[min(3, int((mean - 1) // 0.8))], "mean": mean, "analysts": info.get("analysts"),
                          "target_mean": info.get("target_mean"), "target_high": None, "target_low": None},
            "valuation": {"ps": float(rng.uniform(0.5, 12)), "ev_ebitda": float(rng.uniform(5, 40)), "peg": None, "forward_pe": info.get("forward_pe")}}


def get_extras(sym: str) -> dict:
    """Earnings dates and surprises, dividend record, holders, analyst consensus, extra valuation ratios and news."""
    key = f"extras_{mode()}_{sym}"
    hit = cache_get(key, EXTRAS_TTL)
    if hit is not None:
        return hit
    info = get_info(sym)
    try:
        ex = _demo_extras(sym, info) if mode() == "demo" else _live_extras(sym, info)
    except Exception as e:
        return {"earnings": {"history": []}, "dividends": {"history": []}, "news": [], "error": f"Could not load extras ({type(e).__name__})."}
    ex["fetched_at"] = time.strftime("%Y-%m-%d %H:%M")
    cache_put(key, ex)
    return ex
