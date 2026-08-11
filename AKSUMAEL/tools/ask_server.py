#!/usr/bin/env python3
"""
AKSUMAEL live conversation server — port 7684.
Direct route_llm_call() path, NOT the training bridge.

Usage:
    cd /home/ros/vonduke-builds/AKSUMAEL
    python3 tools/ask_server.py

Then query:
    python3 tools/ask.py "Hey AKSUMAEL, are you up?"
"""
import sys, os, time, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from core.llm_router import route_llm_call
from core.identity import AKSUMAEL_IDENTITY

PORT = 7684
MAX_TOKENS = 900
TIMEOUT    = 90

app = Flask(__name__)
_started = time.time()


def _build_chat_prompt(question: str) -> str:
    return (
        f"{AKSUMAEL_IDENTITY}\n\n"
        "You are in live conversation with your operator, Scott. "
        "Answer naturally and helpfully. Be direct and concise. "
        "Do not recite hardware lists or sensor readings unless specifically asked. "
        "Do not mention Minecraft or game state — you are AKSUMAEL, a general-purpose "
        "engineering intelligence.\n\n"
        "=== OPERATOR MESSAGE ===\n"
        f"{question}\n"
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "uptime_s": round(time.time() - _started, 1), "port": PORT})


@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    question = (data.get("question") or data.get("q") or "").strip()
    if not question:
        return jsonify({"error": "missing 'question' field"}), 400
    t0 = time.time()
    prompt = _build_chat_prompt(question)
    try:
        raw, provider = route_llm_call(prompt, max_tokens=MAX_TOKENS, timeout=TIMEOUT)
        answer = (raw or "").strip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({
        "answer": answer,
        "provider": provider,
        "elapsed_s": round(time.time() - t0, 2),
        "ts": datetime.datetime.now().isoformat(),
    })


if __name__ == "__main__":
    print(f"[ASK] AKSUMAEL live conversation → http://127.0.0.1:{PORT}/ask")
    print(f"[ASK] CLI: python3 tools/ask.py \"your question\"")
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
