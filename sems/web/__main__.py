"""Run the web GUI: python -m sems.web [--port 8000]"""
import argparse

import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()
print(f"Sem's Chess Bot: open http://{args.host}:{args.port}")
uvicorn.run("sems.web.app:app", host=args.host, port=args.port)
