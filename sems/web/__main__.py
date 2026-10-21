"""Run the web GUI: `sems-chess` (or `python -m sems.web`) [--port 8000] [--no-browser]"""
import argparse
import os
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Sem's Chess Bot in your browser")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps it on this computer only")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        # Reachable from other devices: accept any Host name, but still only same-origin pages.
        os.environ["SEMS_ALLOWED_HOSTS"] = "*"
        print(f"Warning: listening on {args.host}, so other devices on your network can open the game.")
    url = f"http://{args.host}:{args.port}"
    print(f"Sem's Chess Bot is running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("sems.web.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
