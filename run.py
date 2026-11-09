"""Start StockPro:  python run.py   (then open http://127.0.0.1:8000)
Set STOCKPRO_MODE=demo to force synthetic data, or =live to require Yahoo Finance.
Flags: --no-browser (don't open a tab), --reload (restart automatically when code in app/ changes).
Login and cloud storage: put SUPABASE_URL and SUPABASE_ANON_KEY in a .env file next to this one (see .env.example)."""
import os, sys, threading, webbrowser
from pathlib import Path
import uvicorn


def load_dotenv(path: Path = Path(__file__).with_name(".env")):
    """KEY=value lines from .env become environment variables (ones already set win). Hosts such as Vercel set them in
    their project settings instead, so this only matters when running on your own machine."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, val = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


if __name__ == "__main__":
    load_dotenv()
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")   # keep 127.0.0.1 unless Supabase login is configured: without it the app is open
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run("app.main:app", host=host, port=port, log_level="info",
                reload="--reload" in sys.argv, reload_dirs=["app"] if "--reload" in sys.argv else None)
