# StockPro: personal stock research app

An InvestingPro-style research dashboard for your own use. Runs locally, stores data only on your computer.

## Run
```bash
pip install -r requirements.txt
python run.py            # opens http://127.0.0.1:8000
python run.py --reload   # same, but picks up code changes without a restart
```
Needs internet for live data (Yahoo Finance via `yfinance`, AMFI NAVs via mfapi.in). No API keys.
No internet? `STOCKPRO_MODE=demo python run.py` uses clearly-labelled synthetic data.
Docker: `docker build -t stockpro . && docker run -p 127.0.0.1:8000:8000 -v stockpro:/data stockpro`

## Features
- **Screener**: the **Nifty 500** (official NSE constituent list, bundled with index group: Nifty 50 / Next 50 / Midcap 150 / Smallcap 250). Runs as a background job with a progress bar (first live run a few minutes, then cached). Preset screens (quality compounders, below fair value, momentum leaders, dividend payers, beaten down but healthy), filter by index group, sector, NSE industry, signal, P/E, ROE, score, name; top ideas and weakest names; **signal changes** (every run is compared with the previous one, so HOLD → BUY flips are listed); CSV export; "Refresh data" refetches from the source
- **Stock page**: Buy/Hold/Sell signals for long (3y+), medium (3-12m) and short (days-weeks) horizons, each with the reasons and the rule; fair value (Graham, growth-adjusted P/E, DCF; only drives the signal when the models agree); health score (5 pillars); **Street view** (analyst consensus and target range, promoter/institutional holding, P/S, EV/EBITDA, PEG); **earnings record** (next results date, EPS estimate vs reported for recent quarters); **dividend record** (yield, years paid, payout growth); latest **news** headlines; **5-year financials** (revenue, profit, margins, cash flow, debt, plus recent quarters) with plain-English trend takeaways; **peers** (same Yahoo industry when there are enough, else same sector) with medians; price chart with 50/200-day averages and RSI; ATR stop-loss; per-stock trend-rule backtest vs buy&hold and Nifty
- **Data quality**: where Yahoo has no ROE, margin, debt/equity, current ratio, free cash flow, EPS, book value or growth figure, the app rebuilds it from the financial statements and the stock page says which numbers were rebuilt and when the fundamentals were fetched
- **ETFs**: ~37 NSE-listed index, factor, sector, international and commodity ETFs. ETF screener (returns, vs Nifty, 3y CAGR, volatility, drawdown, liquidity, trend signals) and an ETF page with returns vs Nifty 50, risk/liquidity, role (core / satellite / cash), similar ETFs and the trend-rule backtest. Any ETF can go in the watchlist and portfolio
- **Watchlist**, **Portfolio + decision journal** (your return vs the same money in Nifty 50, concentration warnings)
- **Momentum backtest** over an index group (Nifty 50 by default) vs Nifty (with caveats)
- **Mutual funds**: compare any AMFI fund: CAGR, volatility, drawdown, Sharpe, tracking error, rolling returns, SIP/XIRR calculator
- **Learn** tab: glossary and investing habits
- **Light / dark mode**: toggle in the header (follows your OS setting until you choose; `?theme=light` or `?theme=dark` in the URL also works)

## Customise
- Universe: the bundled `app/nifty500.csv` (`symbol,name,sector,industry,group`) comes from the NSE index constituent files at niftyindices.com. Index membership changes, so refresh it now and then, or create `~/.stockpro/universe.csv` with the same columns (`industry` and `group` optional, no `.NS`) to use your own list.
- ETF list: create `~/.stockpro/etfs.csv` with `symbol,name,category,index,role` (role = core | satellite | cash). Yahoo tags Indian ETFs as ordinary shares, so this list is how the app knows a ticker is an ETF.
- Any ticker works in the search box: `TCS`, `RELIANCE.BO`, `us:AAPL`.
- Scoring rules and thresholds live in `app/analysis.py`. They are plain, readable Python.
- Data cache: `~/.stockpro/cache` (prices 6h, fundamentals and statements 24h, earnings/dividends/news 6h). Set `STOCKPRO_HOME` to move it. Watchlist, portfolio and signal history are JSON files in the same folder.

## Layout
`app/main.py` (API + persistence), `app/analysis.py` (indicators, fair value, signals, backtests, statement takeaways), `app/data.py` (Yahoo/demo data + cache), `app/universe.py` + `app/nifty500.csv` (stock list), `app/mf.py` (mutual funds), `app/static/` (single-page frontend). Tests: `pytest -q`.

## Known limits
Free data can be delayed, missing or wrong (especially Yahoo fundamentals for Indian stocks). Fair value is assumption-driven. Fundamental rules can't be backtested without point-in-time data. Backtests have survivorship bias, exclude tax, and past results don't predict the future. The app has no login: keep it bound to 127.0.0.1. Not investment advice.
