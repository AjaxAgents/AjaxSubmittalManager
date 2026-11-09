"""Entry point: `python -m asm.main` (from backend/) starts the local server and opens the browser."""
from __future__ import annotations

import argparse
import logging
import threading
import time
import webbrowser

import uvicorn

from .config import settings_cfg


def main() -> None:
    srv = settings_cfg().get("server", {})
    ap = argparse.ArgumentParser(description="Ajax Submittal Manager")
    ap.add_argument("--host", default=srv.get("host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(srv.get("port", 8765)))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    if args.host not in ("127.0.0.1", "localhost"):
        print("WARNING: binding beyond localhost exposes project documents on your network. v1 has no login.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print(f"\n  Ajax Submittal Manager running at {url}\n  Press Ctrl+C to stop.\n")
    uvicorn.run("asm.api.app:app", host=args.host, port=args.port, reload=args.reload, log_level="warning")


if __name__ == "__main__":
    main()
