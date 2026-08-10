#!/usr/bin/env python3
"""
Day 21 conversation test — runs on robocar-hub, reads result into
data/training/conv_test_result.json for grading by the overnight agent.

Usage: python3 tools/conv_test.py
"""
import json, time, requests, pathlib, datetime, sys

BRIDGE = "http://localhost:7683"
OUT = pathlib.Path(__file__).parent.parent / "data/training/conv_test_result.json"

PROMPT = "Hey, can you introduce yourself and tell me what you're working on?"

def main():
    # health check
    try:
        h = requests.get(f"{BRIDGE}/health", timeout=5)
        health = h.json()
        print(f"Bridge health: tick={health.get('tick')} uptime={health.get('uptime_s')}s mode={health.get('mode')}")
    except Exception as e:
        print(f"Bridge unreachable: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nPosting: {PROMPT!r}\n")
    t0 = time.time()
    try:
        r = requests.post(
            f"{BRIDGE}/train",
            json={"text": PROMPT},
            timeout=120
        )
        elapsed = time.time() - t0
        data = r.json()
    except Exception as e:
        print(f"POST failed: {e}", file=sys.stderr)
        sys.exit(1)

    answer = data.get("answer", data.get("text", str(data)))
    print(f"Answer ({elapsed:.1f}s):\n{answer}\n")

    result = {
        "ts": time.time(),
        "run_at": datetime.datetime.now().isoformat(),
        "prompt": PROMPT,
        "answer": answer,
        "elapsed_s": elapsed,
        "bridge_health": health,
    }
    OUT.write_text(json.dumps(result, indent=2))
    print(f"Result written to {OUT}")

if __name__ == "__main__":
    main()
