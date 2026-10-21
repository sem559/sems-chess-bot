"""Run the web GUI: `sems-chess` (or `python -m sems.web`) [--port 8000] [--no-browser]"""
import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Sem's Chess Bot in your browser")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps it on this computer only")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}"
    print(f"Sem's Chess Bot is running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("sems.web.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
