"""Mutual fund data (AMFI NAVs via the free mfapi.in) + comparison metrics.
Expense ratio and AUM are not provided by mfapi.in: check them on the AMC factsheet / Groww / Coin."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from . import data
from .analysis import RF, _f, metrics_from_equity

API = "https://api.mfapi.in/mf"
DEMO_FUNDS = {
    900001: ("Demo Nifty 50 Index Fund - Direct Growth", 1.0, -0.0020, 0.0003),
    900002: ("Demo Nifty Next 50 Index Fund - Direct Growth", 1.15, 0.0, 0.0004),
    900003: ("Demo Flexi Cap Fund - Direct Growth", 0.95, 0.025, 0.004),
    900004: ("Demo Large Cap Fund - Regular Growth", 0.92, -0.004, 0.0035),
    900005: ("Demo Midcap Fund - Direct Growth", 1.2, 0.04, 0.007),
}


def search(q: str) -> list[dict]:
    if data.mode() == "demo":
        return [{"code": c, "name": v[0]} for c, v in DEMO_FUNDS.items() if q.lower() in v[0].lower() or not q]
    import requests
    hit = data.cache_get(f"mfsearch_{q}", 3600)
    if hit is not None:
        return hit
    r = requests.get(f"{API}/search", params={"q": q}, timeout=15)
    r.raise_for_status()
    out = [{"code": int(x["schemeCode"]), "name": x["schemeName"]} for x in r.json()][:40]
    data.cache_put(f"mfsearch_{q}", out)
    return out


def nav_series(code: int) -> tuple[str, pd.Series]:
    key = f"nav_{data.mode()}_{code}"
    hit = data.cache_get(key, 12 * 3600)
    if hit is not None:
        return hit
    if data.mode() == "demo":
        name, beta, alpha, te = DEMO_FUNDS[code]
        b = data.get_history(data.BENCH, 6)["Close"]
        rng = data._rng(f"mf{code}")
        r = beta * b.pct_change().fillna(0) + alpha / 252 + rng.normal(0, te / math.sqrt(252) * 3, len(b))
        s = 100 * (1 + r).cumprod()
        res = (name, pd.Series(s.values, index=b.index))
    else:
        import requests
        r = requests.get(f"{API}/{code}", timeout=20)
        r.raise_for_status()
        j = r.json()
        s = pd.Series({pd.to_datetime(x["date"], format="%d-%m-%Y"): float(x["nav"]) for x in j["data"]}, dtype="float64").sort_index()
        s = s[s > 0]                  # AMFI history sometimes starts with 0.0 placeholders, which would make every ratio inf/NaN
        if len(s) < 30:
            raise ValueError(f"Fund {code} has no usable NAV history on mfapi.in")
        res = (j["meta"]["scheme_name"], s)
    data.cache_put(key, res)
    return res


def _cagr_window(s: pd.Series, years: int):
    end = s.index[-1]
    start = end - pd.DateOffset(years=years)
    if s.index[0] > start + pd.Timedelta(days=10):
        return None
    base = s[:start]
    base = base.iloc[-1] if len(base) else s.iloc[0]
    return _f((s.iloc[-1] / base) ** (1 / years) - 1)


def fund_metrics(name: str, s: pd.Series, bench: pd.Series | None = None) -> dict:
    s = s.dropna()
    r = s.pct_change().dropna()
    last3 = r[r.index >= s.index[-1] - pd.DateOffset(years=3)]
    vol = _f(last3.std() * math.sqrt(252))
    c3 = _cagr_window(s, 3)
    m = metrics_from_equity(s)
    roll = (s / s.shift(252) - 1).dropna()
    out = {"name": name, "latest_nav": _f(s.iloc[-1]), "as_of": s.index[-1].strftime("%Y-%m-%d"),
           "cagr_1y": _cagr_window(s, 1), "cagr_3y": c3, "cagr_5y": _cagr_window(s, 5),
           "volatility_3y": vol, "max_drawdown": m["max_drawdown"],
           "sharpe_3y": _f((c3 - RF) / vol) if (c3 is not None and vol) else None,
           "rolling_1y_median": _f(roll.median()) if len(roll) else None,
           "rolling_1y_positive_pct": _f((roll > 0).mean()) if len(roll) else None,
           "is_index_fund": "index" in name.lower() or "nifty" in name.lower() or "sensex" in name.lower()}
    if bench is not None:
        b = bench.reindex(s.index).ffill().pct_change().dropna()
        a = (r - b).dropna()
        a = a[a.index >= s.index[-1] - pd.DateOffset(years=3)]
        if len(a) > 100:
            out["tracking_error_3y"] = _f(a.std() * math.sqrt(252))   # std-dev of daily return difference vs Nifty 50
            both = pd.concat([r, b], axis=1).dropna().iloc[-756:]
            cov = np.cov(both.iloc[:, 0], both.iloc[:, 1])
            out["beta_vs_nifty"] = _f(cov[0, 1] / cov[1, 1])
    return out


def compare(codes: list[int]) -> dict:
    bench = data.get_history(data.BENCH, 8)["Close"]
    funds, series = [], {}
    for c in codes:
        name, s = nav_series(c)
        funds.append({"code": c, **fund_metrics(name, s, bench)})
        series[c] = s
    start = max(s.index[0] for s in series.values())
    start = max(start, min(s.index[-1] for s in series.values()) - pd.DateOffset(years=5))
    idx = None
    out = {}
    for c, s in series.items():
        s = s[s.index >= start]
        out[c] = (s / s.iloc[0] * 100)
        idx = s.index if idx is None or len(s.index) > len(idx) else idx
    step = max(1, len(idx) // 400)
    dates = [d.strftime("%Y-%m-%d") for d in idx[::step]]
    curves = {str(c): [None if pd.isna(v) else round(float(v), 2) for v in out[c].reindex(idx).ffill().iloc[::step]] for c in out}
    return {"funds": funds, "dates": dates, "curves": curves,
            "notes": ["Tracking error is computed against the Nifty 50 price index; for non-Nifty index funds it will be large and meaningless.",
                      "Expense ratio and AUM are not in this data: check the fund's factsheet. Lower expense + lower tracking error is better for index funds.",
                      "Prefer Direct plans: lower cost than Regular for the same portfolio."]}


def _xirr(flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    t0 = flows[0][0]
    def npv(rate): return sum(cf / (1 + rate) ** ((d - t0).days / 365.0) for d, cf in flows)
    lo, hi = -0.9, 5.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def sip(code: int, monthly: float, years: int) -> dict:
    name, s = nav_series(code)
    start = s.index[-1] - pd.DateOffset(years=years)
    s = s[s.index >= start]
    if len(s) < 60:
        return {"error": f"Fund has less than {years} years of history."}
    months = s.groupby(s.index.to_period("M")).head(1)
    units = float((monthly / months).sum())
    invested = monthly * len(months)
    value = units * float(s.iloc[-1])
    flows = [(d, -monthly) for d in months.index] + [(s.index[-1], value)]
    lump_units = invested / float(s.iloc[0])
    return {"name": name, "months": len(months), "invested": invested, "value": value, "xirr": _f(_xirr(flows)),
            "lumpsum_value_same_total": lump_units * float(s.iloc[-1])}
