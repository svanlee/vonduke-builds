# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.1.0 — Episode Memory (JARVIS-1 pattern)  ║
# ║  Records completed goal attempts, retrieves the most  ║
# ║  similar past episodes to inject as LLM context.      ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time

import config

EPISODES_FILE = os.path.join(config.MEMORY_DIR, 'episodes.jsonl')


class EpisodeMemory:
    """Rolling window of completed episodes: {goal, plan_used, outcome,
    inventory_before, inventory_after, position, tick}. Persisted as JSONL
    (append-friendly, tolerant of partial writes) and capped in RAM to
    config.EPISODE_MEMORY_MAX."""

    def __init__(self):
        self.episodes = []
        self._load()

    def _load(self):
        if not os.path.exists(EPISODES_FILE):
            return
        try:
            with open(EPISODES_FILE) as f:
                lines = f.readlines()
            for line in lines[-config.EPISODE_MEMORY_MAX:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.episodes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            print(f'[EPISODE] load error: {e}')

    def record(self, goal: str, plan: list, outcome: str,
               inv_before: dict, inv_after: dict, position=None, tick: int = 0):
        """outcome: 'success' | 'failure' | 'timeout' (freeform string ok)."""
        episode = {
            'goal':            goal,
            'plan':            plan or [],
            'outcome':         outcome,
            'inventory_before': dict(inv_before or {}),
            'inventory_after':  dict(inv_after or {}),
            'position':        list(position) if position else None,
            'tick':            tick,
            'ts':              time.time(),
        }
        self.episodes.append(episode)
        self.episodes = self.episodes[-config.EPISODE_MEMORY_MAX:]
        try:
            os.makedirs(config.MEMORY_DIR, exist_ok=True)
            with open(EPISODES_FILE, 'a') as f:
                f.write(json.dumps(episode) + '\n')
        except Exception as e:
            print(f'[EPISODE] save error: {e}')
        return episode

    # ── Retrieval ────────────────────────────────────────────────
    @staticmethod
    def _jaccard(a: dict, b: dict) -> float:
        ka, kb = set(a.keys()), set(b.keys())
        if not ka and not kb:
            return 1.0
        union = ka | kb
        if not union:
            return 0.0
        return len(ka & kb) / len(union)

    def retrieve_similar(self, current_goal: str, current_inventory: dict,
                          top_k: int = None) -> list:
        """Return the top_k most relevant past episodes for `current_goal`
        given `current_inventory`, ranked by:
          - same goal (hard filter — falls back to all episodes if none match)
          - Jaccard similarity on inventory item keys
          - successful outcomes preferred (tiebreak boost)
        """
        top_k = top_k or config.EPISODE_RETRIEVE_TOP_K
        if not self.episodes:
            return []

        same_goal = [e for e in self.episodes if e.get('goal') == current_goal]
        pool = same_goal if same_goal else self.episodes

        def _score(e):
            sim = self._jaccard(current_inventory or {}, e.get('inventory_before', {}))
            bonus = 0.25 if e.get('outcome') == 'success' else 0.0
            return sim + bonus

        ranked = sorted(pool, key=_score, reverse=True)
        return ranked[:top_k]

    def context_snippet(self, current_goal: str, current_inventory: dict,
                         top_k: int = None) -> str:
        """Render retrieved episodes as a short text block for LLM context —
        'Last time you tried X, you did Y and it worked/failed'."""
        similar = self.retrieve_similar(current_goal, current_inventory, top_k)
        if not similar:
            return ''
        lines = ['[EPISODES] Past attempts at similar goals:']
        for e in similar:
            plan_str = ' -> '.join(e.get('plan') or []) or 'no plan recorded'
            lines.append(
                f"  - goal={e.get('goal')} outcome={e.get('outcome')} "
                f"plan=[{plan_str}]"
            )
        return '\n'.join(lines)
