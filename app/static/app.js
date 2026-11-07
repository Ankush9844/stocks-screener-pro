"use strict";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
let charts = [];
let STATE = { mode: "live", screener: null, sort: { k: "score", dir: -1 }, wl: new Set() };

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (x, d = 2) => x == null ? "-" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (x, d = 1) => x == null ? "-" : (x * 100).toFixed(d) + "%";
const spct = (x, d = 1) => x == null ? "-" : `<span class="${x >= 0 ? "pos" : "neg"}">${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%</span>`;
const cur = sym => /\.(NS|BO)$/.test(sym || "") || (sym || "").startsWith("^") ? "₹" : "$";
const money = (x, c = "₹") => x == null ? "-" : c + Number(x).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const cr = x => x == null ? "-" : Math.abs(x) >= 1e12 ? "₹" + num(x / 1e12, 2) + " L Cr" : "₹" + num(x / 1e7, 0) + " Cr";
const big = (x, ccy = "INR") => x == null ? "-" : ccy === "INR" ? cr(x) : Math.abs(x) >= 1e9 ? ccy + " " + num(x / 1e9, 2) + " B" : ccy + " " + num(x / 1e6, 0) + " M";
const sigBadge = s => `<span class="sig ${s}">${s}</span>`;
const scoreColor = s => s == null ? "var(--muted)" : s >= 65 ? "var(--green)" : s >= 45 ? "var(--amber)" : "var(--red)";
const bar = s => `<div class="bar"><i style="width:${s ?? 0}%;background:${scoreColor(s)}"></i></div>`;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  return r.json();
}
const spin = msg => view.innerHTML = `<div class="spin">${esc(msg || "Loading…")}</div>`;
const fail = e => view.innerHTML = `<div class="card"><h3>Something went wrong</h3><p class="neg">${esc(e.message)}</p></div>`;
function killCharts() { charts.forEach(c => c.destroy()); charts = []; }
function chart(el, cfg) { const c = new Chart(el, cfg); charts.push(c); return c; }
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();   // current theme colour
// Chart options are built per chart (not shared) so each one reads the colours of the theme active when it is drawn.
const baseOpts = () => ({ responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, animation: false,
  plugins: { legend: { labels: { color: css("--muted"), boxWidth: 12 } } },
  scales: { x: { ticks: { color: css("--muted"), maxTicksLimit: 8 }, grid: { color: css("--grid") } }, y: { ticks: { color: css("--muted") }, grid: { color: css("--grid") } } } });

// ---------------------------------------------------------------- theme
function applyTheme(t, save = true) {
  document.documentElement.dataset.theme = t;
  if (save) localStorage.setItem("theme", t);
  $("#theme").textContent = t === "dark" ? "☀ Light" : "☾ Dark";
  const muted = css("--muted"), grid = css("--grid");
  charts.forEach(c => {   // recolour live charts in place instead of re-rendering the page
    Object.values(c.options.scales || {}).forEach(sc => { if (sc.ticks) sc.ticks.color = muted; if (sc.grid) sc.grid.color = grid; });
    if (c.options.plugins?.legend?.labels) c.options.plugins.legend.labels.color = muted;
    c.data.datasets.forEach(d => { if (d.themed) d.borderColor = css(d.themed); if (d.themedBg) d.backgroundColor = css(d.themedBg); });
    c.update("none");
  });
}
$("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
matchMedia("(prefers-color-scheme: light)").addEventListener("change", e => { if (!localStorage.getItem("theme")) applyTheme(e.matches ? "light" : "dark", false); });
applyTheme(document.documentElement.dataset.theme || "dark", false);

// ---------------------------------------------------------------- shell
async function init() {
  const s = await api("/api/status");
  STATE.mode = s.mode;
  const m = $("#mode"); m.textContent = s.mode === "live" ? "LIVE DATA" : "DEMO DATA"; m.className = "badge " + s.mode;
  if (s.mode === "demo") {
    const b = $("#banner"); b.classList.remove("hidden");
    b.textContent = "Demo mode: all prices and fundamentals are synthetic (" + s.reason + "). Do not make real decisions from them. Run the app on a computer with internet to get live data.";
  }
  try { (await api("/api/watchlist")).forEach(r => STATE.wl.add(r.symbol)); } catch (e) {}
  window.addEventListener("hashchange", route); route();
}
async function route() {
  killCharts();
  view.onclick = null;   // views attach their own delegated handler; don't let one leak into the next page
  const [, page, arg] = (location.hash || "#/screener").split("/");
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#/" + page));
  try {
    if (page === "stock") await stockView(decodeURIComponent(arg));
    else if (page === "etfs") await etfsView();
    else if (page === "watchlist") await watchlistView();
    else if (page === "portfolio") await portfolioView();
    else if (page === "backtest") await backtestView();
    else if (page === "funds") await fundsView();
    else if (page === "learn") learnView();
    else await screenerView();
  } catch (e) { fail(e); }
}

// search box
const q = $("#q"), sg = $("#suggest");
let qt;
q.addEventListener("input", () => { clearTimeout(qt); qt = setTimeout(async () => {
  if (!q.value.trim()) return sg.classList.add("hidden");
  const r = await api("/api/search?q=" + encodeURIComponent(q.value));
  sg.innerHTML = r.map(x => `<div data-s="${esc(x.symbol)}"><b>${esc(x.short || x.symbol)}</b> <span class="muted">${esc(x.name)} · ${esc(x.sector)}</span></div>`).join("");
  sg.classList.remove("hidden");
}, 150); });
sg.addEventListener("click", e => { const d = e.target.closest("[data-s]"); if (d) go(d.dataset.s); });
q.addEventListener("keydown", e => { if (e.key === "Enter" && q.value.trim()) go(q.value.trim()); });
document.addEventListener("click", e => { if (!e.target.closest(".search")) sg.classList.add("hidden"); });
function go(sym) { sg.classList.add("hidden"); q.value = ""; location.hash = "#/stock/" + encodeURIComponent(sym.includes(".") || sym.startsWith("^") || sym.toLowerCase().startsWith("us:") ? sym : sym.toUpperCase()); }

// ---------------------------------------------------------------- screener
async function screenerView(refresh) {
  if (!STATE.screener || refresh) {
    spin(STATE.mode === "live" ? (refresh ? "Refetching prices and fundamentals for every stock…" : "Analysing the universe… the first run downloads data for every stock (usually well under a minute).") : "Analysing…");
    STATE.screener = await api("/api/screener" + (refresh ? "?refresh=true" : ""));
  }
  const d = STATE.screener, rows = d.rows;
  const sectors = [...new Set(rows.map(r => r.sector).filter(Boolean))].sort();
  const cnt = h => ["BUY", "HOLD", "SELL"].map(s => rows.filter(r => r.signals[h].signal === s).length);
  const top = rows.filter(r => r.signals.long.signal === "BUY").sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 5);
  const worst = rows.filter(r => r.signals.long.signal === "SELL").sort((a, b) => (a.score ?? 0) - (b.score ?? 0)).slice(0, 5);
  const changes = d.changes || [], weekAgo = new Date(Date.now() - 7 * 86400e3).toISOString().slice(0, 10);
  const recent = new Map(changes.filter(c => c.date >= weekAgo).map(c => [c.symbol + ":" + c.horizon, c]));
  const badge = (x, h) => { const c = recent.get(x.symbol + ":" + h); return sigBadge(x.signals[h].signal) + (c ? `<span class="chg" title="Changed from ${c.from} on ${c.date}">●</span>` : ""); };
  const short = s => s.replace(".NS", "");
  view.innerHTML = `
  <h2>Screener</h2><p class="sub">${rows.length} stocks analysed ${d.failed.length ? `(${d.failed.length} failed: ${esc(d.failed.join(", "))})` : ""} · as of ${esc(d.as_of)}. Click a column to sort, a row to open.</p>
  <div class="grid g3" style="margin-bottom:16px">
    ${[["long", "Long term"], ["medium", "Medium term"], ["short", "Short term"]].map(([h, l]) => { const [b, o, s] = cnt(h); return `<div class="card stat"><div class="l">${l} signals</div><div class="v"><span class="pos">${b} buy</span> · <span style="color:var(--amber)">${o} hold</span> · <span class="neg">${s} sell</span></div></div>`; }).join("")}
  </div>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card"><h3>Top long-term ideas</h3>${top.length ? top.map(r => `<a class="pill" href="#/stock/${encodeURIComponent(r.symbol)}">${esc(short(r.symbol))} · ${num(r.score, 0)}</a>`).join("") : `<span class="neu">No long-term BUY signals right now.</span>`}<p class="note">Highest health score among stocks with a long-term BUY. Open the stock and read the reasons first.</p></div>
    <div class="card"><h3>Weakest: SELL / avoid</h3>${worst.length ? worst.map(r => `<a class="pill" href="#/stock/${encodeURIComponent(r.symbol)}">${esc(short(r.symbol))} · ${num(r.score, 0)}</a>`).join("") : `<span class="neu">No long-term SELL signals right now.</span>`}<p class="note">Lowest health score among long-term SELL signals. If you hold one, re-read your thesis.</p></div>
    <div class="card"><h3>Signal changes (30 days)</h3>${changes.length ? `<div class="chglist">${changes.slice(0, 8).map(c => `<a class="chgrow" href="#/stock/${encodeURIComponent(c.symbol)}"><span class="muted">${esc(c.date.slice(5))}</span><b>${esc(short(c.symbol))}</b><span class="muted">${esc(c.horizon)}</span>${sigBadge(c.from)}<span class="muted">→</span>${sigBadge(c.to)}</a>`).join("")}</div>${changes.length > 8 ? `<p class="note">${changes.length - 8} more in the table (● marks a change in the last week).</p>` : ""}` : `<span class="neu">None recorded yet. Each screener run is compared with the previous one, so flips like HOLD → BUY show up here over time.</span>`}</div>
  </div>
  <div class="filters">
    <label>Signal horizon<select id="fh"><option value="long">Long term</option><option value="medium">Medium term</option><option value="short">Short term</option></select></label>
    <label>Signal<select id="fs"><option value="">Any</option><option>BUY</option><option>HOLD</option><option>SELL</option></select></label>
    <label>Sector<select id="fsec"><option value="">All</option>${sectors.map(s => `<option>${esc(s)}</option>`).join("")}</select></label>
    <label>Min score <input id="fmin" type="number" value="0" min="0" max="100" style="width:80px"></label>
    <label>Max P/E <input id="fpe" type="number" placeholder="any" style="width:80px"></label>
    <label>Min ROE % <input id="froe" type="number" placeholder="any" style="width:80px"></label>
    <button id="fref" title="Refetch prices and fundamentals from the source (ignores the cache)">Refresh data</button>
    <button id="fcsv" title="Download the filtered table as CSV">Export CSV</button>
  </div>
  <div class="tablewrap"><table><thead><tr id="thead"></tr></thead><tbody id="tbody"></tbody></table></div>`;
  const cols = [["symbol", "Stock"], ["price", "Price"], ["change_1d", "1D"], ["fair_value", "Fair value"], ["upside", "Upside"], ["pe", "P/E"], ["roe", "ROE"], ["score", "Score"], ["sig", "Signal"], ["ret_6m", "6M"], ["rsi", "RSI"]];
  let shown = rows;
  const draw = () => {
    const h = $("#fh").value, s = $("#fs").value, sec = $("#fsec").value, mn = +$("#fmin").value || 0, pe = parseFloat($("#fpe").value), roe = parseFloat($("#froe").value);
    let r = rows.filter(x => (!s || x.signals[h].signal === s) && (!sec || x.sector === sec) && (x.score ?? 0) >= mn &&
      (isNaN(pe) || (x.pe != null && x.pe <= pe)) && (isNaN(roe) || (x.roe != null && x.roe * 100 >= roe)));
    const { k, dir } = STATE.sort;
    const val = x => k === "sig" ? x.signals[h].score : k === "symbol" ? x.symbol : x[k];
    r.sort((a, b) => { const A = val(a), B = val(b); if (A == null) return 1; if (B == null) return -1; return (A > B ? 1 : A < B ? -1 : 0) * dir; });
    shown = r;
    $("#thead").innerHTML = cols.map(([k2, l]) => `<th data-k="${k2}">${l}${STATE.sort.k === k2 ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
    $("#tbody").innerHTML = r.map(x => `<tr class="click" data-s="${esc(x.symbol)}"><td><b>${esc(x.symbol.replace(".NS", ""))}</b> <span class="muted">${esc(x.name)}</span></td>
      <td>${num(x.price)}</td><td>${spct(x.change_1d)}</td><td>${num(x.fair_value, 0)}</td><td>${spct(x.upside, 0)}</td><td>${num(x.pe, 1)}</td><td>${pct(x.roe)}</td>
      <td style="color:${scoreColor(x.score)}">${num(x.score, 0)}</td><td>${badge(x, h)}</td><td>${spct(x.ret_6m, 0)}</td><td>${num(x.rsi, 0)}</td></tr>`).join("")
      || `<tr><td colspan="11" class="neu">No stocks match these filters.</td></tr>`;
  };
  draw();
  view.querySelectorAll(".filters select,.filters input").forEach(el => el.addEventListener("input", draw));
  $("#fref").onclick = () => screenerView(true);
  $("#fcsv").onclick = () => {
    const hdr = ["symbol", "name", "sector", "price", "change_1d", "fair_value", "upside", "pe", "pb", "roe", "score", "signal_long", "signal_medium", "signal_short", "ret_6m", "rsi", "trend"];
    const q = v => v == null ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
    const lines = [hdr.join(",")].concat(shown.map(x => [x.symbol, x.name, x.sector, x.price, x.change_1d, x.fair_value, x.upside, x.pe, x.pb, x.roe, x.score,
      x.signals.long.signal, x.signals.medium.signal, x.signals.short.signal, x.ret_6m, x.rsi, x.trend].map(q).join(",")));
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = `stockpro-screener-${d.as_of.slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(a.href); };
  $("#thead").onclick = e => { const k = e.target.dataset.k; if (!k) return; STATE.sort = { k, dir: STATE.sort.k === k ? -STATE.sort.dir : -1 }; draw(); };
  $("#tbody").onclick = e => { const t = e.target.closest("tr[data-s]"); if (t) location.hash = "#/stock/" + encodeURIComponent(t.dataset.s); };
}

// ---------------------------------------------------------------- stock
function priceCharts(a) {
  const draw = n => { const ch = a.chart, from = Math.max(0, ch.dates.length - Math.round(n * ch.dates.length / 504));
    const sl = k => ch[k].slice(from); const labels = ch.dates.slice(from);
    killCharts();
    chart($("#pc"), { type: "line", data: { labels, datasets: [
      { label: "Price", data: sl("c"), borderColor: css("--text"), themed: "--text", borderWidth: 1.6, pointRadius: 0 },
      { label: "50-day", data: sl("s50"), borderColor: "#4c9be8", borderWidth: 1.2, pointRadius: 0 },
      { label: "200-day", data: sl("s200"), borderColor: "#d9a441", borderWidth: 1.2, pointRadius: 0 }] }, options: baseOpts() });
    const ro = baseOpts(); ro.plugins.legend = { display: false }; ro.scales.y.min = 0; ro.scales.y.max = 100;
    chart($("#rc"), { type: "line", data: { labels, datasets: [{ label: "RSI", data: sl("rsi"), borderColor: css("--accent"), themed: "--accent", borderWidth: 1.2, pointRadius: 0 },
      { data: labels.map(() => 70), borderColor: "rgba(229,83,75,.5)", borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
      { data: labels.map(() => 30), borderColor: "rgba(46,166,107,.5)", borderDash: [4, 4], pointRadius: 0, borderWidth: 1 }] }, options: ro }); };
  draw(504);
  $("#rng").onclick = e => { const n = e.target.dataset.n; if (!n) return; $("#rng").querySelectorAll("button").forEach(b => b.classList.toggle("on", b === e.target)); draw(+n); };
}
const chartCard = () => `<div class="card" style="margin-top:14px"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px"><h3>Price, 50 &amp; 200-day averages</h3>
    <div class="tabs" id="rng">${[["6M", 126], ["1Y", 252], ["2Y", 504]].map(([l, n]) => `<button data-n="${n}" class="${n === 504 ? "on" : ""}">${l}</button>`).join("")}</div></div>
    <div class="chartbox"><canvas id="pc"></canvas></div><div class="chartbox sm"><canvas id="rc"></canvas></div></div>`;

function trendBacktest(a) {
  api("/api/backtest/stock/" + encodeURIComponent(a.symbol)).then(b => {
    if (b.error) return $("#bt").textContent = b.error;
    $("#bt").innerHTML = `<p class="note">Rule: own it while price &gt; 200-day average and 50-day &gt; 200-day average; otherwise hold cash. ${b.trades} round trips, invested ${pct(b.time_invested, 0)} of the time, 0.2% cost per trade.</p>
      <table><thead><tr><th></th><th>CAGR</th><th>Max drawdown</th><th>Volatility</th><th>Sharpe</th></tr></thead><tbody>
      ${[["Trend rule", b.strategy], ["Just buy &amp; hold", b.buy_hold], ["Nifty 50", b.nifty]].filter(x => x[1]).map(([l, m]) => `<tr><td>${l}</td><td>${spct(m.cagr)}</td><td class="neg">${pct(m.max_drawdown, 0)}</td><td>${pct(m.volatility, 0)}</td><td>${num(m.sharpe, 2)}</td></tr>`).join("")}</tbody></table>
      <div class="chartbox" style="margin-top:10px"><canvas id="btc"></canvas></div><p class="note">One instrument, one rule, one history: this shows what happened, not what will. Trend rules usually cut drawdowns but can lag in choppy markets.</p>`;
    const s = b.series;
    chart($("#btc"), { type: "line", data: { labels: s.dates, datasets: [{ label: "Trend rule", data: s.strategy, borderColor: css("--accent"), themed: "--accent", pointRadius: 0, borderWidth: 1.6 },
      { label: "Buy & hold", data: s.buy_hold, borderColor: css("--muted"), themed: "--muted", pointRadius: 0, borderWidth: 1.2 }, ...(s.nifty ? [{ label: "Nifty 50", data: s.nifty, borderColor: "#d9a441", pointRadius: 0, borderWidth: 1.2 }] : [])] }, options: baseOpts() });
  }).catch(e => { const el = $("#bt"); if (el) el.textContent = e.message; });
}

const sigCardFor = (a, c) => k => { const s = a.signals[k];
  return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">${esc(s.horizon)}</h3>${sigBadge(s.signal)}</div>
  ${s.score != null ? `<div class="row"><span class="n">Score</span><span class="b">${bar(s.score)}</span><span class="s">${num(s.score, 0)}</span></div>` : ""}
  ${s.reliability !== "n/a" ? `<div class="note">Reliability: <b>${esc(s.reliability)}</b></div>` : ""}
  <ul class="reasons">${s.reasons.map(([tone, tx]) => `<li class="${tone}">${esc(tx)}</li>`).join("")}</ul>
  <p class="note" style="margin-top:10px">${esc(s.rule)}</p>
  ${s.levels.stop ? `<p class="note">Suggested stop-loss (ATR-based): <b>${c}${num(s.levels.stop, 0)}</b>${s.levels.target && k === "short" ? ` · target ${c}${num(s.levels.target, 0)}` : ""}</p>` : ""}
  ${k === "long" && s.levels.target ? `<p class="note">Estimated fair value: <b>${c}${num(s.levels.target, 0)}</b></p>` : ""}</div>`; };

async function stockView(sym) {
  spin("Analysing " + sym + "…");
  const a = await api("/api/stock/" + encodeURIComponent(sym));
  if (a.kind === "etf") return etfView(a);
  const c = cur(a.symbol), i = a.info, t = a.tech, fv = a.fair_value;
  const watched = STATE.wl.has(a.symbol);
  const sigCard = sigCardFor(a, c);
  // fair value strip
  const pts = [{ l: "Price", v: a.price, col: "var(--text)" }, ...Object.entries(fv.models || {}).map(([l, v]) => ({ l, v, col: "var(--blue)" }))];
  if (fv.value) pts.push({ l: "Fair value", v: fv.value, col: "var(--accent)" });
  if (fv.analyst_target) pts.push({ l: "Analyst target", v: fv.analyst_target, col: "var(--amber)" });
  const lo = Math.min(...pts.map(p => p.v)) * .92, hi = Math.max(...pts.map(p => p.v)) * 1.08;
  const posn = v => ((v - lo) / (hi - lo) * 100).toFixed(1);
  const hist = a.history || [];
  view.innerHTML = `
  ${i.fundamentals_ok === false ? `<div class="warn">Yahoo returned no fundamentals (P/E, ROE, cash flow…) for this ticker, so the long- and medium-term scores below rest on price data only. Check the ticker or try the .BO (BSE) version.</div>` : ""}
  <div class="head"><div><h2>${esc(a.name)}</h2>
    <div><span class="pill">${esc(a.symbol)}</span>${i.sector ? `<span class="pill">${esc(i.sector)}</span>` : ""}${i.market_cap ? `<span class="pill">Mkt cap ${cr(i.market_cap)}</span>` : ""}${!a.in_universe ? `<span class="pill">not in screener universe</span>` : ""}</div></div>
    <div style="text-align:right"><div class="price">${c}${num(a.price)} <span style="font-size:16px">${spct(a.change_1d, 2)}</span></div>
      <button id="wl">${watched ? "★ In watchlist" : "☆ Add to watchlist"}</button> <button id="addp" class="primary">+ Add to portfolio</button></div></div>
  <div class="grid g3">${["long", "medium", "short"].map(sigCard).join("")}</div>
  ${hist.length ? `<p class="note" style="margin:10px 0 0">Signal history: ${hist.slice(0, 4).map(c => `${esc(c.date)} ${esc(c.horizon)} ${esc(c.from)} → <b>${esc(c.to)}</b>`).join(" · ")}</p>` : ""}
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>Fair value (3 simple models)</h3>
      ${fv.value ? `<div class="stat"><span class="v">${c}${num(fv.value, 0)}</span> <span class="muted">median of models · confidence ${esc(fv.confidence)}</span> · ${spct(fv.upside, 0)} vs price</div>
      <div class="fvrow"><div class="fvline"></div>${pts.map(p => `<div class="fvdot" title="${esc(p.l)}" style="left:${posn(p.v)}%;background:${p.col}"></div>`).join("")}</div>
      <div class="kv">${pts.map(p => `<span><i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${p.col};margin-right:7px"></i>${esc(p.l)}</span><span>${c}${num(p.v, 0)}${p.l === "Price" ? "" : " (" + (p.v / a.price - 1 >= 0 ? "+" : "") + ((p.v / a.price - 1) * 100).toFixed(0) + "%)"}</span>`).join("")}</div>` : `<p class="neu">Not enough data to estimate a fair value.</p>`}
      ${(fv.notes || []).map(n => `<p class="note">${esc(n)}</p>`).join("")}
      <p class="note">Assumed growth ${pct(fv.growth_assumed)}. Fair value models are very sensitive to growth and discount-rate assumptions: treat as a range, not a price target.</p></div>
    <div class="card"><h3>Health score · <span style="color:${scoreColor(a.overall)}">${num(a.overall, 0)}/100</span></h3>
      ${Object.entries(a.pillars).map(([k, v]) => `<div class="row"><span class="n">${k}</span><span class="b">${bar(v)}</span><span class="s">${v == null ? "n/a" : num(v, 0)}</span></div>`).join("")}
      <p class="note">Pillars use ROE, margins, growth, debt, cash flow, P/E, P/B, fair value and price momentum. Banks/NBFCs skip debt and cash-flow checks.</p></div>
  </div>
  ${chartCard()}
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>Key numbers</h3><div class="kv">
      <span>P/E (trailing)</span><span>${num(i.pe, 1)}</span><span>Forward P/E</span><span>${num(i.forward_pe, 1)}</span><span>P/B</span><span>${num(i.pb, 2)}</span>
      <span>ROE</span><span>${pct(i.roe)}</span><span>Net margin</span><span>${pct(i.profit_margin)}</span><span>Revenue growth</span><span>${pct(i.revenue_growth)}</span>
      <span>Earnings growth</span><span>${pct(i.earnings_growth)}</span><span>Debt / equity</span><span>${num(i.debt_to_equity, 2)}</span><span>Current ratio</span><span>${num(i.current_ratio, 2)}</span>
      <span>Dividend yield</span><span>${pct(i.dividend_yield)}</span><span>Beta</span><span>${num(i.beta, 2)}</span><span>EPS</span><span>${num(i.eps, 2)}</span></div></div>
    <div class="card"><h3>Technicals</h3><div class="kv">
      <span>Trend</span><span>${esc(t.trend)}</span><span>RSI (14)</span><span>${num(t.rsi, 0)}</span><span>50-day avg</span><span>${num(t.sma50, 0)}</span><span>200-day avg</span><span>${num(t.sma200, 0)}</span>
      <span>52-week range</span><span>${num(t.low_52w, 0)} – ${num(t.high_52w, 0)}</span><span>From 52w high</span><span>${spct(t.from_high)}</span>
      <span>Return 1M / 3M</span><span>${spct(t.ret_1m)} / ${spct(t.ret_3m)}</span><span>Return 6M / 1Y</span><span>${spct(t.ret_6m)} / ${spct(t.ret_1y)}</span><span>Volatility (annual)</span><span>${pct(t.vol_ann, 0)}</span></div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>Financials: is the business actually growing?</h3><div id="fin" class="muted">Loading statements…</div></div>
  <div class="card" style="margin-top:14px"><h3>Peers in the same sector</h3><div id="peers" class="muted">Loading peers…</div></div>
  <div class="card" style="margin-top:14px"><h3>Does the trend rule work on this stock?</h3><div id="bt" class="muted">Running backtest…</div></div>`;
  $("#wl").onclick = async () => { await api(`/api/watchlist/${encodeURIComponent(a.symbol)}`, { method: watched ? "DELETE" : "POST" });
    watched ? STATE.wl.delete(a.symbol) : STATE.wl.add(a.symbol); stockView(sym); };
  $("#addp").onclick = () => { sessionStorage.setItem("prefill", JSON.stringify({ symbol: a.symbol, price: a.price })); location.hash = "#/portfolio"; };
  priceCharts(a);
  trendBacktest(a);
  api("/api/financials/" + encodeURIComponent(a.symbol)).then(f => financialsCard($("#fin"), f)).catch(e => { const el = $("#fin"); if (el) el.textContent = e.message; });
  api("/api/peers/" + encodeURIComponent(a.symbol)).then(p => peersCard($("#peers"), p, a.symbol)).catch(e => { const el = $("#peers"); if (el) el.textContent = e.message; });
}

// ---------------------------------------------------------------- ETF page
function etfView(a) {
  const c = cur(a.symbol), i = a.info, t = a.tech, r = a.returns, b = a.bench_returns || {}, k = a.risk, watched = STATE.wl.has(a.symbol);
  const hist = a.history || [];
  const periods = ["1M", "3M", "6M", "1Y", "3Y CAGR", "5Y CAGR"];
  view.innerHTML = `
  <div class="head"><div><h2>${esc(a.name)}</h2>
    <div><span class="pill">${esc(a.symbol)}</span><span class="pill">ETF · ${esc(i.category)}</span>${i.index ? `<span class="pill">Tracks ${esc(i.index)}</span>` : ""}<span class="pill">${esc(i.role_name)}</span></div></div>
    <div style="text-align:right"><div class="price">${c}${num(a.price)} <span style="font-size:16px">${spct(a.change_1d, 2)}</span></div>
      <button id="wl">${watched ? "★ In watchlist" : "☆ Add to watchlist"}</button> <button id="addp" class="primary">+ Add to portfolio</button></div></div>
  <div class="grid g3">${["long", "medium", "short"].map(sigCardFor(a, c)).join("")}</div>
  ${hist.length ? `<p class="note" style="margin:10px 0 0">Signal history: ${hist.slice(0, 4).map(x => `${esc(x.date)} ${esc(x.horizon)} ${esc(x.from)} → <b>${esc(x.to)}</b>`).join(" · ")}</p>` : ""}
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h3>Returns vs Nifty 50</h3><table><thead><tr><th></th>${periods.map(p => `<th>${p}</th>`).join("")}</tr></thead><tbody>
      <tr><td>This ETF</td>${periods.map(p => `<td>${spct(r[p], 1)}</td>`).join("")}</tr>
      <tr><td>Nifty 50</td>${periods.map(p => `<td>${spct(b[p], 1)}</td>`).join("")}</tr>
      <tr><td>Difference</td>${periods.map(p => `<td>${r[p] != null && b[p] != null ? spct(r[p] - b[p], 1) : "-"}</td>`).join("")}</tr></tbody></table>
      <p class="note">Price returns (dividends excluded; most Indian ETFs reinvest anyway). Nifty 50 is shown as the common yardstick even when the ETF tracks a different index. "-" means not enough listed history.</p></div>
    <div class="card"><h3>Risk &amp; liquidity (last 3 years)</h3><div class="kv">
      <span>Volatility (annual)</span><span>${pct(k.volatility, 0)}</span><span>Worst fall (max drawdown)</span><span class="neg">${pct(k.max_drawdown_3y, 0)}</span>
      <span>Sharpe ratio</span><span>${num(k.sharpe_3y, 2)}</span><span>Beta vs Nifty 50</span><span>${num(k.beta, 2)}</span><span>Correlation with Nifty 50</span><span>${num(k.correlation, 2)}</span>
      <span>Worst / best 1-year window</span><span>${spct(k.worst_1y, 0)} / ${spct(k.best_1y, 0)}</span>
      <span>Avg daily traded value</span><span>${k.avg_traded_value == null ? "-" : cr(k.avg_traded_value)}${k.thin_liquidity ? ' <span class="neg">(thin)</span>' : ""}</span>
      <span>Underlying index P/E</span><span>${num(i.pe, 1)}</span><span>52-week range</span><span>${num(t.low_52w, 0)} – ${num(t.high_52w, 0)}</span>
      <span>Trend / RSI</span><span>${esc(t.trend)} / ${num(t.rsi, 0)}</span></div>
      <p class="note">Low correlation with Nifty (gold, international) is what makes an ETF a diversifier. Expense ratio, tracking error and AUM are not in this data: check the AMC factsheet before buying, and prefer the cheaper, more liquid ETF on the same index.</p></div>
  </div>
  ${chartCard()}
  <div class="card" style="margin-top:14px"><h3>Other ETFs in this category</h3><div id="peers" class="muted">Loading…</div></div>
  <div class="card" style="margin-top:14px"><h3>Does the trend rule work on this ETF?</h3><div id="bt" class="muted">Running backtest…</div></div>`;
  $("#wl").onclick = async () => { await api(`/api/watchlist/${encodeURIComponent(a.symbol)}`, { method: watched ? "DELETE" : "POST" });
    watched ? STATE.wl.delete(a.symbol) : STATE.wl.add(a.symbol); stockView(a.symbol); };
  $("#addp").onclick = () => { sessionStorage.setItem("prefill", JSON.stringify({ symbol: a.symbol, price: a.price })); location.hash = "#/portfolio"; };
  priceCharts(a);
  trendBacktest(a);
  api("/api/peers/" + encodeURIComponent(a.symbol)).then(p => {
    const el = $("#peers"); if (!el) return;
    if (!p.peers.length) return el.innerHTML = `<p class="neu">No other ${esc(p.sector)} ETFs in the list.</p>`;
    el.innerHTML = `<div class="tablewrap" style="max-height:none"><table><thead><tr><th>ETF</th><th>Tracks</th><th>Price</th><th>6M</th><th>1Y</th><th>3Y CAGR</th><th>Volatility</th><th>Max DD</th><th>Traded/day</th><th>Medium</th></tr></thead><tbody>
      ${[p.self, ...p.peers].map((x, j) => `<tr class="click${j === 0 ? " me" : ""}" data-s="${esc(x.symbol)}"><td><b>${esc(x.symbol.replace(".NS", ""))}</b> <span class="muted">${j === 0 ? "this ETF" : esc(x.name)}</span></td><td class="muted">${esc(x.index || "")}</td><td>${num(x.price)}</td><td>${spct(x.ret_6m, 0)}</td><td>${spct(x.ret_1y, 0)}</td><td>${spct(x.cagr_3y, 1)}</td><td>${pct(x.volatility, 0)}</td><td class="neg">${pct(x.max_drawdown, 0)}</td><td>${x.avg_traded_value == null ? "-" : cr(x.avg_traded_value)}</td><td>${sigBadge(x.signals.medium.signal)}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">Two ETFs on the same index should move together; if one shows a very different return, suspect thin trading or a price away from NAV.</p>`;
    el.onclick = e => { const tr = e.target.closest("tr[data-s]"); if (tr && tr.dataset.s !== a.symbol) location.hash = "#/stock/" + encodeURIComponent(tr.dataset.s); };
  }).catch(e => { const el = $("#peers"); if (el) el.textContent = e.message; });
}

// ---------------------------------------------------------------- ETF screener
async function etfsView(refresh) {
  if (!STATE.etfs || refresh) {
    spin(refresh ? "Refetching ETF prices…" : "Analysing ETFs… (first run downloads 5 years of prices for each)");
    STATE.etfs = await api("/api/etfs" + (refresh ? "?refresh=true" : ""));
  }
  const d = STATE.etfs, rows = d.rows, short = s => s.replace(".NS", "");
  const catShort = x => ({ "Broad market (large cap)": "Large-cap index", "Broad market (mid / small cap)": "Mid/small index", "Sector / thematic": "Sector", "Factor / smart beta": "Factor", "Debt / liquid": "Debt" }[x] || x);
  const best = [...rows].filter(r => r.cagr_3y != null).sort((a, b) => b.cagr_3y - a.cagr_3y).slice(0, 5);
  const core = rows.filter(r => r.role === "core");
  view.innerHTML = `
  <h2>ETFs</h2><p class="sub">${rows.length} NSE-listed ETFs analysed ${d.failed.length ? `(${d.failed.length} failed: ${esc(d.failed.map(short).join(", "))})` : ""} · as of ${esc(d.as_of)}. ETFs have no company fundamentals, so they are judged on returns, risk, liquidity and their role in a portfolio. Click a row to open.</p>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card"><h3>Core index ETFs</h3>${core.map(r => `<a class="pill" href="#/stock/${encodeURIComponent(r.symbol)}">${esc(short(r.symbol))} · ${spct(r.ret_1y, 0)}</a>`).join("")}<p class="note">Broad-market trackers: the default long-term holding and the benchmark for everything else. 1-year return shown.</p></div>
    <div class="card"><h3>Best 3-year CAGR</h3>${best.map(r => `<a class="pill" href="#/stock/${encodeURIComponent(r.symbol)}">${esc(short(r.symbol))} · ${pct(r.cagr_3y, 0)}</a>`).join("") || `<span class="neu">Not enough history yet.</span>`}<p class="note">Past winners are often sector or thematic bets that mean-revert. Check drawdown and volatility before chasing.</p></div>
    <div class="card"><h3>Medium-term trend</h3><div class="v" style="font-size:20px;font-weight:600"><span class="pos">${rows.filter(r => r.signals.medium.signal === "BUY").length} buy</span> · <span style="color:var(--amber)">${rows.filter(r => r.signals.medium.signal === "HOLD").length} hold</span> · <span class="neg">${rows.filter(r => r.signals.medium.signal === "SELL").length} sell</span></div><p class="note">Momentum-only signals (3-12 months). A SELL on a core ETF means "pause lump sums", never "stop your SIP".</p></div>
  </div>
  <div class="filters">
    <label>Category<select id="ecat"><option value="">All</option>${d.categories.map(x => `<option>${esc(x)}</option>`).join("")}</select></label>
    <label>Medium-term signal<select id="esig"><option value="">Any</option><option>BUY</option><option>HOLD</option><option>SELL</option></select></label>
    <label>Liquidity<select id="eliq"><option value="">Any</option><option value="liquid">Liquid only</option></select></label>
    <button id="eref" title="Refetch prices from the source">Refresh data</button>
  </div>
  <div class="tablewrap"><table><thead><tr id="ehead"></tr></thead><tbody id="ebody"></tbody></table></div>`;
  const cols = [["symbol", "ETF"], ["category", "Category"], ["price", "Price"], ["change_1d", "1D"], ["ret_1m", "1M"], ["ret_6m", "6M"], ["ret_1y", "1Y"], ["vs_nifty_1y", "vs Nifty 1Y"], ["cagr_3y", "3Y CAGR"], ["volatility", "Volatility"], ["max_drawdown", "Max DD"], ["avg_traded_value", "Traded/day"], ["trend", "Trend"], ["sig", "Medium"], ["short", "Short"]];
  if (!STATE.esort) STATE.esort = { k: "ret_1y", dir: -1 };
  const draw = () => {
    const cat = $("#ecat").value, sg = $("#esig").value, lq = $("#eliq").value;
    const r = rows.filter(x => (!cat || x.category === cat) && (!sg || x.signals.medium.signal === sg) && (lq !== "liquid" || !x.thin));
    const { k, dir } = STATE.esort;
    const val = x => k === "sig" ? x.signals.medium.score : k === "short" ? x.signals.short.score : x[k];
    r.sort((a, b) => { const A = val(a), B = val(b); if (A == null) return 1; if (B == null) return -1; return (A > B ? 1 : A < B ? -1 : 0) * dir; });
    $("#ehead").innerHTML = cols.map(([k2, l]) => `<th data-k="${k2}">${l}${k === k2 ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
    $("#ebody").innerHTML = r.map(x => `<tr class="click" data-s="${esc(x.symbol)}"><td><b>${esc(short(x.symbol))}</b> <span class="muted">${esc(x.name)}</span></td><td class="muted">${esc(catShort(x.category))}${x.role === "core" ? " · core" : ""}</td>
      <td>${num(x.price)}</td><td>${spct(x.change_1d)}</td><td>${spct(x.ret_1m, 0)}</td><td>${spct(x.ret_6m, 0)}</td><td>${spct(x.ret_1y, 0)}</td><td>${spct(x.vs_nifty_1y, 0)}</td><td>${spct(x.cagr_3y, 1)}</td><td>${pct(x.volatility, 0)}</td><td class="neg">${pct(x.max_drawdown, 0)}</td>
      <td>${x.avg_traded_value == null ? "-" : cr(x.avg_traded_value)}${x.thin ? ' <span class="neg" title="Under ₹1 Cr a day: wide spreads likely">thin</span>' : ""}</td><td>${esc(x.trend)}</td><td>${sigBadge(x.signals.medium.signal)}</td><td>${sigBadge(x.signals.short.signal)}</td></tr>`).join("")
      || `<tr><td colspan="15" class="neu">No ETFs match these filters.</td></tr>`;
  };
  draw();
  view.querySelectorAll(".filters select").forEach(el => el.addEventListener("input", draw));
  $("#eref").onclick = () => etfsView(true);
  $("#ehead").onclick = e => { const k = e.target.dataset.k; if (!k) return; STATE.esort = { k, dir: STATE.esort.k === k ? -STATE.esort.dir : -1 }; draw(); };
  $("#ebody").onclick = e => { const t = e.target.closest("tr[data-s]"); if (t) location.hash = "#/stock/" + encodeURIComponent(t.dataset.s); };
}

function financialsCard(el, f) {
  if (!el) return;
  const A = f.annual || {}, Q = f.quarterly || {}, ccy = f.currency || "INR", P = A.periods || [];
  if (f.error || !P.length) { el.innerHTML = `<p class="neu">${esc(f.error || "No statement data available for this ticker.")}</p>`; return; }
  const rows = [["Revenue", "revenue"], ["Operating income", "operating_income"], ["Net profit", "net_income"], ["Net margin", "margin"], ["EPS (diluted)", "eps"],
    ["Operating cash flow", "operating_cash_flow"], ["Capex", "capex"], ["Free cash flow", "free_cash_flow"], ["Total debt", "total_debt"], ["Shareholders' equity", "equity"], ["Cash", "cash"]];
  const margin = (T, i) => (T.net_income?.[i] != null && T.revenue?.[i]) ? pct(T.net_income[i] / T.revenue[i]) : "-";
  const cell = (k, i) => k === "margin" ? margin(A, i) : k === "eps" ? num(A.eps?.[i], 2) : big(A[k]?.[i], ccy);
  const yoy = (arr, i) => (arr && i > 0 && arr[i] != null && arr[i - 1] != null && arr[i - 1] > 0) ? `<div>${spct(arr[i] / arr[i - 1] - 1, 0)}</div>` : "";
  el.innerHTML = `<ul class="reasons" style="margin-top:0">${(f.takeaways || []).map(([t, x]) => `<li class="${t}">${esc(x)}</li>`).join("")}</ul>
  <div class="chartbox" style="height:220px;margin:12px 0"><canvas id="finc"></canvas></div>
  <div class="tablewrap" style="max-height:none"><table><thead><tr><th>Annual (FY ending)</th>${P.map(p => `<th>${esc(p)}</th>`).join("")}</tr></thead><tbody>
  ${rows.filter(([, k]) => k === "margin" || (A[k] && A[k].some(v => v != null))).map(([l, k]) => `<tr><td>${l}</td>${P.map((_, i) => `<td>${cell(k, i)}${k === "revenue" || k === "net_income" ? yoy(A[k], i) : ""}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
  ${Q.periods?.length ? `<div class="tablewrap" style="margin-top:10px;max-height:none"><table><thead><tr><th>Quarterly</th>${Q.periods.map(p => `<th>${esc(p)}</th>`).join("")}</tr></thead><tbody>
    <tr><td>Revenue</td>${Q.periods.map((_, i) => `<td>${big(Q.revenue?.[i], ccy)}</td>`).join("")}</tr>
    <tr><td>Net profit</td>${Q.periods.map((_, i) => `<td>${big(Q.net_income?.[i], ccy)}</td>`).join("")}</tr>
    <tr><td>Net margin</td>${Q.periods.map((_, i) => `<td>${margin(Q, i)}</td>`).join("")}</tr></tbody></table></div>` : ""}
  <p class="note">Source: ${esc(f.source || "")}. Amounts in ${ccy === "INR" ? "₹ crore" : ccy}; small numbers under a figure are year-on-year growth. Yahoo's statements for Indian companies can lag the latest results or have gaps: confirm on the company's investor page.</p>`;
  const fo = baseOpts(); fo.scales.y.ticks.callback = v => big(v, ccy);
  chart($("#finc"), { type: "bar", data: { labels: P, datasets: [
    { label: "Revenue", data: A.revenue || [], backgroundColor: "rgba(76,155,232,.75)" },
    { label: "Net profit", data: A.net_income || [], backgroundColor: css("--accent"), themedBg: "--accent" },
    ...(A.free_cash_flow ? [{ label: "Free cash flow", data: A.free_cash_flow, backgroundColor: "rgba(217,164,65,.75)" }] : [])] },
    options: fo });
}

function peersCard(el, p, self) {
  if (!el) return;
  if (!p.peers?.length) { el.innerHTML = `<p class="neu">No other ${esc(p.sector || "same-sector")} stocks in the screener universe to compare with.</p>`; return; }
  const m = p.median || {};
  const vs = (v, med, lowerBetter) => v == null || med == null || v === med ? "" : ((lowerBetter ? v < med : v > med) ? ' class="pos"' : ' class="neg"');
  el.innerHTML = `<p class="sub" style="margin-bottom:8px">${esc(p.sector)} · ${p.peers.length} peers. Sector median: P/E ${num(m.pe, 1)} · P/B ${num(m.pb, 2)} · ROE ${pct(m.roe)} · score ${num(m.score, 0)}.</p>
  <div class="tablewrap" style="max-height:none"><table><thead><tr><th>Stock</th><th>Price</th><th>P/E</th><th>P/B</th><th>ROE</th><th>6M</th><th>Upside</th><th>Score</th><th>Long</th><th>Medium</th></tr></thead><tbody>
  ${[p.self, ...p.peers].map((r, i) => `<tr class="click${i === 0 ? " me" : ""}" data-s="${esc(r.symbol)}"><td><b>${esc(r.symbol.replace(".NS", ""))}</b> <span class="muted">${i === 0 ? "this stock" : esc(r.name)}</span></td><td>${num(r.price)}</td>
    <td${vs(r.pe, m.pe, true)}>${num(r.pe, 1)}</td><td${vs(r.pb, m.pb, true)}>${num(r.pb, 2)}</td><td${vs(r.roe, m.roe)}>${pct(r.roe)}</td><td>${spct(r.ret_6m, 0)}</td><td>${spct(r.upside, 0)}</td>
    <td style="color:${scoreColor(r.score)}">${num(r.score, 0)}</td><td>${sigBadge(r.signals.long.signal)}</td><td>${sigBadge(r.signals.medium.signal)}</td></tr>`).join("")}</tbody></table></div>
  <p class="note">Green/red = better/worse than the sector median (cheaper P/E and P/B, higher ROE). Ratios only mean something within a sector, and cheap can be cheap for a reason.</p>`;
  el.onclick = e => { const t = e.target.closest("tr[data-s]"); if (t && t.dataset.s !== self) location.hash = "#/stock/" + encodeURIComponent(t.dataset.s); };
}

// ---------------------------------------------------------------- watchlist
async function watchlistView() {
  spin("Loading watchlist…");
  const rows = await api("/api/watchlist");
  view.innerHTML = `<h2>Watchlist</h2><p class="sub">Stocks you're tracking. Add from any stock page with ☆.</p>` + (rows.length ? `<div class="tablewrap"><table><thead><tr><th>Stock</th><th>Price</th><th>1D</th><th>Upside to fair value</th><th>Score</th><th>Long</th><th>Medium</th><th>Short</th><th></th></tr></thead><tbody>
  ${rows.map(r => r.error ? `<tr><td>${esc(r.symbol)}</td><td colspan="8" class="neg">No data</td></tr>` : `<tr class="click" data-s="${esc(r.symbol)}"><td><b>${esc(r.symbol.replace(".NS", ""))}</b> <span class="muted">${esc(r.name)}</span></td><td>${num(r.price)}</td><td>${spct(r.change_1d)}</td><td>${spct(r.upside, 0)}</td>
  <td style="color:${scoreColor(r.score)}">${num(r.score, 0)}</td><td>${sigBadge(r.signals.long.signal)}</td><td>${sigBadge(r.signals.medium.signal)}</td><td>${sigBadge(r.signals.short.signal)}</td><td><button class="link" data-rm="${esc(r.symbol)}">remove</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="card neu">Nothing here yet. Open a stock and press “Add to watchlist”.</div>`);
  view.onclick = async e => { const rm = e.target.dataset.rm; if (rm) { e.stopPropagation(); await api("/api/watchlist/" + encodeURIComponent(rm), { method: "DELETE" }); STATE.wl.delete(rm); return watchlistView(); }
    const t = e.target.closest("tr[data-s]"); if (t) location.hash = "#/stock/" + encodeURIComponent(t.dataset.s); };
}

// ---------------------------------------------------------------- portfolio
async function portfolioView() {
  spin("Loading portfolio…");
  const p = await api("/api/portfolio"), T = p.totals, pre = JSON.parse(sessionStorage.getItem("prefill") || "null");
  sessionStorage.removeItem("prefill");
  view.innerHTML = `<h2>Portfolio &amp; decision journal</h2><p class="sub">Record every buy with the reason. The honest test of a stock-picker: are you beating a plain Nifty 50 index fund? Data stays on this computer.</p>
  ${p.warnings.map(w => `<div class="warn">${esc(w)}</div>`).join("")}
  ${p.holdings.length ? `<div class="grid g4" style="margin-bottom:14px">
    <div class="card stat"><div class="l">Invested</div><div class="v">${money(T.invested)}</div></div>
    <div class="card stat"><div class="l">Current value</div><div class="v">${money(T.value)}</div></div>
    <div class="card stat"><div class="l">Your return</div><div class="v">${spct(T.pnl_pct)}</div></div>
    <div class="card stat"><div class="l">Same money in Nifty 50</div><div class="v">${spct(T.nifty_pct)}</div></div>
    <div class="card stat"><div class="l">You vs Nifty</div><div class="v">${T.alpha == null ? "-" : (T.alpha >= 0 ? "<span class=pos>Ahead " : "<span class=neg>Behind ") + money(Math.abs(T.alpha * T.invested)) + "</span>"}</div></div></div>` : ""}
  <div class="card" style="margin-bottom:14px"><h3>Add a purchase</h3><form id="pf" class="filters" style="margin:0">
    <label>Ticker<input name="symbol" required value="${esc(pre?.symbol || "")}" placeholder="TCS" style="width:130px"></label>
    <label>Quantity<input name="qty" type="number" step="any" required style="width:90px"></label>
    <label>Buy price<input name="price" type="number" step="any" required value="${pre ? num(pre.price, 2).replace(/,/g, "") : ""}" style="width:110px"></label>
    <label>Date<input name="date" type="date" required value="${new Date().toISOString().slice(0, 10)}"></label>
    <label style="flex:1;min-width:220px">Why did I buy? (thesis)<input name="note" placeholder="e.g. strong ROE, below fair value, I understand the business"></label>
    <button class="primary">Add</button></form></div>
  ${p.holdings.length ? `<div class="tablewrap"><table><thead><tr><th>Stock</th><th>Qty</th><th>Buy</th><th>Now</th><th>Value</th><th>P&amp;L</th><th>vs Nifty</th><th>Weight</th><th>Long</th><th>Thesis</th><th></th></tr></thead><tbody>
  ${p.holdings.map(h => h.error ? `<tr><td>${esc(h.symbol)}</td><td colspan="10" class="neg">${esc(h.error)}</td></tr>` : `<tr><td><a href="#/stock/${encodeURIComponent(h.symbol)}" style="color:var(--text)"><b>${esc(h.symbol.replace(".NS", ""))}</b></a><div class="muted">${esc(h.date)}</div></td><td>${num(h.qty, 0)}</td><td>${num(h.price)}</td><td>${num(h.current)}</td><td>${money(h.value)}</td>
  <td>${spct(h.pnl_pct)}</td><td>${spct(h.alpha)}</td><td>${pct(h.weight, 0)}</td><td>${sigBadge(h.signals.long)}</td><td style="white-space:normal;max-width:260px" class="muted">${esc(h.note)}</td><td><button class="link" data-del="${h.id}">delete</button></td></tr>`).join("")}</tbody></table></div><p class="note">${esc(p.note)}</p>` : `<div class="card neu">No holdings yet.</div>`}`;
  $("#pf").onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    try { await api("/api/portfolio", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) }); portfolioView(); } catch (er) { alert(er.message); } };
  view.onclick = async e => { const id = e.target.dataset.del; if (id && confirm("Delete this entry?")) { await api("/api/portfolio/" + id, { method: "DELETE" }); portfolioView(); } };
}

// ---------------------------------------------------------------- backtest
async function backtestView() {
  view.innerHTML = `<h2>Strategy backtest: momentum</h2><p class="sub">Each month, buy the N strongest stocks by past return (skipping the latest month), equal weight, then compare with the Nifty 50 index. Momentum is one of the best-documented stock-picking effects: this lets you check it on your own universe.</p>
  <div class="filters"><label>Stocks held<input id="bn" type="number" value="10" min="3" max="30" style="width:80px"></label>
  <label>Lookback (months)<input id="bl" type="number" value="6" min="1" max="12" style="width:80px"></label><button class="primary" id="brun">Run backtest</button></div><div id="bout"></div>`;
  const run = async () => { $("#bout").innerHTML = `<div class="spin">Running… (downloads up to 10 years of prices the first time)</div>`;
    try { const b = await api(`/api/backtest/momentum?top_n=${$("#bn").value}&lookback_months=${$("#bl").value}`);
      if (b.error) return $("#bout").innerHTML = `<div class="card neg">${esc(b.error)}</div>`;
      const rowm = (l, m) => `<tr><td>${l}</td><td>${spct(m.cagr)}</td><td>${num(m.total_return, 2)}x−1</td><td class="neg">${pct(m.max_drawdown, 0)}</td><td>${pct(m.volatility, 0)}</td><td>${num(m.sharpe, 2)}</td></tr>`;
      $("#bout").innerHTML = `<div class="card"><div class="chartbox"><canvas id="bc"></canvas></div></div>
      <div class="tablewrap" style="margin-top:14px"><table><thead><tr><th></th><th>CAGR</th><th>Total return</th><th>Max drawdown</th><th>Volatility</th><th>Sharpe</th></tr></thead><tbody>
      ${rowm("Momentum top " + b.params.top_n, b.strategy)}${rowm("Equal-weight universe (" + b.universe_size + " stocks)", b.equal_weight)}${rowm("Nifty 50 index", b.nifty)}</tbody></table></div>
      <div class="card" style="margin-top:14px"><h3>Current momentum picks</h3>${b.holdings_now.map(s => `<a class="pill" href="#/stock/${encodeURIComponent(s)}">${esc(s.replace(".NS", ""))}</a>`).join("")}</div>
      <div class="card" style="margin-top:14px"><h3>Read this before trusting the result</h3><ul class="reasons">${b.caveats.map(c => `<li class="neg">${esc(c)}</li>`).join("")}</ul></div>`;
      const s = b.series; chart($("#bc"), { type: "line", data: { labels: s.dates, datasets: [{ label: "Momentum", data: s.strategy, borderColor: css("--accent"), themed: "--accent", pointRadius: 0, borderWidth: 1.8 },
        { label: "Equal-weight universe", data: s.equal_weight, borderColor: css("--muted"), themed: "--muted", pointRadius: 0, borderWidth: 1.2 }, { label: "Nifty 50", data: s.nifty, borderColor: "#d9a441", pointRadius: 0, borderWidth: 1.2 }] }, options: baseOpts() });
    } catch (e) { $("#bout").innerHTML = `<div class="card neg">${esc(e.message)}</div>`; } };
  $("#brun").onclick = () => { killCharts(); run(); }; run();
}

// ---------------------------------------------------------------- mutual funds
let FUND_SEL = new Map();
async function fundsView() {
  view.innerHTML = `<h2>Mutual funds</h2><p class="sub">Search any Indian mutual fund (AMFI data via mfapi.in), tick up to 5, and compare returns, risk and tracking error. Try “nifty 50 index direct”.</p>
  <div class="filters"><label>Search<input id="fq" placeholder="nifty 50 index direct" style="width:300px"></label><button id="fgo">Search</button><button id="fcmp" class="primary">Compare selected (<span id="fn">0</span>)</button></div>
  <div class="grid g2"><div class="card"><h3>Results</h3><div id="fres" class="muted">Search to see funds.</div></div><div class="card"><h3>Selected</h3><div id="fsel" class="muted">None yet.</div></div></div><div id="fout" style="margin-top:14px"></div>`;
  const upd = () => { $("#fn").textContent = FUND_SEL.size; $("#fsel").innerHTML = FUND_SEL.size ? [...FUND_SEL].map(([c, n]) => `<div class="row"><span class="b">${esc(n)}</span><button class="link" data-x="${c}">remove</button></div>`).join("") : "None yet."; };
  const search = async () => { $("#fres").textContent = "Searching…"; try { const r = await api("/api/mf/search?q=" + encodeURIComponent($("#fq").value));
    $("#fres").innerHTML = r.length ? r.map(f => `<div class="row"><input type="checkbox" data-c="${f.code}" data-n="${esc(f.name)}" ${FUND_SEL.has(f.code) ? "checked" : ""}><span class="b">${esc(f.name)}</span></div>`).join("") : "No funds found."; } catch (e) { $("#fres").innerHTML = `<span class="neg">${esc(e.message)}</span>`; } };
  $("#fgo").onclick = search; $("#fq").onkeydown = e => { if (e.key === "Enter") search(); };
  $("#fres").onchange = e => { const c = +e.target.dataset.c; if (!c) return; if (e.target.checked) { if (FUND_SEL.size >= 5) { e.target.checked = false; return alert("Max 5 funds"); } FUND_SEL.set(c, e.target.dataset.n); } else FUND_SEL.delete(c); upd(); };
  $("#fsel").onclick = e => { const x = +e.target.dataset.x; if (x) { FUND_SEL.delete(x); upd(); search(); } };
  $("#fcmp").onclick = async () => { if (!FUND_SEL.size) return alert("Select at least one fund.");
    $("#fout").innerHTML = `<div class="spin">Comparing…</div>`;
    try { const r = await api("/api/mf/compare?codes=" + [...FUND_SEL.keys()].join(","));
      const cols = [css("--accent"), "#4c9be8", "#d9a441", "#e5534b", "#a78bfa"];
      $("#fout").innerHTML = `<div class="card"><h3>Growth of 100 (last ≤5 years)</h3><div class="chartbox"><canvas id="mc"></canvas></div></div>
      <div class="tablewrap" style="margin-top:14px"><table><thead><tr><th>Fund</th><th>1Y</th><th>3Y CAGR</th><th>5Y CAGR</th><th>Volatility</th><th>Max DD</th><th>Sharpe</th><th>Tracking err.</th><th>1Y roll. median</th><th>% 1Y windows +ve</th></tr></thead><tbody>
      ${r.funds.map(f => `<tr><td style="white-space:normal;max-width:320px">${esc(f.name)}<div class="muted">NAV ${num(f.latest_nav)} · ${esc(f.as_of)}</div></td><td>${spct(f.cagr_1y)}</td><td>${spct(f.cagr_3y)}</td><td>${spct(f.cagr_5y)}</td><td>${pct(f.volatility_3y, 0)}</td><td class="neg">${pct(f.max_drawdown, 0)}</td><td>${num(f.sharpe_3y, 2)}</td><td>${f.is_index_fund ? pct(f.tracking_error_3y, 2) : "n/a"}</td><td>${spct(f.rolling_1y_median)}</td><td>${pct(f.rolling_1y_positive_pct, 0)}</td></tr>`).join("")}</tbody></table></div>
      ${r.notes.map(n => `<p class="note">${esc(n)}</p>`).join("")}
      <div class="card" style="margin-top:14px"><h3>SIP calculator</h3><div class="filters"><label>Fund<select id="sc">${r.funds.map(f => `<option value="${f.code}">${esc(f.name.slice(0, 60))}</option>`).join("")}</select></label>
      <label>Monthly ₹<input id="sm" type="number" value="10000" style="width:100px"></label><label>Years<input id="sy" type="number" value="5" min="1" max="15" style="width:70px"></label><button id="sgo">Calculate</button></div><div id="sres"></div></div>`;
      chart($("#mc"), { type: "line", data: { labels: r.dates, datasets: r.funds.map((f, i) => ({ label: f.name.slice(0, 40), data: r.curves[f.code], borderColor: cols[i], pointRadius: 0, borderWidth: 1.5 })) }, options: baseOpts() });
      $("#sgo").onclick = async () => { try { const s = await api(`/api/mf/sip?code=${$("#sc").value}&monthly=${$("#sm").value}&years=${$("#sy").value}`);
        $("#sres").innerHTML = s.error ? `<span class="neg">${esc(s.error)}</span>` : `<div class="grid g4"><div class="card stat"><div class="l">Invested (${s.months} months)</div><div class="v">${money(s.invested)}</div></div><div class="card stat"><div class="l">Value today</div><div class="v">${money(s.value)}</div></div><div class="card stat"><div class="l">SIP return (XIRR)</div><div class="v">${spct(s.xirr)}</div></div><div class="card stat"><div class="l">If invested as lump sum on day 1</div><div class="v">${money(s.lumpsum_value_same_total)}</div></div></div>`; } catch (e) { $("#sres").innerHTML = `<span class="neg">${esc(e.message)}</span>`; } };
    } catch (e) { $("#fout").innerHTML = `<div class="card neg">${esc(e.message)}</div>`; } };
  upd();
}

// ---------------------------------------------------------------- learn
function learnView() {
  const G = [["P/E ratio", "Price ÷ yearly earnings per share. How many rupees you pay for ₹1 of profit. Compare with the stock's own history and its sector, not across sectors."],
    ["P/B ratio", "Price ÷ book value per share. Most useful for banks and financials."],
    ["ROE", "Profit ÷ shareholders' equity. Above ~15% sustained is a good sign of a quality business."],
    ["Debt/equity", "How much borrowed money is used. Lower is safer. Ignore for banks (debt is their raw material)."],
    ["Free cash flow", "Cash left after running and investing in the business. Profits that never turn into cash are a red flag."],
    ["Fair value", "An estimate of what the business is worth from earnings, book value and cash flows. Always a range; wrong assumptions give wrong answers."],
    ["Margin of safety", "Buying well below your fair value estimate so that you survive being wrong."],
    ["50/200-day average", "Average closing price over 50/200 trading days. Price above a rising 200-day average = long-term uptrend."],
    ["RSI", "0-100 momentum gauge. Below 30 = beaten down, above 70 = stretched. Not a reason alone to buy or sell."],
    ["MACD", "Compares fast vs slow price averages. A cross above its signal line hints momentum is turning up."],
    ["ATR stop-loss", "A stop placed 2-2.5× the stock's typical daily range below price, so normal noise doesn't shake you out."],
    ["Max drawdown", "Biggest peak-to-trough fall. Ask yourself: could I sit through that without selling?"],
    ["Sharpe ratio", "Return above the risk-free rate per unit of volatility. Higher = better reward for risk."],
    ["Tracking error", "For index funds: how far the fund strays from its index. Lower is better."],
    ["XIRR", "Annualised return that accounts for the timing of each SIP instalment."],
    ["ETF", "Exchange-traded fund: a basket (an index, a sector, gold…) that trades like a share. Buy it for the basket, judge it on cost, tracking and liquidity, not on company fundamentals."],
    ["Core vs satellite", "Core = broad index ETFs you hold for decades. Satellite = sector, factor, international or commodity tilts you add deliberately and keep small."]];
  view.innerHTML = `<h2>Learn: how to become a good investor</h2><p class="sub">The app shows evidence; these habits decide whether you profit from it.</p>
  <div class="grid g2"><div class="card"><h3>Ten habits that matter</h3><ul class="reasons">
  ${["Start with a baseline: a low-cost Nifty 50 index fund. Your stock picks must beat it after costs and tax, otherwise just own the index.", "Use the Portfolio tab to track every pick against Nifty. Judge yourself over 3+ years, not weeks.", "Write down the thesis before buying (Journal). Review it when the price moves. This is the fastest way to learn from mistakes.", "Only buy businesses you can explain in two sentences.", "Buy quality at a fair or cheap price. A great company at a silly price is still a bad investment.", "Diversify: 10-20 stocks across sectors, no single stock above ~15-20%.", "Size positions so that a total loss in one stock doesn't hurt (e.g. 5% each).", "Avoid short-term trading as a beginner: most retail F&O and intraday traders lose money. Treat the Short-term signal as a timing hint only.", "Invest regularly (SIP) and keep an emergency fund + insurance outside the market.", "Expect 30-40% drawdowns on the way. Decide your response in advance."].map(t => `<li class="pos">${esc(t)}</li>`).join("")}</ul></div>
  <div class="card"><h3>How the signals work</h3><ul class="reasons">
  <li class="neu"><b>Long term:</b> health score (profitability, growth, balance sheet, valuation, momentum) + fair value. BUY needs score ≥ 65 and price ≥ 10% under fair value.</li>
  <li class="neu"><b>Medium term:</b> momentum-heavy score + uptrend filter + relative strength vs Nifty.</li>
  <li class="neu"><b>Short term:</b> RSI, MACD, Bollinger bands and trend. Lowest reliability.</li>
  <li class="neg"><b>Limits:</b> free data can be stale or wrong; fundamentals are trailing; fair value is assumption-driven; nothing here sees news, management quality, regulation or fraud. Always read the company's latest results and annual report before putting real money in.</li>
  <li class="neg"><b>Tax &amp; costs</b> (STCG/LTCG, brokerage) reduce returns and are not modelled in signals.</li></ul></div></div>
  <div class="card" style="margin-top:14px"><h3>Glossary</h3><div class="grid g2">${G.map(([k, v]) => `<div><b>${k}</b><div class="muted" style="font-size:13px">${esc(v)}</div></div>`).join("")}</div></div>`;
}

init();
