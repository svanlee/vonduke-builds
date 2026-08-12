#!/usr/bin/env python3
"""
catalog/validate.py — Capability Catalog validator (Phase 1)

Loads every capabilities/<domain>/<slug>.yaml and checks:
  - Required fields present and non-empty
  - id matches <domain>.<slug> from file path
  - current_rung is 1, 2, or 3
  - all 3 ladder rungs (rung_1, rung_2, rung_3) present with label + description
  - the_human is non-empty
  - domain is one of the allowed values
  - breaks_into / builds_on are lists (may be empty)

Exits 0 if all pass, 1 if any fail.
Usage:
    python3 catalog/validate.py                  # all capabilities
    python3 catalog/validate.py perception/      # one domain
    python3 catalog/validate.py perception/reid-bridge.yaml  # one file
"""

import os
import sys
import pathlib

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed — run: pip install pyyaml")
    sys.exit(1)

ROOT = pathlib.Path(__file__).parent.parent
CAP_DIR = ROOT / "capabilities"

REQUIRED_TOP = ["id", "name", "domain", "category", "status",
                "current_rung", "summary", "replaces", "ladder", "the_human"]
REQUIRED_RUNG = ["label", "description"]
ALLOWED_DOMAINS = {"AKSUMAEL", "ROBOCAR", "HIVE", "TOOLING"}
ALLOWED_RUNGS = {1, 2, 3}


def validate_file(path: pathlib.Path) -> list[str]:
    """Return list of error strings (empty = pass)."""
    errors = []

    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]

    if not isinstance(doc, dict):
        return ["top-level is not a mapping"]

    # Required top-level fields
    for field in REQUIRED_TOP:
        if field not in doc:
            errors.append(f"missing required field: {field}")
        elif not doc[field] and doc[field] != 0:
            errors.append(f"field '{field}' is present but empty")

    if errors:
        return errors  # can't validate id/domain without them

    # id format: must be <domain_lower>.<slug> matching file path
    expected_id = f"{path.parent.name}.{path.stem}"
    if doc["id"] != expected_id:
        errors.append(f"id '{doc['id']}' doesn't match expected '{expected_id}' (from path)")

    # domain
    if doc["domain"] not in ALLOWED_DOMAINS:
        errors.append(f"domain '{doc['domain']}' not in {sorted(ALLOWED_DOMAINS)}")

    # current_rung
    if doc["current_rung"] not in ALLOWED_RUNGS:
        errors.append(f"current_rung '{doc['current_rung']}' not in {sorted(ALLOWED_RUNGS)}")

    # ladder: must have exactly rung_1, rung_2, rung_3
    ladder = doc.get("ladder", {})
    if not isinstance(ladder, dict):
        errors.append("ladder is not a mapping")
    else:
        for rung_key in ["rung_1", "rung_2", "rung_3"]:
            if rung_key not in ladder:
                errors.append(f"ladder missing '{rung_key}'")
            elif not isinstance(ladder[rung_key], dict):
                errors.append(f"ladder.{rung_key} is not a mapping")
            else:
                for sub in REQUIRED_RUNG:
                    if sub not in ladder[rung_key] or not ladder[rung_key][sub]:
                        errors.append(f"ladder.{rung_key} missing '{sub}'")

    # the_human
    if isinstance(doc.get("the_human"), str) and not doc["the_human"].strip():
        errors.append("the_human is blank")

    # optional list fields
    for list_field in ["breaks_into", "builds_on"]:
        if list_field in doc and not isinstance(doc[list_field], list):
            errors.append(f"'{list_field}' must be a list (may be empty)")

    return errors


def collect_files(args: list[str]) -> list[pathlib.Path]:
    if not args:
        return sorted(CAP_DIR.rglob("*.yaml"))
    paths = []
    for arg in args:
        p = pathlib.Path(arg)
        if not p.is_absolute():
            # try relative to repo root, then capabilities/
            if (ROOT / p).exists():
                p = ROOT / p
            elif (CAP_DIR / p).exists():
                p = CAP_DIR / p
        if p.is_file():
            paths.append(p)
        elif p.is_dir():
            paths.extend(sorted(p.rglob("*.yaml")))
        else:
            print(f"WARNING: not found: {arg}")
    return paths


def main():
    files = collect_files(sys.argv[1:])
    if not files:
        print(f"No capability YAML files found under {CAP_DIR}")
        sys.exit(1)

    passed = failed = 0
    for path in files:
        rel = path.relative_to(ROOT)
        errors = validate_file(path)
        if errors:
            failed += 1
            print(f"FAIL  {rel}")
            for e in errors:
                print(f"      ✗ {e}")
        else:
            passed += 1
            print(f"PASS  {rel}")

    print(f"\n{passed} passed  {failed} failed  ({passed + failed} total)")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
