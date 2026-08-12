#!/usr/bin/env python3
"""
catalog/report.py — Plaintext census of the Capability Catalog

Answers (without opening code):
  1. What rung is every subsystem on?
  2. What single next capability moves the most children up a rung?
  3. What does each system replace (phrased as a job)?

Usage:
    python3 catalog/report.py              # full report to stdout
    python3 catalog/report.py --rungs      # rung summary only
    python3 catalog/report.py --leverage   # highest-leverage next cap only
    python3 catalog/report.py --replaces   # "what it replaces" only
"""

import pathlib
import sys
import textwrap
from collections import defaultdict

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed — run: pip install pyyaml")
    sys.exit(1)

ROOT    = pathlib.Path(__file__).parent.parent
CAP_DIR = ROOT / "capabilities"
RUNG_LABELS = {1: "HUMAN-LED", 2: "HUMAN-ASSISTED", 3: "FULLY-AUTONOMOUS"}
RUNG_ICONS  = {1: "●", 2: "◑", 3: "○"}  # filled = more human work remaining


def load_capabilities() -> list[dict]:
    caps = []
    for path in sorted(CAP_DIR.rglob("*.yaml")):
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
            if isinstance(doc, dict) and "id" in doc:
                caps.append(doc)
        except Exception as e:
            print(f"WARNING: skipping {path}: {e}", file=sys.stderr)
    return caps


def wrap(text: str, width: int = 72, indent: str = "    ") -> str:
    if not text:
        return ""
    return textwrap.fill(str(text).strip(), width=width,
                         initial_indent=indent, subsequent_indent=indent)


# ── Section 1: Rung summary ─────────────────────────────────────────────────

def section_rungs(caps: list[dict]) -> str:
    by_domain = defaultdict(list)
    for cap in caps:
        by_domain[cap.get("domain", "UNKNOWN")].append(cap)

    lines = ["═" * 72,
             "  AUTONOMY LADDER — Current Rung by Capability",
             "  ● human-led  ◑ human-assisted  ○ fully-autonomous",
             "═" * 72]

    for domain in sorted(by_domain):
        lines.append(f"\n  {domain}")
        lines.append("  " + "─" * 68)
        for cap in sorted(by_domain[domain], key=lambda c: c.get("current_rung", 0)):
            rung  = cap.get("current_rung", 0)
            icon  = RUNG_ICONS.get(rung, "?")
            label = RUNG_LABELS.get(rung, f"rung-{rung}")
            name  = cap.get("name", cap["id"])
            lines.append(f"  {icon}  {name:<40} rung {rung}  {label}")
            ladder = cap.get("ladder", {})
            next_rung_key = f"rung_{rung + 1}"
            if next_rung_key in ladder:
                next_desc = ladder[next_rung_key].get("description", "")
                if next_desc:
                    lines.append(wrap(f"→ {next_desc}", width=70, indent="       "))

    return "\n".join(lines)


# ── Section 2: Leverage analysis ────────────────────────────────────────────

def section_leverage(caps: list[dict]) -> str:
    """Find caps at rung < 3 whose children are also stuck at low rungs.
    Promoting the parent enables the children → high leverage."""
    id_to_cap = {c["id"]: c for c in caps}
    # count how many registered children each cap has that are below rung 3
    leverage: dict[str, int] = defaultdict(int)

    for cap in caps:
        parent_id = cap["id"]
        for child_id in cap.get("breaks_into") or []:
            child = id_to_cap.get(child_id)
            if child and child.get("current_rung", 3) < 3:
                leverage[parent_id] += 1

    # also count direct builds_on dependents
    for cap in caps:
        for dep_id in cap.get("builds_on") or []:
            if cap.get("current_rung", 3) < 3:
                leverage[dep_id] += 1

    # rank by caps that are themselves below rung 3
    candidates = [
        (score, cid)
        for cid, score in leverage.items()
        if id_to_cap.get(cid, {}).get("current_rung", 3) < 3
    ]
    candidates.sort(reverse=True)

    lines = ["\n" + "═" * 72,
             "  LEVERAGE — Next Capability With Most Downstream Impact",
             "═" * 72]

    if not candidates:
        lines.append("\n  No blocked dependents found across current catalog.")
        return "\n".join(lines)

    for score, cid in candidates[:3]:
        cap  = id_to_cap[cid]
        rung = cap.get("current_rung", 0)
        name = cap.get("name", cid)
        next_rung_key = f"rung_{rung + 1}"
        next_label    = cap.get("ladder", {}).get(next_rung_key, {}).get("label", "")
        lines.append(f"\n  {name}  ({cid})")
        lines.append(f"  Currently rung {rung} — next: {next_label or 'n/a'}")
        lines.append(f"  Unblocks {score} downstream capability/dependencies")

    return "\n".join(lines)


# ── Section 3: What each system replaces ────────────────────────────────────

def section_replaces(caps: list[dict]) -> str:
    lines = ["\n" + "═" * 72,
             "  WHAT EACH CAPABILITY REPLACES  (job framing)",
             "═" * 72]

    by_domain = defaultdict(list)
    for cap in caps:
        by_domain[cap.get("domain", "UNKNOWN")].append(cap)

    for domain in sorted(by_domain):
        lines.append(f"\n  {domain}")
        lines.append("  " + "─" * 68)
        for cap in sorted(by_domain[domain], key=lambda c: c.get("name", c["id"])):
            name = cap.get("name", cap["id"])
            replaces = cap.get("replaces", "—")
            lines.append(f"  {name}")
            lines.append(wrap(replaces, width=70, indent="    ↳ "))

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    caps = load_capabilities()
    if not caps:
        print(f"No capability YAMLs found under {CAP_DIR}")
        sys.exit(1)

    args = sys.argv[1:]
    show_all = not args

    if show_all or "--rungs" in args:
        print(section_rungs(caps))
    if show_all or "--leverage" in args:
        print(section_leverage(caps))
    if show_all or "--replaces" in args:
        print(section_replaces(caps))

    if show_all:
        print(f"\n  {len(caps)} capabilities indexed  •  run catalog/validate.py to check schema\n")


if __name__ == "__main__":
    main()
