#!/usr/bin/env python3
"""
catalog/graph.py — Build catalog/graph.json from capabilities/*.yaml

Produces a JSON graph with:
  nodes: [{id, name, domain, status, current_rung, category}]
  edges: [{source, target, rel}]  rel = "breaks_into" | "builds_on"

Usage:
    python3 catalog/graph.py               # writes catalog/graph.json
    python3 catalog/graph.py --print       # pretty-print to stdout instead
"""

import json
import pathlib
import sys

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed — run: pip install pyyaml")
    sys.exit(1)

ROOT     = pathlib.Path(__file__).parent.parent
CAP_DIR  = ROOT / "capabilities"
OUT_PATH = ROOT / "catalog" / "graph.json"


def load_capabilities() -> list[dict]:
    caps = []
    for path in sorted(CAP_DIR.rglob("*.yaml")):
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
            if isinstance(doc, dict) and "id" in doc:
                doc["_path"] = str(path.relative_to(ROOT))
                caps.append(doc)
        except Exception as e:
            print(f"WARNING: skipping {path}: {e}", file=sys.stderr)
    return caps


def build_graph(caps: list[dict]) -> dict:
    nodes = []
    edges = []
    known_ids = {c["id"] for c in caps}

    for cap in caps:
        nodes.append({
            "id":           cap["id"],
            "name":         cap.get("name", cap["id"]),
            "domain":       cap.get("domain", "UNKNOWN"),
            "category":     cap.get("category", ""),
            "status":       cap.get("status", ""),
            "current_rung": cap.get("current_rung", 0),
        })

        for child in cap.get("breaks_into") or []:
            edges.append({"source": cap["id"], "target": child, "rel": "breaks_into"})

        for dep in cap.get("builds_on") or []:
            edges.append({"source": cap["id"], "target": dep, "rel": "builds_on"})

    # flag edges to nodes not yet in the catalog
    for edge in edges:
        edge["target_exists"] = edge["target"] in known_ids

    return {"nodes": nodes, "edges": edges}


def main():
    caps  = load_capabilities()
    graph = build_graph(caps)

    if "--print" in sys.argv:
        print(json.dumps(graph, indent=2))
        return

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(graph, f, indent=2)
    print(f"Wrote {OUT_PATH}  ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")


if __name__ == "__main__":
    main()
