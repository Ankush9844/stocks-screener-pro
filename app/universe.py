"""Stock and ETF universes. Default stocks = the Nifty 500 (bundled `nifty500.csv`, from the official NSE index
constituent lists, with the index group each stock belongs to: Nifty 50 / Next 50 / Midcap 150 / Smallcap 250);
default ETFs = the liquid NSE-listed index, factor, sector, international and commodity ETFs.
Index membership changes over time: override by creating ~/.stockpro/universe.csv (`symbol,name,sector[,industry,group]`)
or ~/.stockpro/etfs.csv (`symbol,name,category,index,role`), symbols without the .NS suffix.
Yahoo Finance tags Indian ETFs as ordinary equities, so this list is how the app knows a ticker is an ETF."""
from __future__ import annotations
import csv
from pathlib import Path
from . import data

BUNDLED = Path(__file__).parent / "nifty500.csv"
GROUPS = ("Nifty 50", "Nifty Next 50", "Midcap 150", "Smallcap 250")
_cache: dict = {}


def _read_csv(p: Path) -> list[dict]:
    with open(p, newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


# ETF categories and the role each usually plays in a portfolio
CORE, SAT, CASH = "core", "satellite", "cash"
BROAD, MIDSM, SECTOR, FACTOR, INTL, COMMOD, DEBT = ("Broad market (large cap)", "Broad market (mid / small cap)", "Sector / thematic",
                                                   "Factor / smart beta", "International", "Commodity", "Debt / liquid")
ETFS = [
    ("NIFTYBEES", "Nippon India Nifty 50 BeES", BROAD, "Nifty 50", CORE), ("SETFNIF50", "SBI Nifty 50 ETF", BROAD, "Nifty 50", CORE),
    ("JUNIORBEES", "Nippon India Nifty Next 50 Junior BeES", BROAD, "Nifty Next 50", CORE), ("NEXT50IETF", "ICICI Pru Nifty Next 50 ETF", BROAD, "Nifty Next 50", CORE),
    ("BSE500IETF", "ICICI Pru BSE 500 ETF", BROAD, "BSE 500", CORE), ("EQUAL50ADD", "DSP Nifty 50 Equal Weight ETF", BROAD, "Nifty 50 Equal Weight", CORE),
    ("MOM100", "Motilal Oswal Nifty Midcap 100 ETF", MIDSM, "Nifty Midcap 100", SAT), ("MIDCAPETF", "ICICI Pru Nifty Midcap 150 ETF", MIDSM, "Nifty Midcap 150", SAT),
    ("HDFCSML250", "HDFC Nifty Smallcap 250 ETF", MIDSM, "Nifty Smallcap 250", SAT), ("MIDQ50ADD", "DSP Nifty Midcap 150 Quality 50 ETF", MIDSM, "Nifty Midcap 150 Quality 50", SAT),
    ("BANKBEES", "Nippon India Nifty Bank BeES", SECTOR, "Nifty Bank", SAT), ("PSUBNKBEES", "Nippon India Nifty PSU Bank BeES", SECTOR, "Nifty PSU Bank", SAT),
    ("ITBEES", "Nippon India Nifty IT ETF", SECTOR, "Nifty IT", SAT), ("PHARMABEES", "Nippon India Nifty Pharma ETF", SECTOR, "Nifty Pharma", SAT),
    ("AUTOBEES", "Nippon India Nifty Auto ETF", SECTOR, "Nifty Auto", SAT), ("HEALTHY", "ABSL Nifty Healthcare ETF", SECTOR, "Nifty Healthcare", SAT),
    ("FMCGIETF", "ICICI Pru Nifty FMCG ETF", SECTOR, "Nifty FMCG", SAT), ("INFRABEES", "Nippon India Nifty Infrastructure BeES", SECTOR, "Nifty Infrastructure", SAT),
    ("CONSUMBEES", "Nippon India Nifty India Consumption ETF", SECTOR, "Nifty India Consumption", SAT), ("CPSEETF", "CPSE ETF", SECTOR, "Nifty CPSE", SAT),
    ("ICICIB22", "Bharat 22 ETF", SECTOR, "S&P BSE Bharat 22", SAT), ("MNC", "Kotak Nifty MNC ETF", SECTOR, "Nifty MNC", SAT),
    ("NV20BEES", "Nippon India Nifty 50 Value 20 BeES", FACTOR, "Nifty 50 Value 20", SAT), ("LOWVOLIETF", "ICICI Pru Nifty 100 Low Vol 30 ETF", FACTOR, "Nifty 100 Low Volatility 30", SAT),
    ("MOMOMENTUM", "Motilal Oswal Nifty 200 Momentum 30 ETF", FACTOR, "Nifty 200 Momentum 30", SAT), ("ALPHA", "Kotak Nifty Alpha 50 ETF", FACTOR, "Nifty Alpha 50", SAT),
    ("SBIETFQLTY", "SBI Nifty 200 Quality 30 ETF", FACTOR, "Nifty 200 Quality 30", SAT), ("DIVOPPBEES", "Nippon India Nifty Dividend Opportunities 50 ETF", FACTOR, "Nifty Dividend Opportunities 50", SAT),
    ("MON100", "Motilal Oswal NASDAQ 100 ETF", INTL, "NASDAQ 100", SAT), ("MAFANG", "Mirae Asset NYSE FANG+ ETF", INTL, "NYSE FANG+", SAT),
    ("MASPTOP50", "Mirae Asset S&P 500 Top 50 ETF", INTL, "S&P 500 Top 50", SAT), ("HNGSNGBEES", "Nippon India Hang Seng BeES", INTL, "Hang Seng", SAT),
    ("GOLDBEES", "Nippon India Gold BeES", COMMOD, "Domestic gold price", SAT), ("GOLDIETF", "ICICI Pru Gold ETF", COMMOD, "Domestic gold price", SAT),
    ("SILVERBEES", "Nippon India Silver ETF", COMMOD, "Domestic silver price", SAT), ("SILVERIETF", "ICICI Pru Silver ETF", COMMOD, "Domestic silver price", SAT),
    ("LIQUIDBEES", "Nippon India Nifty 1D Rate Liquid BeES", DEBT, "Nifty 1D Rate (overnight)", CASH),
]


def load_etfs() -> list[dict]:
    p = data.HOME / "etfs.csv"
    rows = ETFS
    if p.exists():
        with open(p, newline="") as f:
            rows = [(r["symbol"].strip(), r.get("name", r["symbol"]), r.get("category", SECTOR), r.get("index", ""), r.get("role", SAT))
                    for r in csv.DictReader(f)]
    return [{"symbol": data.normalize(s), "short": s, "name": n, "category": c, "index": i, "role": r, "kind": "etf"} for s, n, c, i, r in rows]


def lookup_etf(symbol: str) -> dict | None:
    for e in load_etfs():
        if e["symbol"] == symbol:
            return e
    return None


def load_universe() -> list[dict]:
    """Stock universe, re-read only when the CSV changes (the screener and search call this constantly)."""
    p = data.HOME / "universe.csv"
    src = p if p.exists() else BUNDLED
    mtime = src.stat().st_mtime
    hit = _cache.get("stocks")
    if hit and hit[0] == (str(src), mtime):
        return hit[1]
    rows = []
    for r in _read_csv(src):
        if not r.get("symbol"):
            continue
        rows.append({"symbol": data.normalize(r["symbol"]), "short": r["symbol"], "name": r.get("name") or r["symbol"],
                     "sector": r.get("sector", ""), "industry": r.get("industry", ""), "group": r.get("group") or "Custom", "kind": "stock"})
    _cache["stocks"] = ((str(src), mtime), rows)
    return rows


def groups() -> list[str]:
    seen = {u["group"] for u in load_universe()}
    return [g for g in GROUPS if g in seen] + sorted(g for g in seen if g not in GROUPS)


def lookup(symbol: str) -> dict | None:
    for u in load_universe():
        if u["symbol"] == symbol:
            return u
    return None


def search(q: str, limit: int = 12) -> list[dict]:
    q = q.strip().lower()
    if not q:
        return []
    etfs = [{**e, "sector": "ETF · " + e["category"]} for e in load_etfs()]
    return [u for u in load_universe() + etfs if q in u["short"].lower() or q in u["name"].lower()][:limit]
