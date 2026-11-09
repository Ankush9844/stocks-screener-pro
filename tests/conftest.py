"""Shared test setup, imported by pytest before any test module (and so before the app reads its environment)."""
import os, shutil
shutil.rmtree("/tmp/stockpro_test_home", ignore_errors=True)   # leftovers from an earlier run (signal history) would change results
for _k in [k for k in os.environ if k.startswith("SUPABASE_")]:
    del os.environ[_k]          # the suite must never talk to a real Supabase project; test_supabase.py uses a stand-in
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
