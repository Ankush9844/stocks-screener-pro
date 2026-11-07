"""Start StockPro:  python run.py   (then open http://127.0.0.1:8000)
Set STOCKPRO_MODE=demo to force synthetic data, or =live to require Yahoo Finance.
Flags: --no-browser (don't open a tab), --reload (restart automatically when code in app/ changes)."""
import os, sys, threading, webbrowser
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")   # keep 127.0.0.1: this app has no login
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run("app.main:app", host=host, port=port, log_level="info",
                reload="--reload" in sys.argv, reload_dirs=["app"] if "--reload" in sys.argv else None)
