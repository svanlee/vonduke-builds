# ╔══════════════════════════════════════════════════════╗
# ║  Mastermind — Master World Model                      ║
# ║  Aggregates per-agent WorldModel snapshots into one    ║
# ║  shared spatial picture the coordinator can hand back  ║
# ║  out to any agent in the hive.                         ║
# ╚══════════════════════════════════════════════════════╝
#
# Each AKSUMAEL instance already keeps its own core/world_model.py
# (chunks -> {ores_seen, threats_seen, resources, visited_at}), persisted
# to data/world_model.json. Mastermind doesn't replace that — it's the
# hive-level union: every agent's chunk knowledge, tagged with who saw it
# and when, merged per environment so a ground bot mining in "minecraft"
# can benefit from another ground bot's discoveries without either one
# having to be near the other.

import json
import os
import time

MASTER_WORLD_FILE = "data/mastermind_world.json"


class MasterWorldModel:
    def __init__(self, persist_path: str = MASTER_WORLD_FILE):
        self.persist_path = persist_path
        # env_name -> chunk_key -> merged chunk dict (ores_seen/threats_seen/
        # resources/visited_at, plus a 'contributors' set of agent_ids and
        # 'updated_at' wall-clock time for staleness checks)
        self.envs: dict = {}
        self._load()

    # ── Persistence ────────────────────────────────────────────
    def _load(self):
        if not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path) as f:
                data = json.load(f)
            self.envs = data.get("envs", {})
        except Exception as e:
            print(f"[MASTER_WORLD] load error: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)
            with open(self.persist_path, "w") as f:
                json.dump({"envs": self.envs, "saved_at": time.time()}, f, indent=2)
        except Exception as e:
            print(f"[MASTER_WORLD] save error: {e}")

    # ── Merge ──────────────────────────────────────────────────
    def merge_agent_update(self, agent_id: str, env_name: str, world_model_snapshot: dict):
        """Fold one agent's WorldModel.chunks (as produced by
        core/world_model.py's `save()`, i.e. {'chunks': {...}, ...}) into the
        shared picture for `env_name`. Newer sightings win per-ore, and every
        chunk records which agents have contributed to it."""
        chunks = world_model_snapshot.get("chunks", {})
        if not chunks:
            return

        env_bucket = self.envs.setdefault(env_name, {})
        now = time.time()

        for chunk_key, chunk in chunks.items():
            merged = env_bucket.setdefault(chunk_key, {
                "ores_seen": {}, "threats_seen": [], "resources": {},
                "visited_at": None, "contributors": [], "updated_at": now,
            })

            for label, hit in chunk.get("ores_seen", {}).items():
                existing = merged["ores_seen"].get(label)
                if existing is None or hit.get("tick", 0) >= existing.get("tick", 0):
                    merged["ores_seen"][label] = {**hit, "agent_id": agent_id}

            for threat in chunk.get("threats_seen", []):
                merged["threats_seen"].append({**threat, "agent_id": agent_id})
            merged["threats_seen"] = merged["threats_seen"][-20:]  # cap growth

            merged["resources"].update(chunk.get("resources", {}))

            visited = chunk.get("visited_at")
            if visited is not None:
                merged["visited_at"] = max(visited, merged["visited_at"] or 0)

            if agent_id not in merged["contributors"]:
                merged["contributors"].append(agent_id)
            merged["updated_at"] = now

        self.save()

    # ── Query ──────────────────────────────────────────────────
    def get_shared_knowledge(self, env_name: str) -> dict:
        """Return the merged chunk map for `env_name` (empty dict if the
        hive has no knowledge of that environment yet)."""
        return self.envs.get(env_name, {})

    def nearest_ore(self, env_name: str, label: str, position: tuple = None):
        """Return the [x, y, z] of the nearest hive-known `label` ore in
        `env_name`, or None. Mirrors core/world_model.WorldModel.nearest_ore
        but searches every agent's contributed knowledge, not just one."""
        candidates = []
        for chunk in self.get_shared_knowledge(env_name).values():
            hit = chunk.get("ores_seen", {}).get(label)
            if hit:
                candidates.append(hit["pos"])
        if not candidates:
            return None
        if position is None:
            return candidates[0]
        px, _, pz = position

        def _dist(p):
            return ((p[0] - px) ** 2 + (p[2] - pz) ** 2) ** 0.5
        return min(candidates, key=_dist)

    def summary(self, env_name: str) -> str:
        knowledge = self.get_shared_knowledge(env_name)
        if not knowledge:
            return f"[MASTER_WORLD] no shared knowledge for '{env_name}' yet."
        ore_counts = {}
        contributors = set()
        for chunk in knowledge.values():
            contributors.update(chunk.get("contributors", []))
            for label in chunk.get("ores_seen", {}):
                ore_counts[label] = ore_counts.get(label, 0) + 1
        parts = ", ".join(f"{k}×{v}" for k, v in sorted(ore_counts.items(), key=lambda x: -x[1])[:6])
        return (f"[MASTER_WORLD] '{env_name}': {len(knowledge)} chunks from "
                f"{len(contributors)} agent(s). Known ores: {parts or 'none'}.")
