#!/usr/bin/env python3
# Run with: venv/bin/python3 tools/ask.py
"""
CLI: python3 tools/ask.py "your question"
Sends a question to AKSUMAEL and prints the answer.
Starts ask_server if not already running.
"""
import sys, os, subprocess, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests

SERVER = "http://127.0.0.1:7684"
REPO   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_server():
    try:
        r = requests.get(f"{SERVER}/health", timeout=3)
        if r.ok:
            return True
    except Exception:
        pass
    # Start it
    print("[ask] starting ask_server.py in background...", file=sys.stderr)
    subprocess.Popen(
        [sys.executable, os.path.join(REPO, "tools", "ask_server.py")],
        cwd=REPO,
        stdout=open("/tmp/ask_server.log", "a"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(15):
        time.sleep(1)
        try:
            r = requests.get(f"{SERVER}/health", timeout=2)
            if r.ok:
                print("[ask] server ready", file=sys.stderr)
                return True
        except Exception:
            pass
    print("[ask] server did not start in time", file=sys.stderr)
    return False


def ask(question: str) -> str:
    ensure_server()
    r = requests.post(
        f"{SERVER}/ask",
        json={"question": question},
        timeout=120,
    )
    d = r.json()
    if "error" in d:
        return f"ERROR: {d['error']}"
    return d["answer"]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 tools/ask.py \"your question\"")
        sys.exit(1)
    q = " ".join(sys.argv[1:])
    print(ask(q))
