import json, os, math, time
os.environ["STOCKPRO_MODE"] = "demo"
os.environ["STOCKPRO_HOME"] = "/tmp/stockpro_test_home"
os.makedirs("/tmp/stockpro_test_home", exist_ok=True)
# A small universe keeps the suite fast; the bundled Nifty 500 file is tested separately.
open("/tmp/stockpro_test_home/universe.csv", "w").write("symbol,name,sector,industry,group\n" + "\n".join(
    f"{s},{n},{sec},{ind},{g}" for s, n, sec, ind, g in [
        ("TCS", "Tata Consultancy Services", "Technology", "Information Technology", "Nifty 50"), ("INFY", "Infosys", "Technology", "Information Technology", "Nifty 50"),
        ("WIPRO", "Wipro", "Technology", "Information Technology", "Nifty 50"), ("HCLTECH", "HCL Technologies", "Technology", "Information Technology", "Nifty 50"),
        ("HDFCBANK", "HDFC Bank", "Financial Services", "Financial Services", "Nifty 50"), ("ICICIBANK", "ICICI Bank", "Financial Services", "Financial Services", "Nifty 50"),
        ("RELIANCE", "Reliance Industries", "Energy", "Oil Gas & Consumable Fuels", "Nifty 50"), ("ITC", "ITC", "Consumer Defensive", "Fast Moving Consumer Goods", "Nifty 50"),
        ("SUNPHARMA", "Sun Pharma", "Healthcare", "Healthcare", "Nifty 50"), ("LT", "Larsen & Toubro", "Industrials", "Construction", "Nifty 50"),
        ("PERSISTENT", "Persistent Systems", "Technology", "Information Technology", "Nifty Next 50"), ("CDSL", "Central Depository Services", "Financial Services", "Financial Services", "Midcap 150")]))
import numpy as np, pandas as pd
from fastapi.testclient import TestClient
from app import analysis, data, universe
from app.main import app

c = TestClient(app)


def screen(path, timeout=90):
    """Screeners run in the background: poll like the frontend does until the job is finished."""
    t0 = time.time()
    while True:
        d = c.get(path).json()
        if not d["progress"]["running"]:
            return d
        assert time.time() - t0 < timeout, "screener job did not finish"
        path = path.split("?")[0]
        time.sleep(0.2)


def test_scale_and_inverse():
    assert analysis.scale(0.25, 0.05, 0.25) == 100
    assert analysis.scale(12, 45, 12) == 100 and analysis.scale(45, 45, 12) == 0
    assert analysis.scale(None, 0, 1) is None


def test_rsi_bounds_and_trend_series():
    s = pd.Series(np.linspace(100, 200, 300))
    assert analysis.rsi(s).iloc[-1] > 95           # pure uptrend -> RSI near 100
    assert analysis.rsi(s[::-1].reset_index(drop=True)).iloc[-1] < 5


def test_dcf_monotonic_in_growth():
    lo = analysis.dcf_per_share(1e9, 1e8, 0.03)
    hi = analysis.dcf_per_share(1e9, 1e8, 0.15)
    assert hi > lo > 0


def test_fair_value_skips_dcf_for_banks_and_handles_missing():
    info = {"sector": "Financial Services", "eps": 50, "book_value": 400, "fcf": 1e9, "shares": 1e8}
    fv = analysis.fair_value(info, 1000)
    assert not any("DCF" in k for k in fv["models"])
    assert analysis.fair_value({}, 100)["value"] is None


def test_signals_are_valid_everywhere():
    for r in screen("/api/screener")["rows"]:
        for h in ("long", "medium", "short"):
            assert r["signals"][h]["signal"] in {"BUY", "HOLD", "SELL"}
        assert r["score"] is None or 0 <= r["score"] <= 100


def test_strong_vs_weak_company():
    df = data._demo_history("X", 5)
    good = dict(sector="Technology", roe=.3, profit_margin=.25, revenue_growth=.2, earnings_growth=.25, debt_to_equity=.1,
                current_ratio=2.5, fcf=5e9, shares=1e8, eps=price_eps(df, 15), book_value=price_bv(df), pe=15, pb=2)
    bad = dict(sector="Technology", roe=.02, profit_margin=.01, revenue_growth=-.1, earnings_growth=-.2, debt_to_equity=2.5,
               current_ratio=.6, fcf=-1e9, shares=1e8, eps=price_eps(df, 80), book_value=price_bv(df), pe=80, pb=9)
    assert analysis.analyze_frame(df, good)["overall"] > analysis.analyze_frame(df, bad)["overall"] + 30
    assert analysis.analyze_frame(df, bad)["signals"]["long"]["signal"] == "SELL"


def price_eps(df, pe): return float(df.Close.iloc[-1]) / pe
def price_bv(df): return float(df.Close.iloc[-1]) / 3


def test_backtest_trend_and_momentum_metrics():
    df = data._demo_history("TCS.NS", 8)
    b = analysis.backtest_trend(df, data._demo_history(data.BENCH, 8))
    assert b["trades"] >= 0 and -1 <= b["strategy"]["max_drawdown"] <= 0 and len(b["series"]["dates"]) > 100
    r = c.get("/api/backtest/momentum?top_n=8&lookback_months=6").json()
    assert r["strategy"]["cagr"] is not None and len(r["holdings_now"]) == 8 and r["caveats"]


def test_no_lookahead_in_trend_rule():
    # price jumps on the last day: yesterday's position must not capture it
    c_ = pd.Series(np.r_[np.full(300, 100.0), 150.0], index=pd.bdate_range("2024-01-01", periods=301))
    df = pd.DataFrame({"Close": c_, "High": c_, "Low": c_, "Open": c_, "Volume": 1.0})
    b = analysis.backtest_trend(df)
    assert b["strategy"]["total_return"] <= 0.0001  # was in cash the day before the jump


def test_stock_endpoint_and_errors():
    j = c.get("/api/stock/TCS").json()
    assert j["symbol"] == "TCS.NS" and j["chart"]["dates"] and set(j["signals"]) == {"long", "medium", "short"}
    assert c.get("/api/stock/this-is-not-real").status_code in (200, 404)  # demo mode synthesises; live returns 404


def test_watchlist_and_portfolio_roundtrip():
    c.post("/api/watchlist/INFY"); assert "INFY.NS" in c.get("/api/watchlist").json()[0]["symbol"]
    c.delete("/api/watchlist/INFY")
    assert c.get("/api/watchlist").json() == []
    assert c.post("/api/portfolio", json={"symbol": "x", "qty": -1, "price": 1, "date": "2024-01-01"}).status_code == 400
    h = c.post("/api/portfolio", json={"symbol": "TCS", "qty": 10, "price": 3000, "date": "2024-01-15", "note": "t"}).json()
    p = c.get("/api/portfolio").json()
    assert p["totals"]["invested"] == 30000 and p["holdings"][0]["nifty_pct"] is not None and p["warnings"]
    c.delete("/api/portfolio/" + h["id"])
    assert c.get("/api/portfolio").json()["holdings"] == []


def test_mutual_funds():
    f = c.get("/api/mf/search?q=nifty").json(); assert len(f) >= 2
    r = c.get("/api/mf/compare?codes=900001,900003").json()
    idx = r["funds"][0]
    assert idx["is_index_fund"] and idx["tracking_error_3y"] is not None and idx["tracking_error_3y"] < r["funds"][1]["volatility_3y"]
    s = c.get("/api/mf/sip?code=900001&monthly=10000&years=3").json()
    assert s["invested"] == 10000 * s["months"] and s["xirr"] is not None


def test_json_has_no_nan():
    import json
    txt = c.get("/api/stock/RELIANCE").text
    assert "NaN" not in txt and "Infinity" not in txt
    json.loads(txt)


def test_low_confidence_fair_value_does_not_force_sell():
    p = {k: 55.0 for k in analysis.WEIGHTS["long"]}        # a middling score: HOLD territory on its own
    tech = {"price": 100.0}
    low = {"value": 50.0, "upside": -0.5, "mos": -1.0, "confidence": "Low", "models": {}}
    assert analysis.long_signal({}, tech, low, p)["signal"] == "HOLD"
    assert analysis.long_signal({}, tech, {**low, "confidence": "High"}, p)["signal"] == "SELL"
    good = {"value": 130.0, "upside": 0.3, "mos": 0.23, "confidence": "Medium", "models": {}}
    assert analysis.long_signal({}, tech, good, {k: 70.0 for k in p})["signal"] == "BUY"


def test_financial_takeaways_detect_trends():
    fin = {"annual": {"periods": ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"],
                      "revenue": [100, 112, 125, 140, 160], "net_income": [10, 12, 14, 17, 21],
                      "free_cash_flow": [5, 6, 7, 8, 9], "total_debt": [50, 45, 40, 30, 20], "equity": [100, 110, 120, 135, 150],
                      "operating_cash_flow": [11, 13, 15, 18, 22]},
           "quarterly": {"periods": ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026", "Sep 2026"],
                         "revenue": [40, 41, 42, 43, 48], "net_income": [5, 5, 5, 5, 6]}}
    t = analysis.financial_takeaways(fin, "Technology")
    txt = " | ".join(x for _, x in t)
    assert "Revenue grew in 4 of 4 years" in txt and "Free cash flow positive in 5 of 5" in txt and "deleveraging" in txt
    assert "Latest quarter (Sep 2026) revenue +20% vs a year ago" in txt
    assert all(tone == "pos" for tone, x in t if x.startswith(("Revenue", "Net profit", "Free cash", "Debt/equity")))
    bad = {"annual": {"periods": ["Mar 2024", "Mar 2025", "Mar 2026"], "revenue": [100, 90, 80], "net_income": [5, -2, -8]}}
    tb = analysis.financial_takeaways(bad, "Technology")
    assert any(tone == "neg" and "Loss-making in 2 of 3" in x for tone, x in tb)
    assert analysis.financial_takeaways({"annual": {"periods": ["Mar 2026"]}})[0][0] == "neu"


def test_financials_endpoint_demo():
    f = c.get("/api/financials/TCS").json()
    assert len(f["annual"]["periods"]) == 5 and len(f["annual"]["revenue"]) == 5 and f["takeaways"]
    assert len(f["quarterly"]["periods"]) == 5 and f["currency"] == "INR"
    bank = c.get("/api/financials/HDFCBANK").json()
    assert not any("Free cash flow" in x for _, x in bank["takeaways"])   # FCF rules skipped for financials


def test_peers_endpoint():
    p = c.get("/api/peers/TCS").json()
    assert p["sector"] == "Technology" and p["peers"] and all(r["sector"] == "Technology" for r in p["peers"])
    assert "TCS.NS" not in [r["symbol"] for r in p["peers"]] and p["self"]["symbol"] == "TCS.NS" and p["pool"] == 4
    assert p["median"]["pe"] is not None and "pb" in p["peers"][0]


def test_screener_records_signal_history():
    s = screen("/api/screener?refresh=true")
    assert "changes" in s and isinstance(s["changes"], list) and len(s["rows"]) == 12 and not s["failed"]
    f = data.HOME / f"signal_history_{data.mode()}.json"
    assert f.exists() and len(json.loads(f.read_text())["last"]) == len(s["rows"])
    assert isinstance(c.get("/api/stock/TCS").json()["history"], list)


def test_invalidate_clears_disk_cache():
    data.get_history("INFY.NS")
    assert any(data.CACHE_DIR.glob("hist_*_INFY.NS_*.pkl"))
    assert data.invalidate(["INFY.NS"]) >= 1
    assert not any(data.CACHE_DIR.glob("hist_*_INFY.NS_*.pkl"))


def test_portfolio_rejects_future_date():
    assert c.post("/api/portfolio", json={"symbol": "TCS", "qty": 1, "price": 1, "date": "2099-01-01"}).status_code == 400
    assert c.get("/api/stock/TCS?days=5").status_code == 422


def test_etf_universe_and_search():
    from app import universe
    etfs = universe.load_etfs()
    assert len(etfs) >= 30 and all(e["symbol"].endswith(".NS") and e["role"] in ("core", "satellite", "cash") for e in etfs)
    assert universe.lookup_etf("NIFTYBEES.NS")["category"].startswith("Broad market")
    hits = c.get("/api/search?q=gold").json()
    assert any(h["symbol"] == "GOLDBEES.NS" and h.get("kind") == "etf" for h in hits)


def test_etf_detail_page_shape():
    j = c.get("/api/stock/NIFTYBEES").json()
    assert j["kind"] == "etf" and j["signals"]["long"]["signal"] == "CORE" and j["signals"]["long"]["score"] is None
    assert set(j["returns"]) == {"1M", "3M", "6M", "1Y", "3Y CAGR", "5Y CAGR"} and j["returns"]["3Y CAGR"] is not None
    assert j["bench_returns"]["1Y"] is not None and j["risk"]["beta"] is not None and -1 <= j["risk"]["correlation"] <= 1
    assert j["signals"]["medium"]["signal"] in {"BUY", "HOLD", "SELL"} and j["fair_value"]["value"] is None and j["chart"]["dates"]
    assert c.get("/api/stock/GOLDBEES").json()["signals"]["long"]["signal"] == "SATELLITE"
    assert c.get("/api/stock/LIQUIDBEES").json()["signals"]["long"]["signal"] == "CASH"
    f = c.get("/api/financials/NIFTYBEES").json()
    assert f["error"] and not f["annual"]["periods"]


def test_etf_screener_and_peers():
    e = screen("/api/etfs")
    assert len(e["rows"]) >= 30 and not e["failed"] and "Commodity" in e["categories"]
    r = next(x for x in e["rows"] if x["symbol"] == "NIFTYBEES.NS")
    assert r["kind"] == "etf" and r["role"] == "core" and r["vs_nifty_1y"] is not None and r["avg_traded_value"] is not None
    p = c.get("/api/peers/GOLDBEES").json()
    assert p["kind"] == "etf" and p["sector"] == "Commodity" and all(x["category"] == "Commodity" for x in p["peers"]) and p["peers"]


def test_etf_in_watchlist_and_portfolio():
    c.post("/api/watchlist/NIFTYBEES")
    w = c.get("/api/watchlist").json()
    assert any(x["symbol"] == "NIFTYBEES.NS" and x["signals"]["long"]["signal"] == "CORE" for x in w)
    c.delete("/api/watchlist/NIFTYBEES")
    h = c.post("/api/portfolio", json={"symbol": "GOLDBEES", "qty": 100, "price": 50, "date": "2024-01-15"}).json()
    p = c.get("/api/portfolio").json()
    assert p["holdings"][0]["sector"] == "Commodity" and p["holdings"][0]["signals"]["long"] == "SATELLITE"
    c.delete("/api/portfolio/" + h["id"])


def test_benchmark_is_aligned_to_instrument_last_date():
    df = data._demo_history("TCS.NS", 5)
    bench = data._demo_history(data.BENCH, 5)
    stale = df.iloc[:-3]                       # instrument lags the index by three sessions
    b = analysis.align_bench(stale, bench)
    assert b.index[-1] == stale.index[-1]
    a = analysis.analyze_etf(stale, {"name": "x", "avg_volume": 1e6}, bench, {"role": "core", "index": "Nifty 50", "category": "Broad"})
    expect = float(bench["Close"][:stale.index[-1]].iloc[-1] / bench["Close"][:stale.index[-1]].iloc[-22] - 1)
    assert abs(a["bench_returns"]["1M"] - expect) < 1e-9


def test_bundled_nifty500_universe():
    rows = universe._read_csv(universe.BUNDLED)
    syms = {r["symbol"] for r in rows}
    assert len(rows) >= 480 and {"RELIANCE", "TCS", "HDFCBANK", "PERSISTENT"} <= syms
    groups = {r["group"] for r in rows}
    assert groups == set(universe.GROUPS)
    assert all(r["sector"] and r["industry"] for r in rows)
    # the test override is active, so the app itself sees the small universe
    assert c.get("/api/status").json()["universe_size"] == 12 and universe.groups() == ["Nifty 50", "Nifty Next 50", "Midcap 150"]


def test_screener_reports_progress_and_group():
    d = screen("/api/screener")
    assert d["progress"] == {"done": 12, "total": 12, "running": False}
    r = next(x for x in d["rows"] if x["symbol"] == "CDSL.NS")
    assert r["group"] == "Midcap 150" and r["industry"] == "Financial Services" and "dividend_yield" in r and "fv_confidence" in r
    assert c.get("/api/stock/CDSL").json()["group"] == "Midcap 150"


def test_fill_from_statements_derives_missing_ratios():
    fin = {"annual": {"periods": ["Mar 2024", "Mar 2025"], "revenue": [1000.0, 1200.0], "net_income": [100.0, 150.0], "operating_income": [150.0, 200.0],
                      "equity": [800.0, 1000.0], "total_debt": [300.0, 250.0], "current_assets": [400.0, 450.0], "current_liabilities": [200.0, 300.0],
                      "operating_cash_flow": [180.0, 210.0], "capex": [-60.0, -70.0], "shares_out": [10.0, 10.0]},
           "quarterly": {"periods": ["Jun 2024", "Sep 2024", "Dec 2024", "Mar 2025"], "revenue": [280.0, 300.0, 310.0, 330.0], "net_income": [30.0, 40.0, 35.0, 45.0]}}
    info = {"sector": "Technology", "price": 500.0, "pe": None, "roe": None, "profit_margin": None, "debt_to_equity": 0.3, "fundamentals_ok": False}
    out = data.fill_from_statements(info, fin)
    assert out["roe"] == 150 / 1000 and out["profit_margin"] == 150 / 1220 and out["debt_to_equity"] == 0.3    # existing value kept
    assert out["current_ratio"] == 1.5 and out["fcf"] == 140.0 and out["eps"] == 15.0 and out["book_value"] == 100.0
    assert abs(out["revenue_growth"] - 0.2) < 1e-9 and abs(out["earnings_growth"] - 0.5) < 1e-9
    assert out["pe"] == 500 / 15 and out["fundamentals_ok"] is True and "roe" in out["derived"] and "debt_to_equity" not in out["derived"]
    bank = data.fill_from_statements({"sector": "Financial Services", "price": 100.0}, fin)
    assert bank.get("debt_to_equity") is None and bank.get("fcf") is None and bank["roe"] == 0.15
    assert data.fill_from_statements({"roe": None}, {"annual": {"periods": []}}) == {"roe": None}


def test_extras_endpoint_demo():
    x = c.get("/api/extras/TCS").json()
    assert x["kind"] == "stock" and x["earnings"]["next_date"] and len(x["earnings"]["history"]) == 6
    assert x["dividends"]["yield"] > 0 and len(x["dividends"]["history"]) == 5 and x["news"]
    assert x["consensus"]["key"] in {"strong_buy", "buy", "hold", "underperform"} and 0 < x["holders"]["insiders"] < 1
    assert c.get("/api/extras/GOLDBEES").json()["kind"] == "etf"
    assert "Street view" in json.dumps(c.get("/api/stock/TCS").json()["signals"]["long"]["reasons"])


def test_momentum_backtest_group_param():
    b = c.get("/api/backtest/momentum?top_n=3&lookback_months=3&group=Nifty%2050").json()
    assert b["group"] == "Nifty 50" and b["universe_size"] == 10 and b["groups"][0] == "Nifty 50"
    assert c.get("/api/backtest/momentum?top_n=3&lookback_months=3&group=nonsense").json()["group"] == "Nifty 50"


def test_retry_backs_off_on_rate_limit(monkeypatch):
    class YFRateLimitError(Exception):
        pass
    sleeps, calls = [], []
    monkeypatch.setattr(data.time, "sleep", lambda t: sleeps.append(t))
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise YFRateLimitError("Too Many Requests")
        return "ok"
    assert data._retry(flaky) == "ok" and sleeps == [4, 12]
    def other():
        raise ValueError("bad symbol")
    try:
        data._retry(other)
        assert False
    except ValueError:
        pass
    assert data.rate_limited(YFRateLimitError("x")) and not data.rate_limited(ValueError("x"))


def test_price_only_stock_gets_hold_not_buy():
    df = data.get_history("TCS.NS")
    a = analysis.analyze_frame(df, {"name": "X", "fundamentals_ok": False, "price": float(df["Close"].iloc[-1])})
    assert a["overall"] is None and a["signals"]["long"]["signal"] == "HOLD" and a["signals"]["long"]["score"] is None
    assert "No fundamentals" in a["signals"]["long"]["reasons"][0][1]
