"""Analysis engine: indicators, fair value, health score, horizon signals, backtests.

Everything here is a transparent, rule-based heuristic. It is research input, not advice.
The rules are deliberately simple so you can read, question and change them."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

RF = 0.065          # approx. Indian risk-free rate, used for Sharpe-style ratios
COST = 0.002        # 0.2% round-trip cost assumption in backtests (brokerage + STT + slippage)
FINANCIALS = {"Financial Services"}


def _f(x):
    try:
        if x is None:
            return None
        x = float(x)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except Exception:
        return None


def scale(x, lo, hi):
    """Linear 0-100 score. Works for inverse metrics too (lo > hi)."""
    x = _f(x)
    if x is None:
        return None
    return float(np.clip((x - lo) / (hi - lo), 0, 1) * 100)


def avg(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


# ------------------------------------------------------------------ indicators
def sma(s, n): return s.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    au, ad = up.ewm(alpha=1 / n, adjust=False).mean(), dn.ewm(alpha=1 / n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(ad.notna(), np.nan)


def macd(close):
    line = ema(close, 12) - ema(close, 26)
    sig = ema(line, 9)
    return line, sig, line - sig


def atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _last(s, k=1):
    try:
        v = s.iloc[-k]
        return _f(v)
    except Exception:
        return None


def tech_snapshot(df: pd.DataFrame) -> dict:
    c = df["Close"]
    s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
    r = rsi(c)
    _, _, hist = macd(c)
    sd = c.rolling(20).std()
    lower, upper = s20 - 2 * sd, s20 + 2 * sd
    price = _last(c)

    def ret(n):
        return _f(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None

    bb = None
    if _last(upper) is not None and _last(upper) != _last(lower):
        bb = (price - _last(lower)) / (_last(upper) - _last(lower))
    yr = c.iloc[-252:]
    snap = {
        "price": price, "sma20": _last(s20), "sma50": _last(s50), "sma200": _last(s200),
        "rsi": _last(r), "macd_hist": _last(hist), "macd_hist_prev": _last(hist, 2),
        "bb_pos": bb, "atr": _last(atr(df)),
        "ret_1m": ret(21), "ret_3m": ret(63), "ret_6m": ret(126), "ret_1y": ret(252),
        "vol_ann": _f(c.pct_change().iloc[-252:].std() * math.sqrt(252)),
        "high_52w": _f(yr.max()), "low_52w": _f(yr.min()),
        "change_1d": ret(1),
    }
    snap["from_high"] = _f(price / snap["high_52w"] - 1) if snap["high_52w"] else None
    s50v, s200v = snap["sma50"], snap["sma200"]
    if s200v and s50v:
        snap["trend"] = "up" if (price > s200v and s50v > s200v) else "down" if (price < s200v and s50v < s200v) else "mixed"
    else:
        snap["trend"] = "unknown"
    return snap


# ------------------------------------------------------------------ fair value
def growth_estimate(info):
    gs = [g for g in (info.get("earnings_growth"), info.get("revenue_growth")) if g is not None]
    if not gs:
        return 0.08, False
    return float(np.clip(sum(gs) / len(gs), 0.02, 0.22)), True


def dcf_per_share(fcf, shares, g, disc=0.12, term=0.05, years=10):
    total, cf = 0.0, fcf
    for t in range(1, years + 1):
        gt = g + (term - g) * (t - 1) / (years - 1)   # growth fades linearly to terminal rate
        cf *= 1 + gt
        total += cf / (1 + disc) ** t
    total += cf * (1 + term) / (disc - term) / (1 + disc) ** years
    return total / shares


def fair_value(info: dict, price: float) -> dict:
    models, notes = {}, []
    eps, bv, fcf, shares = info.get("eps"), info.get("book_value"), info.get("fcf"), info.get("shares")
    fin = info.get("sector") in FINANCIALS
    g, g_known = growth_estimate(info)
    if eps and eps > 0 and bv and bv > 0:
        models["Graham number"] = math.sqrt(22.5 * eps * bv)
    if eps and eps > 0:
        fair_pe = float(np.clip(12 + 100 * g, 12, 32))
        models[f"Growth-adjusted P/E ({fair_pe:.0f}x)"] = eps * fair_pe
    if not fin and fcf and fcf > 0 and shares and shares > 0:
        models["DCF (10y, 12% discount)"] = dcf_per_share(fcf, shares, g)
    elif fin:
        notes.append("DCF skipped: free cash flow isn't meaningful for banks/NBFCs.")
    if not g_known:
        notes.append("No growth data: assumed 8% growth.")
    vals = sorted(models.values())
    if not vals or not price:
        return {"value": None, "upside": None, "mos": None, "models": models, "confidence": "None",
                "analyst_target": info.get("target_mean"), "growth_assumed": g, "notes": notes + ["Not enough data for a fair value."]}
    value = float(np.median(vals))
    spread = vals[-1] / vals[0] if vals[0] > 0 else 9
    conf = "High" if len(vals) >= 3 and spread < 1.6 else "Medium" if len(vals) >= 2 and spread < 2.5 else "Low"
    return {"value": value, "upside": value / price - 1, "mos": (value - price) / value, "models": models,
            "confidence": conf, "analyst_target": info.get("target_mean"), "growth_assumed": g, "notes": notes}


# ------------------------------------------------------------------ pillars
def pillars(info: dict, tech: dict, fv: dict) -> dict:
    fin = info.get("sector") in FINANCIALS
    fcf = info.get("fcf")
    price, s200 = tech["price"], tech["sma200"]
    vs200 = (price / s200 - 1) if (price and s200) else None
    return {
        "Profitability": avg([scale(info.get("roe"), 0.05, 0.25), scale(info.get("profit_margin"), 0.03, 0.25)]),
        "Growth": avg([scale(info.get("revenue_growth"), -0.05, 0.25), scale(info.get("earnings_growth"), -0.10, 0.30)]),
        "Financial strength": avg([
            None if fin else scale(info.get("debt_to_equity"), 2.0, 0.2),
            None if fin else scale(info.get("current_ratio"), 0.8, 2.0),
            None if (fin or fcf is None) else (100.0 if fcf > 0 else 15.0)]),
        "Valuation": avg([scale(info.get("pe"), 45, 12), scale(info.get("pb"), 9, 1.2), scale(fv.get("mos"), -0.4, 0.4)]),
        "Momentum": avg([scale(vs200, -0.15, 0.20), scale(tech.get("ret_6m"), -0.15, 0.35), scale(tech.get("ret_1y"), -0.15, 0.50)]),
    }


WEIGHTS = {
    "long": {"Profitability": .25, "Growth": .20, "Financial strength": .15, "Valuation": .30, "Momentum": .10},
    "medium": {"Profitability": .15, "Growth": .20, "Financial strength": .05, "Valuation": .15, "Momentum": .45},
}


def weighted(p, w):
    num = sum(p[k] * w[k] for k in w if p.get(k) is not None)
    den = sum(w[k] for k in w if p.get(k) is not None)
    return num / den if den else None


def _pct(x, d=1):
    return f"{x * 100:.{d}f}%"


# ------------------------------------------------------------------ signals
def long_signal(info, tech, fv, p):
    score = weighted(p, WEIGHTS["long"])
    reasons = []
    # Only let the fair-value estimate override the score when the models broadly agree.
    fv_usable = fv.get("confidence") in ("Medium", "High")
    mos = fv.get("mos") if fv_usable else None
    if info.get("roe") is not None:
        r = info["roe"]
        reasons.append(("pos" if r >= .18 else "neg" if r < .08 else "neu", f"ROE {_pct(r)} " + ("- efficient use of shareholder money" if r >= .18 else "- weak returns on equity" if r < .08 else "- average")))
    if info.get("profit_margin") is not None:
        m = info["profit_margin"]
        reasons.append(("pos" if m >= .15 else "neg" if m < .05 else "neu", f"Net profit margin {_pct(m)}"))
    for k, lab in (("revenue_growth", "Revenue growth"), ("earnings_growth", "Earnings growth")):
        g = info.get(k)
        if g is not None:
            reasons.append(("pos" if g >= .12 else "neg" if g < 0 else "neu", f"{lab} {_pct(g)}"))
    if info.get("sector") not in FINANCIALS:
        de = info.get("debt_to_equity")
        if de is not None:
            reasons.append(("pos" if de < .5 else "neg" if de > 1.5 else "neu", f"Debt/equity {de:.2f}" + (" - low debt" if de < .5 else " - heavily indebted" if de > 1.5 else "")))
        if info.get("fcf") is not None:
            reasons.append(("pos" if info["fcf"] > 0 else "neg", "Free cash flow positive" if info["fcf"] > 0 else "Burning cash (negative free cash flow)"))
    if info.get("pe") is not None:
        pe = info["pe"]
        reasons.append(("pos" if pe < 15 else "neg" if pe > 45 else "neu", f"P/E {pe:.1f}"))
    if fv.get("value"):
        u = fv["upside"]
        reasons.append(("pos" if u >= .15 else "neg" if u <= -.20 else "neu",
                        f"Fair value est. {fv['value']:.0f} => {_pct(abs(u), 0)} {'below' if u > 0 else 'above'} price (confidence: {fv['confidence']})"))
        if not fv_usable:
            reasons.append(("neu", "Fair value models disagree (Low confidence), so valuation does not override the score here."))
    at = info.get("target_mean")
    if at and tech["price"]:
        cons = info.get("consensus")
        who = f"{int(info['analysts'])} analysts" if info.get("analysts") else "analysts"
        label = {"strong_buy": "Strong buy", "buy": "Buy", "hold": "Hold", "underperform": "Underperform", "sell": "Sell"}.get(cons or "", "")
        reasons.append(("neu", f"Street view: {label + ' consensus, ' if label else ''}mean target {at:.0f} ({_pct(at / tech['price'] - 1, 0)} vs price) from {who}. Analysts chase prices too, so weigh it lightly."))
    if info.get("fundamentals_ok") is False:
        score, sig = None, "HOLD"
        reasons.insert(0, ("neg", "No fundamentals available for this ticker, so there is no basis for a long-term verdict."))
    elif score is None:
        sig = "HOLD"
    elif score >= 65 and (mos is None or mos >= 0.10):
        sig = "BUY"
    elif score < 45 or (mos is not None and mos < -0.30):
        sig = "SELL"
    else:
        sig = "HOLD"
    return {"horizon": "Long term (3+ years)", "signal": sig, "score": score, "reliability": "Medium-High",
            "rule": "BUY: score >= 65 and price >= 10% below fair value. SELL/avoid: score < 45 or price > 30% above fair value. The fair-value tests only apply when its confidence is Medium or High.",
            "reasons": reasons, "levels": {"target": fv.get("value")}}


def medium_signal(info, tech, fv, p, bench_tech=None):
    score = weighted(p, WEIGHTS["medium"])
    reasons, price = [], tech["price"]
    t = tech["trend"]
    reasons.append(("pos" if t == "up" else "neg" if t == "down" else "neu",
                    {"up": "Uptrend: price above 200-day average and 50-day above 200-day", "down": "Downtrend: price and 50-day average both below 200-day",
                     "mixed": "Mixed trend (no clear direction)", "unknown": "Not enough history for trend"}[t]))
    if tech.get("ret_6m") is not None:
        reasons.append(("pos" if tech["ret_6m"] > .1 else "neg" if tech["ret_6m"] < -.05 else "neu", f"6-month return {_pct(tech['ret_6m'])}"))
        if bench_tech and bench_tech.get("ret_6m") is not None:
            rs = tech["ret_6m"] - bench_tech["ret_6m"]
            reasons.append(("pos" if rs > .03 else "neg" if rs < -.03 else "neu", f"{'Beating' if rs > 0 else 'Lagging'} Nifty by {_pct(abs(rs))} over 6 months"))
    if tech.get("from_high") is not None:
        reasons.append(("neu", f"{_pct(abs(tech['from_high']))} below 52-week high"))
    if info.get("earnings_growth") is not None:
        reasons.append(("pos" if info["earnings_growth"] > .1 else "neg" if info["earnings_growth"] < 0 else "neu", f"Earnings trend {_pct(info['earnings_growth'])}"))
    if score is None:
        sig = "HOLD"
    elif score >= 62 and t == "up":
        sig = "BUY"
    elif score < 42 or (t == "down" and (tech.get("ret_6m") or 0) < 0):
        sig = "SELL"
    else:
        sig = "HOLD"
    stop = price - 2.5 * tech["atr"] if tech.get("atr") else None
    return {"horizon": "Medium term (3-12 months)", "signal": sig, "score": score, "reliability": "Medium",
            "rule": "BUY: score >= 62 and uptrend. SELL/avoid: score < 42, or downtrend with negative 6-month return.",
            "reasons": reasons, "levels": {"stop": stop}}


def short_signal(info, tech):
    pts, reasons = 50.0, []
    r, h, hp, bb, t = tech.get("rsi"), tech.get("macd_hist"), tech.get("macd_hist_prev"), tech.get("bb_pos"), tech["trend"]
    if r is not None:
        d = 20 if r < 30 else 8 if r < 40 else -20 if r > 70 else -5 if r > 60 else 0
        pts += d
        reasons.append(("pos" if d > 0 else "neg" if d < 0 else "neu", f"RSI {r:.0f} " + ("- oversold" if r < 30 else "- overbought" if r > 70 else "- neutral zone")))
    if h is not None and hp is not None:
        if hp < 0 <= h:
            pts += 20; reasons.append(("pos", "MACD just crossed above its signal line (bullish)"))
        elif hp > 0 >= h:
            pts -= 20; reasons.append(("neg", "MACD just crossed below its signal line (bearish)"))
        elif h > 0 and h > hp:
            pts += 12; reasons.append(("pos", "MACD momentum positive and rising"))
        elif h < 0 and h < hp:
            pts -= 12; reasons.append(("neg", "MACD momentum negative and falling"))
        else:
            reasons.append(("neu", "MACD momentum fading"))
    if bb is not None:
        d = 10 if bb < .1 else -10 if bb > .9 else 0
        pts += d
        if d:
            reasons.append(("pos" if d > 0 else "neg", "Price at lower Bollinger band" if d > 0 else "Price stretched at upper Bollinger band"))
    pts += 8 if t == "up" else -8 if t == "down" else 0
    pts = float(np.clip(pts, 0, 100))
    sig = "BUY" if pts >= 65 else "SELL" if pts <= 35 else "HOLD"
    price = tech["price"]
    stop = price - 2 * tech["atr"] if tech.get("atr") else None
    return {"horizon": "Short term (days to weeks)", "signal": sig, "score": pts, "reliability": "Low",
            "rule": "Indicator points (RSI, MACD, Bollinger, trend). BUY >= 65, SELL <= 35. Short-term signals are noisy: treat as a timing hint for a stock you already like.",
            "reasons": reasons, "levels": {"stop": stop, "target": price + 3 * tech["atr"] if tech.get("atr") else None}}


def align_bench(df: pd.DataFrame, bench_df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Cut the benchmark at the instrument's last date: Yahoo often has the index a day ahead of NSE tickers,
    and comparing a 1-month return to one that includes an extra (possibly big) day is misleading."""
    if bench_df is None or df.empty:
        return bench_df
    b = bench_df[bench_df.index <= df.index[-1]]
    return b if len(b) > 0 else bench_df


def analyze_frame(df: pd.DataFrame, info: dict, bench_df: pd.DataFrame | None = None) -> dict:
    bench_df = align_bench(df, bench_df)
    tech = tech_snapshot(df)
    price = tech["price"]
    info = dict(info)
    info["price"] = price
    fv = fair_value(info, price)
    p = pillars(info, tech, fv)
    bench_tech = tech_snapshot(bench_df) if bench_df is not None and len(bench_df) > 130 else None
    sigs = {"long": long_signal(info, tech, fv, p), "medium": medium_signal(info, tech, fv, p, bench_tech), "short": short_signal(info, tech)}
    keys = ("name", "sector", "industry", "currency", "market_cap", "pe", "forward_pe", "pb", "roe", "roa", "debt_to_equity",
            "current_ratio", "profit_margin", "op_margin", "revenue_growth", "earnings_growth", "eps", "book_value",
            "dividend_yield", "beta", "target_mean", "target_high", "target_low", "analysts", "fundamentals_ok",
            "consensus", "consensus_mean", "next_earnings", "ps", "ev_ebitda", "peg", "insiders", "institutions", "derived", "fetched_at")
    return {"price": price, "change_1d": tech["change_1d"], "info": {k: info.get(k) for k in keys},
            "tech": tech, "fair_value": fv, "pillars": p,
            "overall": None if info.get("fundamentals_ok") is False else weighted(p, WEIGHTS["long"]), "signals": sigs}


# ------------------------------------------------------------------ ETFs (price-only analysis + portfolio role)
ETF_ROLES = {
    "core": ("CORE", "Core holding", "A broad-market index ETF is the baseline every stock pick must beat. Don't try to time it: invest regularly (SIP) and hold for years. "
                     "Pick the cheapest, most liquid ETF on the same index; expense ratio and tracking error are on the AMC factsheet (not in this data)."),
    "satellite": ("SATELLITE", "Satellite holding", "Concentrated in one sector, factor, region or asset, so it can lag the broad market for years. "
                               "Use it as a deliberate tilt on top of a core index fund, typically 5-15% of the portfolio each, and know why you own it."),
    "cash": ("CASH", "Cash parking", "Tracks overnight rates: returns roughly like a savings account, with near-zero volatility. "
                     "Useful to park money inside your demat account, not a growth investment."),
}


def cagr_years(close: pd.Series, years: int):
    """CAGR over the last N years if we have (nearly) that much history, else None."""
    n = int(years * 252)
    if len(close) < n * 0.95:
        return None
    c = close.iloc[-n:] if len(close) >= n else close
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return _f((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 and c.iloc[0] > 0 else None


def _returns_block(close: pd.Series) -> dict:
    def ret(n):
        return _f(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else None
    return {"1M": ret(21), "3M": ret(63), "6M": ret(126), "1Y": ret(252), "3Y CAGR": cagr_years(close, 3), "5Y CAGR": cagr_years(close, 5)}


def analyze_etf(df: pd.DataFrame, info: dict, bench_df: pd.DataFrame | None, meta: dict) -> dict:
    """ETFs have no company fundamentals, so judge them on what matters: returns vs the market, risk, liquidity and their role."""
    bench_df = align_bench(df, bench_df)
    tech = tech_snapshot(df)
    price, c = tech["price"], df["Close"]
    role_key = meta.get("role", "satellite")
    tag, role_name, role_text = ETF_ROLES.get(role_key, ETF_ROLES["satellite"])
    r = c.pct_change().dropna()
    last3 = r.iloc[-756:]
    c3 = c.iloc[-756:]
    cagr3 = cagr_years(c, 3)
    vol3 = _f(last3.std() * math.sqrt(252))
    risk = {"volatility": vol3, "max_drawdown_3y": _f((c3 / c3.cummax() - 1).min()),
            "sharpe_3y": _f((cagr3 - RF) / vol3) if (cagr3 is not None and vol3) else None}
    roll = (c / c.shift(252) - 1).dropna()
    if len(roll) > 50:
        risk["worst_1y"], risk["best_1y"] = _f(roll.min()), _f(roll.max())
    bench_tech, bench_returns = None, {}
    if bench_df is not None and len(bench_df) > 130:
        bench_tech = tech_snapshot(bench_df)
        bench_returns = _returns_block(bench_df["Close"])
        b = bench_df["Close"].pct_change().dropna()
        both = pd.concat([r, b], axis=1).dropna().iloc[-756:]
        if len(both) > 100:
            cov = np.cov(both.iloc[:, 0], both.iloc[:, 1])
            risk["beta"] = _f(cov[0, 1] / cov[1, 1]) if cov[1, 1] else None
            risk["correlation"] = _f(both.iloc[:, 0].corr(both.iloc[:, 1]))
    avg_vol = info.get("avg_volume")
    traded = _f(avg_vol * price) if (avg_vol and price) else None
    risk["avg_traded_value"] = traded
    risk["thin_liquidity"] = bool(traded is not None and traded < 1e7)       # under ~1 crore a day: wide spreads likely
    reasons = [("neu", f"{role_name}: tracks {meta.get('index') or 'its index'} ({meta.get('category', 'ETF')})")]
    if cagr3 is not None and bench_returns.get("3Y CAGR") is not None:
        d = cagr3 - bench_returns["3Y CAGR"]
        reasons.append(("pos" if d > .02 else "neg" if d < -.02 else "neu", f"3-year CAGR {_pct(cagr3)} vs Nifty 50 {_pct(bench_returns['3Y CAGR'])}"))
    if risk.get("max_drawdown_3y") is not None:
        reasons.append(("neg" if risk["max_drawdown_3y"] < -.3 else "neu", f"Worst fall in 3 years: {_pct(risk['max_drawdown_3y'], 0)}"))
    if risk["thin_liquidity"]:
        reasons.append(("neg", "Thinly traded: use limit orders and expect wider bid-ask spreads"))
    elif traded:
        reasons.append(("pos", "Liquid: healthy daily traded value"))
    if info.get("pe"):
        reasons.append(("neu", f"Underlying index P/E about {info['pe']:.1f}"))
    long = {"horizon": "Long term (3+ years)", "signal": tag, "score": None, "reliability": "n/a",
            "rule": role_text, "reasons": reasons, "levels": {}}
    pillars = {"Momentum": avg([scale((price / tech["sma200"] - 1) if (price and tech["sma200"]) else None, -0.15, 0.20),
                                scale(tech.get("ret_6m"), -0.15, 0.35), scale(tech.get("ret_1y"), -0.15, 0.50)])}
    fv = {"value": None, "upside": None, "mos": None, "models": {}, "confidence": "None", "notes": [], "growth_assumed": None}
    sigs = {"long": long, "medium": medium_signal(info, tech, fv, pillars, bench_tech), "short": short_signal(info, tech)}
    sigs["medium"]["rule"] = "Momentum-only score (vs 200-day average, 6-month and 1-year return). BUY: score >= 62 and uptrend. SELL/avoid: score < 42, or downtrend with negative 6-month return. For a core ETF, treat SELL as 'pause lump sums', not 'stop your SIP'."
    return {"kind": "etf", "price": price, "change_1d": tech["change_1d"],
            "info": {"name": info.get("name"), "sector": meta.get("category"), "category": meta.get("category"), "index": meta.get("index"),
                     "role": role_key, "role_name": role_name, "currency": info.get("currency") or "INR", "pe": info.get("pe"),
                     "avg_volume": avg_vol, "dividend_yield": info.get("dividend_yield")},
            "tech": tech, "fair_value": fv, "pillars": pillars, "overall": None,
            "returns": _returns_block(c), "bench_returns": bench_returns, "risk": risk, "signals": sigs}


# ------------------------------------------------------------------ financial statement trends
def _cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _year_ago(label: str) -> str:
    mon, yr = label.split()
    return f"{mon} {int(yr) - 1}"


def financial_takeaways(fin: dict, sector: str = "") -> list[tuple[str, str]]:
    """Plain-English readings of the multi-year statements: (tone, text) pairs like the signal reasons."""
    a = fin.get("annual") or {}
    periods = a.get("periods") or []
    n, out = len(periods), []
    if n < 2:
        return [("neu", "Not enough statement history to judge trends.")]

    def ser(k):
        v = a.get(k) or []
        return list(v) + [None] * (n - len(v))

    rev, ni, fcf, debt, eq, ocf = (ser(k) for k in ("revenue", "net_income", "free_cash_flow", "total_debt", "equity", "operating_cash_flow"))

    def ups(s):
        pairs = [(s[i - 1], s[i]) for i in range(1, n) if s[i - 1] is not None and s[i] is not None]
        return sum(1 for x, y in pairs if y > x), len(pairs)

    def trend_line(label, s):
        u, m = ups(s)
        if not m:
            return
        c = _cagr(s[0], s[-1], n - 1)
        tone = "pos" if (c or 0) >= .10 and u >= m - 1 else "neg" if (c is not None and c < 0) or u <= m // 2 else "neu"
        out.append((tone, f"{label} grew in {u} of {m} years" + (f" ({_pct(c, 0)}/yr)" if c is not None else "")))

    trend_line("Revenue", rev)
    losses = sum(1 for x in ni if x is not None and x < 0)
    if losses:
        out.append(("neg", f"Loss-making in {losses} of {n} years"))
    else:
        trend_line("Net profit", ni)
    if rev[0] and rev[-1] and ni[0] is not None and ni[-1] is not None:
        m0, m1 = ni[0] / rev[0], ni[-1] / rev[-1]
        d = m1 - m0
        out.append(("pos" if d > .02 else "neg" if d < -.02 else "neu",
                    f"Net margin {_pct(m0)} -> {_pct(m1)} ({'expanding' if d > .02 else 'shrinking' if d < -.02 else 'stable'})"))
    if sector not in FINANCIALS:
        vals = [x for x in fcf if x is not None]
        if vals:
            pos = sum(1 for x in vals if x > 0)
            out.append(("pos" if pos == len(vals) else "neg" if pos <= len(vals) / 2 else "neu",
                        f"Free cash flow positive in {pos} of {len(vals)} years"))
        if debt[0] is not None and debt[-1] is not None and eq[0] and eq[-1] and eq[0] > 0 and eq[-1] > 0:
            de0, de1 = debt[0] / eq[0], debt[-1] / eq[-1]
            out.append(("pos" if de1 < de0 - .1 else "neg" if de1 > de0 + .2 else "neu",
                        f"Debt/equity {de0:.2f} -> {de1:.2f} ({'deleveraging' if de1 < de0 - .1 else 'rising debt' if de1 > de0 + .2 else 'steady'})"))
        pairs = [(ocf[i], ni[i]) for i in range(n) if ocf[i] is not None and ni[i] is not None and ni[i] > 0]
        if len(pairs) >= 2:
            ratio = sum(o for o, _ in pairs) / sum(i for _, i in pairs)
            out.append(("pos" if ratio >= .8 else "neg" if ratio < .5 else "neu",
                        f"Operating cash flow is {ratio * 100:.0f}% of reported profit" +
                        (" - profits convert well to cash" if ratio >= .8 else " - weak cash conversion; check receivables and accounting" if ratio < .5 else "")))
    q = fin.get("quarterly") or {}
    qp = q.get("periods") or []
    if qp and q.get("revenue"):
        last, want = qp[-1], _year_ago(qp[-1])
        if want in qp:
            i, j = qp.index(want), len(qp) - 1
            for label, key in (("revenue", "revenue"), ("net profit", "net_income")):
                s = q.get(key) or []
                if len(s) > j and s[i] and s[j] is not None and s[i] > 0:
                    g = s[j] / s[i] - 1
                    out.append(("pos" if g >= .10 else "neg" if g < 0 else "neu", f"Latest quarter ({last}) {label} {'+' if g >= 0 else ''}{_pct(g, 0)} vs a year ago"))
    return out


# ------------------------------------------------------------------ series for charts
def downsample(series_list, max_pts=600):
    n = len(series_list[0])
    step = max(1, n // max_pts)
    return [s[::step] for s in series_list]


def chart_series(df: pd.DataFrame, days: int = 504) -> dict:
    c = df["Close"]
    full = pd.DataFrame({"c": c, "s50": sma(c, 50), "s200": sma(c, 200), "rsi": rsi(c)})
    d = full.iloc[-days:]
    out = {"dates": [x.strftime("%Y-%m-%d") for x in d.index]}
    for k in ("c", "s50", "s200", "rsi"):
        out[k] = [None if pd.isna(v) else round(float(v), 2) for v in d[k]]
    return out


# ------------------------------------------------------------------ backtests
def metrics_from_equity(eq: pd.Series) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    vol = r.std() * math.sqrt(252)
    return {"total_return": _f(eq.iloc[-1] / eq.iloc[0] - 1), "cagr": _f(cagr), "volatility": _f(vol),
            "sharpe": _f((cagr - RF) / vol) if vol else None, "max_drawdown": _f((eq / eq.cummax() - 1).min()), "years": _f(yrs)}


def _series_out(index, **cols):
    step = max(1, len(index) // 500)
    out = {"dates": [d.strftime("%Y-%m-%d") for d in index[::step]]}
    for k, s in cols.items():
        out[k] = [round(float(v), 4) for v in s.iloc[::step]]
    return out


def backtest_trend(df: pd.DataFrame, bench_df: pd.DataFrame | None = None, cost: float = COST) -> dict:
    """Own-stock rule: hold while price > 200DMA and 50DMA > 200DMA, otherwise sit in cash."""
    c = df["Close"]
    s50, s200 = sma(c, 50), sma(c, 200)
    pos = ((c > s200) & (s50 > s200)).shift(1, fill_value=False).astype(float)
    ret = c.pct_change().fillna(0)
    trades = pos.diff().abs().fillna(0)
    strat = pos * ret - trades * cost / 2
    start = s200.first_valid_index()
    if start is None:
        return {"error": "Need at least ~1 year of history for the 200-day rule."}
    strat, ret, pos = strat[start:], ret[start:], pos[start:]
    eq, bh = (1 + strat).cumprod(), (1 + ret).cumprod()
    out = {"strategy": metrics_from_equity(eq), "buy_hold": metrics_from_equity(bh),
           "trades": int(trades[start:].sum() // 2), "time_invested": _f(pos.mean())}
    cols = {"strategy": eq, "buy_hold": bh}
    if bench_df is not None:
        b = bench_df["Close"].reindex(eq.index).ffill().bfill()
        be = b / b.iloc[0]
        out["nifty"] = metrics_from_equity(be)
        cols["nifty"] = be
    out["series"] = _series_out(eq.index, **cols)
    return out


def backtest_momentum(closes: pd.DataFrame, bench: pd.Series, top_n=10, lookback_m=6, skip_m=1, cost=COST) -> dict:
    """Monthly rebalanced, equal-weight top-N momentum portfolio vs Nifty and vs equal-weight universe.
    CAVEAT: uses today's index members (survivorship bias) -> results are flattering."""
    closes = closes.dropna(how="all").ffill()
    bench = bench.reindex(closes.index).ffill()
    daily = closes.pct_change().fillna(0)
    lb, sk = lookback_m * 21, skip_m * 21
    if len(closes) < lb + sk + 60:
        return {"error": "Not enough history for this lookback."}
    rebal = set(closes.index.to_series().groupby(closes.index.to_period("M")).last().values)
    w = pd.Series(0.0, index=closes.columns)
    pending, started, holdings = 0.0, None, []
    strat, dates = [], []
    for i, d in enumerate(closes.index):
        if i < lb + sk:
            continue
        if started is None:
            started = i
        r = float((w * daily.iloc[i]).sum()) - pending
        pending = 0.0
        strat.append(r)
        dates.append(d)
        if d in rebal:
            mom = (closes.iloc[i - sk] / closes.iloc[i - sk - lb] - 1).dropna()
            top = list(mom.nlargest(top_n).index)
            new = pd.Series(0.0, index=closes.columns)
            new[top] = 1 / len(top)
            pending = float((new - w).abs().sum()) * cost / 2
            w, holdings = new, top
    idx = pd.DatetimeIndex(dates)
    eq = (1 + pd.Series(strat, index=idx)).cumprod()
    be = bench.reindex(idx) / bench.reindex(idx).iloc[0]
    ew = (1 + daily.loc[idx].mean(axis=1)).cumprod()
    return {"strategy": metrics_from_equity(eq), "nifty": metrics_from_equity(be), "equal_weight": metrics_from_equity(ew),
            "holdings_now": holdings, "series": _series_out(idx, strategy=eq, nifty=be, equal_weight=ew),
            "params": {"top_n": top_n, "lookback_months": lookback_m, "skip_months": skip_m, "cost": cost},
            "caveats": ["Survivorship bias: tests today's index members, so past losers are missing and results look better than reality.",
                        "Prices only: fundamentals-based rules can't be fairly backtested with free data (no point-in-time financials).",
                        f"Assumes {cost * 100:.1f}% round-trip trading cost; taxes not included.",
                        "Past performance does not predict future results."]}
