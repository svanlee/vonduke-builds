#!/usr/bin/env python3
"""
Training session runner for AKSUMAEL.
Posts named objectives to /train, polls training_log.jsonl for answers,
grades them, and writes session JSON + results MD.

Usage:
  python3 run_training_session.py <day> <topic> <rep_delay_s> <obj_delay_s> \
      <objectives_json_file> <out_results_md> <out_session_json> <out_runs_json>

Or imported and called as a library.
"""
import json, time, sys, pathlib, re, datetime, requests

BRIDGE = "http://localhost:7683"
TRAINING_LOG = pathlib.Path("data/memory/training_log.jsonl")
POLL_INTERVAL = 3   # seconds between log polls
POLL_TIMEOUT  = 180 # seconds to wait for an answer

# ── Grading ──────────────────────────────────────────────────────────────────

HARDWARE_DUMP_SIGNALS = [
    r"i2c_addresses",
    r"capture_card.*missing",
    r"FSM.*NOT RUNNING",
    r"camera.*unavailable",
    r"ttyACM",
    r"vision_ok.*false",
    r"sensor_readings",
    r"hardware_status",
    r"capture card.*missing",
    r"no visual input",
    r"game loop.*prevents",
]

MINECRAFT_SIGNALS = [
    r"minecraft",
    r"crafting table",
    r"survival mode",
    r"wood block",
    r"mine.*ore",
    r"build.*shelter",
]

IDENTITY_FAIL = [
    r"\bJarvis\b",
    r"I am not",
    r"I cannot help",
    r"outside my (current )?scope",
    r"I don't have the ability",
]

def auto_grade(objective: str, answer: str, allow_game_content: bool = False) -> tuple[str, str]:
    """Return (grade, reason)."""
    if not answer or len(answer.strip()) < 10:
        return "FAIL", "empty or trivially short answer"

    a_lower = answer.lower()
    obj_lower = objective.lower()

    # Hard FAIL signals
    for pat in HARDWARE_DUMP_SIGNALS:
        if re.search(pat, answer, re.I):
            return "FAIL", f"hardware dump detected: {pat}"

    if not allow_game_content:
        for pat in MINECRAFT_SIGNALS:
            if re.search(pat, a_lower):
                return "FAIL", f"Minecraft content detected: {pat}"

    for pat in IDENTITY_FAIL:
        if re.search(pat, answer, re.I):
            return "FAIL", f"identity/refusal failure: {pat}"

    # Count words — too short is PARTIAL
    words = len(answer.split())
    if words < 15:
        return "PARTIAL", f"answer too short ({words} words)"

    # Check if it's actually on-topic (very rough heuristic: answer shares key words with obj)
    obj_words = set(re.findall(r'\b\w{4,}\b', obj_lower))
    ans_words  = set(re.findall(r'\b\w{4,}\b', a_lower))
    overlap = len(obj_words & ans_words)
    if overlap == 0 and len(obj_words) > 3:
        return "PARTIAL", "answer may be off-topic (no keyword overlap)"

    return "PASS", "on-topic, no hardware dump, adequate length"


# ── Bridge helpers ────────────────────────────────────────────────────────────

def post_objective(goal_name: str, text: str) -> bool:
    """Queue a named training objective. Returns True on success."""
    try:
        r = requests.post(
            f"{BRIDGE}/train",
            json={"text": text, "goal": goal_name},
            timeout=15,
        )
        d = r.json()
        return d.get("queued", False)
    except Exception as e:
        print(f"[RUNNER] POST failed for {goal_name}: {e}", file=sys.stderr)
        return False


def poll_for_answer(goal_name: str, since_pos: int) -> tuple[str | None, int, int]:
    """
    Tail training_log.jsonl from byte offset `since_pos` looking for goal_name.
    Returns (answer_text_or_None, new_byte_offset, tick).
    """
    deadline = time.time() + POLL_TIMEOUT
    pos = since_pos
    while time.time() < deadline:
        try:
            size = TRAINING_LOG.stat().st_size
            if size > pos:
                chunk = TRAINING_LOG.read_bytes()[pos:]
                for raw in chunk.split(b"\n"):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                        if entry.get("goal") == goal_name and entry.get("answer"):
                            new_pos = TRAINING_LOG.stat().st_size
                            return entry["answer"], new_pos, entry.get("tick", 0)
                    except json.JSONDecodeError:
                        pass
                pos = size
        except FileNotFoundError:
            pass
        time.sleep(POLL_INTERVAL)
    return None, pos, 0


# ── Session runner ────────────────────────────────────────────────────────────

def run_session(
    day: int,
    topic: str,
    objectives: list[str],
    rep_delay: float = 30.0,
    obj_delay: float = 60.0,
    id_prefix: str | None = None,
    n_reps: int = 3,
    allow_game_content: bool = False,
) -> dict:
    """
    Run a full training session. Returns the session dict suitable for
    writing to day{N}_session.json.
    """
    prefix = id_prefix or f"day{day}"
    session = {
        "day": day,
        "topic": topic,
        "run_at": datetime.datetime.now().isoformat(),
        "results": [],
    }
    runs_flat = []  # for the *_runs.json flat list

    log_pos = TRAINING_LOG.stat().st_size if TRAINING_LOG.exists() else 0

    total = len(objectives) * n_reps
    done  = 0

    for obj_i, objective in enumerate(objectives, start=1):
        obj_id    = f"{prefix}-{obj_i}"
        obj_entry = {"obj": obj_i, "objective": objective, "reps": []}

        for rep in range(1, n_reps + 1):
            goal_name = f"train:{obj_id}-r{rep}"
            print(f"\n[RUNNER] Day {day} | O{obj_i}/{len(objectives)} Rep {rep}/{n_reps}")
            print(f"         goal: {goal_name}")
            print(f"         text: {objective[:80]}...")

            queued = post_objective(goal_name, objective)
            if not queued:
                print(f"[RUNNER] WARNING: bridge did not confirm queue for {goal_name}")

            t0 = time.time()
            answer, log_pos, tick = poll_for_answer(goal_name, log_pos)
            elapsed = time.time() - t0

            if answer is None:
                grade  = "FAIL"
                reason = f"no answer within {POLL_TIMEOUT}s"
                answer = ""
            else:
                grade, reason = auto_grade(objective, answer, allow_game_content=allow_game_content)

            done += 1
            print(f"[RUNNER] {grade} ({reason}) | tick={tick} | {elapsed:.0f}s | {len(answer.split())} words")

            rep_entry = {
                "rep": rep, "tick": tick, "elapsed_s": round(elapsed, 1),
                "answer": answer, "words": len(answer.split()), "grade": grade,
                "grade_reason": reason,
            }
            obj_entry["reps"].append(rep_entry)
            runs_flat.append({
                "goal": goal_name, "prompt": objective,
                "tick": tick, "elapsed_s": round(elapsed, 1),
                "answer": answer, "words": len(answer.split()),
                "id": obj_id, "rep": rep, "grade": grade,
            })

            progress = f"{done}/{total}"
            if rep < n_reps:
                print(f"[RUNNER] waiting {rep_delay}s before next rep... ({progress})")
                time.sleep(rep_delay)

        session["results"].append(obj_entry)

        if obj_i < len(objectives):
            print(f"[RUNNER] waiting {obj_delay}s before next objective...")
            time.sleep(obj_delay)

    session["runs_flat"] = runs_flat
    return session


# ── Output writers ────────────────────────────────────────────────────────────

def write_results_md(session: dict, path: pathlib.Path):
    day   = session["day"]
    topic = session["topic"]
    lines = [f"# Day {day} Training Results — {topic}", ""]

    counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    for obj_entry in session["results"]:
        obj_i     = obj_entry["obj"]
        objective = obj_entry["objective"]
        reps      = obj_entry["reps"]
        grades    = [r["grade"] for r in reps]
        for g in grades:
            counts[g] = counts.get(g, 0) + 1
        overall = "PASS" if all(g == "PASS" for g in grades) else \
                  "FAIL" if all(g == "FAIL" for g in grades) else "PARTIAL"
        grade_str = ", ".join(grades)
        lines.append(f"## O{obj_i:02d}: {objective[:80]}")
        lines.append(f"**Overall: {overall}** ({grade_str})")
        lines.append("")
        for r in reps:
            lines.append(f"### Rep {r['rep']} (tick {r['tick']}) — {r['grade']}")
            lines.append(r["answer"])
            lines.append("")
        lines.append("---")
        lines.append("")

    total = sum(counts.values())
    lines.append(f"## Summary")
    lines.append(
        f"PASS: {counts.get('PASS',0)} | "
        f"PARTIAL: {counts.get('PARTIAL',0)} | "
        f"FAIL: {counts.get('FAIL',0)} / {total} objectives"
    )
    path.write_text("\n".join(lines))
    print(f"[RUNNER] wrote {path}")


def write_session_json(session: dict, path: pathlib.Path):
    # Strip runs_flat from the session JSON (keep it clean)
    out = {k: v for k, v in session.items() if k != "runs_flat"}
    path.write_text(json.dumps(out, indent=2))
    print(f"[RUNNER] wrote {path}")


def write_runs_json(session: dict, path: pathlib.Path):
    path.write_text(json.dumps(session.get("runs_flat", []), indent=2))
    print(f"[RUNNER] wrote {path}")


# ── CLI entry-point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--day",       type=int,   required=True)
    p.add_argument("--topic",     type=str,   required=True)
    p.add_argument("--prefix",    type=str,   default=None)
    p.add_argument("--reps",      type=int,   default=3)
    p.add_argument("--rep-delay", type=float, default=30.0)
    p.add_argument("--obj-delay", type=float, default=60.0)
    p.add_argument("--objectives",type=str,   required=True, help="JSON array string or @file")
    p.add_argument("--out-md",    type=str,   required=True)
    p.add_argument("--out-json",  type=str,   required=True)
    p.add_argument("--out-runs",  type=str,   default=None)
    p.add_argument("--mode",       type=str,   default="robotics", choices=["robotics","game"])
    args = p.parse_args()

    # Parse objectives
    if args.objectives.startswith("@"):
        objs = json.loads(pathlib.Path(args.objectives[1:]).read_text())
    else:
        objs = json.loads(args.objectives)

    session = run_session(
        day       = args.day,
        topic     = args.topic,
        objectives= objs,
        rep_delay = args.rep_delay,
        obj_delay = args.obj_delay,
        id_prefix = args.prefix,
        n_reps    = args.reps,
        allow_game_content = (args.mode == "game"),
    )
    write_results_md(session,  pathlib.Path(args.out_md))
    write_session_json(session, pathlib.Path(args.out_json))
    if args.out_runs:
        write_runs_json(session, pathlib.Path(args.out_runs))
