#!/usr/bin/env python3
"""
bench/er2_vs_mesh.py — ASIMOV-Agentic style benchmark for AKSUMAEL supervisor.

Measures:
  - Tier 1 (deterministic) refusal accuracy and latency
  - Tier 2 (mesh-llm) refusal accuracy and latency (when available)
  - Agreement rate between Tier 1 and Tier 2

Usage:
  cd ~/vonduke-builds/AKSUMAEL
  python3 bench/er2_vs_mesh.py
  python3 bench/er2_vs_mesh.py --tier2          # also test mesh-llm
  python3 bench/er2_vs_mesh.py --prompts bench/prompts.json
"""

import argparse
import json
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from safety.core import Supervisor, Ruling
from safety.beliefs import BeliefProxy


def make_belief(spec: dict, last_vision_age_s: float) -> BeliefProxy:
    wm = types.SimpleNamespace(
        health_pct     = spec.get("health_pct", 1.0),
        hunger_pct     = spec.get("hunger_pct", 1.0),
        food_items     = spec.get("food_items", []),
        facing         = spec.get("facing", "north"),
        y_level        = spec.get("y_level", 64),
        light_level    = spec.get("light_level", 15),
        pos_x          = spec.get("pos_x", 0.0),
        pos_z          = spec.get("pos_z", 0.0),
    )
    return BeliefProxy(
        world_mem       = wm,
        mine_pitch      = spec.get("mine_pitch", 0.0),
        inventory_count = spec.get("inventory_count", 0),
        last_vision_ts  = time.time() - last_vision_age_s,
    )


def run_benchmark(prompts_path: str, test_tier2: bool = False) -> dict:
    with open(prompts_path) as f:
        data = json.load(f)

    prompts = data["prompts"]
    results = []

    sup_t1 = Supervisor(enable_tier2=False)
    sup_t2 = Supervisor(enable_tier2=True) if test_tier2 else None

    print(f"\n{'─'*60}")
    print(f"  AKSUMAEL Supervisor Benchmark  ({len(prompts)} prompts)")
    print(f"  Tier 2 (mesh-llm): {'ON' if test_tier2 else 'OFF'}")
    print(f"{'─'*60}\n")

    for p in prompts:
        pid        = p["id"]
        label      = p["label"]
        proposed   = p["proposed"]
        current    = p["current"]
        bspec      = p["belief"]
        age        = bspec.get("last_vision_age_s", 0.3)
        expected   = p["expected_ruling"]
        substitute = p.get("expected_substitute")

        belief = make_belief(bspec, age)

        # ── Tier 1 ──────────────────────────────────────────────────────────
        t0 = time.perf_counter()
        v1 = sup_t1.review(proposed, current, belief)
        t1_ms = (time.perf_counter() - t0) * 1000

        t1_pass = (v1.ruling.value == expected)
        sub_pass = (substitute is None or v1.substitute == substitute)
        t1_ok    = t1_pass and sub_pass

        row = {
            "id":          pid,
            "label":       label,
            "proposed":    proposed,
            "expected":    expected,
            "t1_ruling":   v1.ruling.value,
            "t1_substitute": v1.substitute,
            "t1_correct":  t1_ok,
            "t1_ms":       round(t1_ms, 3),
        }

        mark = "✓" if t1_ok else "✗"
        print(f"  {mark} [{pid}] {label}")
        print(f"      T1: {v1.ruling.value:<8}  expected: {expected:<8}  "
              f"sub={v1.substitute or '—':<10}  {t1_ms:.3f}ms")
        if not t1_ok:
            print(f"      !! MISMATCH — expected ruling={expected} sub={substitute}")

        # ── Tier 2 (optional) ────────────────────────────────────────────────
        if test_tier2 and sup_t2:
            belief2 = make_belief(bspec, age)   # fresh timestamp
            t0 = time.perf_counter()
            v2 = sup_t2.review(proposed, current, belief2)
            t2_ms = (time.perf_counter() - t0) * 1000

            t2_ok = (v2.ruling.value == expected)
            agree = (v1.ruling.value == v2.ruling.value)

            row.update({
                "t2_ruling":  v2.ruling.value,
                "t2_correct": t2_ok,
                "t2_ms":      round(t2_ms, 3),
                "t1_t2_agree": agree,
            })
            print(f"      T2: {v2.ruling.value:<8}  agree_t1={agree}  {t2_ms:.3f}ms")

        results.append(row)
        print()

    # ── Summary ──────────────────────────────────────────────────────────────
    n          = len(results)
    t1_correct = sum(1 for r in results if r["t1_correct"])
    t1_latencies = [r["t1_ms"] for r in results]
    t1_avg = sum(t1_latencies) / n
    t1_max = max(t1_latencies)

    print(f"{'─'*60}")
    print(f"  Tier 1  accuracy : {t1_correct}/{n} ({100*t1_correct/n:.0f}%)")
    print(f"  Tier 1  latency  : avg={t1_avg:.3f}ms  max={t1_max:.3f}ms")

    if test_tier2:
        t2_correct = sum(1 for r in results if r.get("t2_correct", False))
        t2_agree   = sum(1 for r in results if r.get("t1_t2_agree", False))
        t2_latencies = [r["t2_ms"] for r in results if "t2_ms" in r]
        t2_avg = sum(t2_latencies) / len(t2_latencies) if t2_latencies else 0
        print(f"  Tier 2  accuracy : {t2_correct}/{n} ({100*t2_correct/n:.0f}%)")
        print(f"  Tier 2  latency  : avg={t2_avg:.1f}ms")
        print(f"  T1/T2 agreement  : {t2_agree}/{n} ({100*t2_agree/n:.0f}%)")

    print(f"{'─'*60}\n")

    summary = {
        "n_prompts":    n,
        "t1_accuracy":  t1_correct / n,
        "t1_avg_ms":    round(t1_avg, 3),
        "t1_max_ms":    round(t1_max, 3),
        "results":      results,
    }
    if test_tier2:
        summary["t2_accuracy"] = t2_correct / n
        summary["t2_avg_ms"]   = round(t2_avg, 1)
        summary["t1_t2_agreement"] = t2_agree / n

    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", default="bench/prompts.json")
    parser.add_argument("--tier2", action="store_true",
                        help="Also benchmark mesh-llm Tier 2 (requires running model)")
    parser.add_argument("--out", default=None,
                        help="Write JSON results to file")
    args = parser.parse_args()

    summary = run_benchmark(args.prompts, test_tier2=args.tier2)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Results written to {args.out}")

    # Non-zero exit if accuracy < 100%
    sys.exit(0 if summary["t1_accuracy"] == 1.0 else 1)


if __name__ == "__main__":
    main()
