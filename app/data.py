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
        for pat in (f"hist_*_{safe}_*.pkl", f"info_*_{safe}.pkl", f"fin_*_{safe}.pkl"):
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
def _live_history(sym: str, years: int, period: str | None = None) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(sym).history(period=period or f"{years}y", auto_adjust=True)
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
    i = yf.Ticker(sym).info or {}
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
    }


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
        except Exception:
            info = {"name": sym, "price": price, "fundamentals_ok": False}
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
                 "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")}
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
            return getattr(t, attr)
        except Exception:
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


def get_financials(sym: str) -> dict:
    """Multi-year income statement, cash flow and balance sheet highlights. Values are absolute currency units."""
    key = f"fin_{mode()}_{sym}"
    hit = cache_get(key, FIN_TTL)
    if hit is not None:
        return hit
    try:
        fin = _demo_financials(sym) if mode() == "demo" else _live_financials(sym)
    except Exception as e:
        return {"annual": {"periods": []}, "quarterly": {"periods": []}, "error": f"Could not load statements ({type(e).__name__})."}
    fin["currency"] = get_info(sym).get("currency") or "INR"
    cache_put(key, fin)
    return fin
