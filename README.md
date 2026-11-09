# StockPro: personal stock research app

An InvestingPro-style research dashboard for your own use. Runs locally and stores data only on your computer, or, with a Supabase project attached, behind a login with each user's watchlist and portfolio kept in Supabase.

## Run
```bash
pip install -r requirements.txt
python run.py            # opens http://127.0.0.1:8000
python run.py --reload   # same, but picks up code changes without a restart
```
Needs internet for live data (Yahoo Finance via `yfinance`, AMFI NAVs via mfapi.in). No API keys.
No internet? `STOCKPRO_MODE=demo python run.py` uses clearly-labelled synthetic data.
Docker and hosting: see Deploy below.

## Features
- **Screener**: the **Nifty 500** (official NSE constituent list, bundled with index group: Nifty 50 / Next 50 / Midcap 150 / Smallcap 250). Runs as a background job with a progress bar (first live run a few minutes, then cached). Preset screens (quality compounders, below fair value, momentum leaders, dividend payers, beaten down but healthy), filter by index group, sector, NSE industry, signal, P/E, ROE, score, name; top ideas and weakest names; **signal changes** (every run is compared with the previous one, so HOLD → BUY flips are listed); CSV export; "Refresh data" refetches from the source
- **Stock page**: Buy/Hold/Sell signals for long (3y+), medium (3-12m) and short (days-weeks) horizons, each with the reasons and the rule; fair value (Graham, growth-adjusted P/E, DCF; only drives the signal when the models agree); health score (5 pillars); **Street view** (analyst consensus and target range, promoter/institutional holding, P/S, EV/EBITDA, PEG); **earnings record** (next results date, EPS estimate vs reported for recent quarters); **dividend record** (yield, years paid, payout growth); latest **news** headlines; **5-year financials** (revenue, profit, margins, cash flow, debt, plus recent quarters) with plain-English trend takeaways; **peers** (same Yahoo industry when there are enough, else same sector) with medians; price chart with 50/200-day averages and RSI; ATR stop-loss; per-stock trend-rule backtest vs buy&hold and Nifty
- **Data quality**: where Yahoo has no ROE, margin, debt/equity, current ratio, free cash flow, EPS, book value or growth figure, the app rebuilds it from the financial statements and the stock page says which numbers were rebuilt and when the fundamentals were fetched
- **ETFs**: ~37 NSE-listed index, factor, sector, international and commodity ETFs. ETF screener (returns, vs Nifty, 3y CAGR, volatility, drawdown, liquidity, trend signals) and an ETF page with returns vs Nifty 50, risk/liquidity, role (core / satellite / cash), similar ETFs and the trend-rule backtest. Any ETF can go in the watchlist and portfolio
- **Watchlist**, **Portfolio + decision journal** (your return vs the same money in Nifty 50, concentration warnings)
- **Momentum backtest** over an index group (Nifty 50 by default) vs Nifty (with caveats)
- **Mutual funds**: compare any AMFI fund: CAGR, volatility, drawdown, Sharpe, tracking error, rolling returns, SIP/XIRR calculator
- **Learn** tab: glossary and investing habits
- **Accounts (optional)**: email + password login through Supabase, with the watchlist and portfolio journal stored per user. See *Login and cloud storage* below
- **Light / dark mode**: toggle in the header (follows your OS setting until you choose; `?theme=light` or `?theme=dark` in the URL also works)

## Deploy
The app is a plain FastAPI service, so anything that runs Python works. Two things decide where it runs well: the screener for 500 stocks takes a couple of minutes of background work, and the watchlist, journal and cache are files on disk.
- **A server you control (recommended)**: `docker build -t stockpro . && docker run -p 8000:8000 -v stockpro:/data stockpro`, or the same image on Render, Railway, Fly.io or a small VPS. Persistent disk, background jobs work, one instance serves the cache to everyone. Turn on the Supabase login below (or put it behind a VPN): without it the app is open to anyone who can reach it.
- **Vercel (serverless)**: works with zero config (`vercel.json` sets a 60 s function limit). Vercel sets `VERCEL=1`, which switches the app to *serverless mode*: the only writable folder is `/tmp`, so the data cache, watchlist and journal survive only while an instance is warm, and the screener advances a few seconds per poll request instead of running in the background (keep the tab open; the first full run takes a few minutes and will be redone after a cold start). Without Supabase that is fine for trying it out, not for keeping a portfolio; with Supabase configured the watchlist, journal and signal history live there and survive cold starts (the price and fundamentals cache is still rebuilt). `STOCKPRO_SERVERLESS=1` forces this mode elsewhere; `STOCKPRO_STEP_SECONDS` (default 8) is the work done per request.
- **Yahoo throttling**: a full 500-stock fetch is roughly 2,000 requests. Yahoo rate-limits bursts; the app backs off and retries, and "Refresh data" only refetches prices (ratios and statements keep their 24 h cache). Don't refresh repeatedly, and expect a shared host's IP to be throttled sooner than your laptop's.

## Login and cloud storage (Supabase)
Off by default. Set two environment variables and the app requires a signed-in user for everything under `/api` and keeps each user's watchlist and portfolio journal in your Supabase project instead of JSON files.
1. **Create the tables**: in the Supabase dashboard open *SQL Editor*, paste `supabase/schema.sql`, run it. It creates `watchlist`, `holdings` and `app_state` with row-level security, so a user can only ever read and write their own rows.
2. **Set the variables** (dashboard: *Project Settings > API*). Locally: copy `.env.example` to `.env` and fill it in; `python run.py` reads it. On Vercel: *Project Settings > Environment Variables*, then redeploy.
   - `SUPABASE_URL`: the project URL, `https://<ref>.supabase.co`
   - `SUPABASE_ANON_KEY`: the public key (`anon`, or the newer `sb_publishable_...`)
   - `SUPABASE_SERVICE_ROLE_KEY` (optional, secret): with it the screener's signal history is stored in Supabase too. Without it that history stays in a local file, which on Vercel is lost at every cold start
3. **Tell Supabase where the app lives**: *Authentication > URL Configuration*: set *Site URL* to your deployment (e.g. `https://your-app.vercel.app`) and add `http://127.0.0.1:8000` under *Redirect URLs* if you also run it locally. The confirmation and password-reset emails link back to these addresses.
4. **Decide who can sign up**: anyone who can open the site can create an account. For a personal deployment, create your own account and then switch off *Authentication > Sign In / Providers > Allow new users to sign up*. New accounts must confirm their email by default; Supabase's built-in mailer only sends a few emails an hour, so for anything beyond yourself add your own SMTP there (or switch *Confirm email* off).

How it works: the browser never talks to Supabase or sees a key. The server signs users in over Supabase's Auth API and keeps the session in two `HttpOnly` cookies (about an hour for the access token, renewed automatically with the refresh token). Watchlist and portfolio requests are made with the signed-in user's own token, so Supabase's row-level security is what separates users, not app code. Screener and price data are shared and are not stored in Supabase. A watchlist or journal already saved in the local JSON files is not copied into Supabase.

## Customise
- Universe: the bundled `app/nifty500.csv` (`symbol,name,sector,industry,group`) comes from the NSE index constituent files at niftyindices.com. Index membership changes, so refresh it now and then, or create `~/.stockpro/universe.csv` with the same columns (`industry` and `group` optional, no `.NS`) to use your own list.
- ETF list: create `~/.stockpro/etfs.csv` with `symbol,name,category,index,role` (role = core | satellite | cash). Yahoo tags Indian ETFs as ordinary shares, so this list is how the app knows a ticker is an ETF.
- Any ticker works in the search box: `TCS`, `RELIANCE.BO`, `us:AAPL`.
- Scoring rules and thresholds live in `app/analysis.py`. They are plain, readable Python.
- Data cache: `~/.stockpro/cache` (prices 6h, fundamentals and statements 24h, earnings/dividends/news 6h). Set `STOCKPRO_HOME` to move it. Watchlist, portfolio and signal history are JSON files in the same folder, unless Supabase is configured (see above).

## Layout
`app/main.py` (API + persistence), `app/supa.py` + `supabase/schema.sql` (optional Supabase login and storage), `app/analysis.py` (indicators, fair value, signals, backtests, statement takeaways), `app/data.py` (Yahoo/demo data + cache), `app/universe.py` + `app/nifty500.csv` (stock list), `app/mf.py` (mutual funds), `app/static/` (single-page frontend). Tests: `pytest -q`.

## Known limits
Free data can be delayed, missing or wrong (especially Yahoo fundamentals for Indian stocks). Fair value is assumption-driven. Fundamental rules can't be backtested without point-in-time data. Backtests have survivorship bias, exclude tax, and past results don't predict the future. Without the Supabase login the app is open: keep it bound to 127.0.0.1. Not investment advice.
