"""
core/supervisor.py — supervisory veto layer for AKSUMAEL.

Pattern borrowed from DeepMind's ASIMOV-Agentic benchmark (Jul 2026):
the reasoning layer must be able to REFUSE an action proposed by the
executor, and must escalate to a human when genuinely uncertain.

Two tiers:
  Tier 1 — deterministic invariants. No LLM. Sub-millisecond. Never bypassed.
  Tier 2 — mesh-llm review, only for transitions Tier 1 flags AMBIGUOUS.
            Disabled by default (enable_tier2=False); enable after confirming
            Tier 1 adds < 1 ms to the control loop.

Every verdict is logged to live.log. The audit trail is the point:
a veto layer with no log is unfalsifiable.
"""

import json
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

MESH_LLM_URL     = "http://127.0.0.1:9337/v1/chat/completions"
MESH_LLM_TIMEOUT = 2.0          # hard cap — slow veto = failed veto
MESH_LLM_MODEL   = "mesh-llm"


class Ruling(str, Enum):
    ALLOW    = "allow"
    DENY     = "deny"
    ESCALATE = "escalate"


@dataclass
class Verdict:
    ruling:    Ruling
    reason:    str
    tier:      int
    proposed:  str
    substitute: Optional[str] = None   # replacement state on DENY
    latency_ms: float         = 0.0
    meta:       dict          = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "ts":          time.time(),
            "ruling":      self.ruling.value,
            "reason":      self.reason,
            "tier":        self.tier,
            "proposed":    self.proposed,
            "substitute":  self.substitute,
            "latency_ms":  round(self.latency_ms, 2),
            **self.meta,
        }


# ── BeliefProxy ──────────────────────────────────────────────────────────────
# Adapts WorldMemory + live FSM fields to the supervisor's expected interface.
# Passed into every invariant so they never reach into WorldMemory directly.

class BeliefProxy:
    """Thin read-only adapter over WorldMemory for supervisor invariants."""

    def __init__(self, world_mem, mine_pitch: float = 0.0,
                 inventory_count: int = 0, last_vision_ts: float = 0.0):
        self._wm              = world_mem
        self.mine_pitch       = mine_pitch       # camera pitch during MINE (degrees)
        self.inventory_count  = inventory_count
        self.last_vision_ts   = last_vision_ts

    # ── health / hunger ───────────────────────────────────────────
    @property
    def health(self) -> float:
        """0–20 scale."""
        return getattr(self._wm, 'health_pct', 1.0) * 20.0

    @property
    def hunger(self) -> float:
        """0–20 scale."""
        return getattr(self._wm, 'hunger_pct', 1.0) * 20.0

    @property
    def has_food(self) -> bool:
        return bool(getattr(self._wm, 'food_items', []))

    # ── navigation ────────────────────────────────────────────────
    @property
    def orientation(self):
        """Returns facing string ('north'/'south'/'east'/'west') or None."""
        return getattr(self._wm, 'facing', None)

    @property
    def y_level(self) -> float:
        return getattr(self._wm, 'y_level', 64)

    # ── environment ───────────────────────────────────────────────
    @property
    def light_level(self):
        return getattr(self._wm, 'light_level', None)

    @property
    def threats(self) -> list:
        return []   # placeholder — wire in hostile mob list if tracked

    @property
    def inventory_max(self) -> int:
        return 36   # Minecraft fixed slot count

    @property
    def context_key(self) -> str:
        """Coarse spatial key for procedural memory lookups."""
        wm = self._wm
        px = getattr(wm, 'pos_x', None)
        pz = getattr(wm, 'pos_z', None)
        if px is not None and pz is not None:
            return f"{int(px)//32}:{int(pz)//32}"
        return "global"


# ── Tier 1 invariants ─────────────────────────────────────────────────────────
# Each returns a Verdict to veto, or None to pass through.
# Order: life-safety → loop-breaking → perceptual uncertainty.

def inv_combat_while_weak(proposed: str, current: str, belief: BeliefProxy):
    if proposed != "COMBAT":
        return None
    hp = belief.health
    if hp <= 8:
        return Verdict(Ruling.DENY,
                       f"COMBAT refused at health={hp:.0f}; below survival floor",
                       1, proposed, substitute="FLEE")
    return None


def inv_no_dig_down(proposed: str, current: str, belief: BeliefProxy):
    if proposed != "MINE":
        return None
    if belief.mine_pitch < -75:
        return Verdict(Ruling.DENY,
                       "MINE refused: near-vertical downward pitch, void/lava risk",
                       1, proposed, substitute="EXPLORE")
    return None


def inv_starving(proposed: str, current: str, belief: BeliefProxy):
    hunger = belief.hunger
    if hunger <= 3 and proposed not in ("EAT", "FLEE"):
        if belief.has_food:
            return Verdict(Ruling.DENY,
                           f"{proposed} refused: hunger={hunger:.0f}, food in inventory",
                           1, proposed, substitute="EAT")
    return None


def inv_inventory_full(proposed: str, current: str, belief: BeliefProxy):
    if proposed in ("MINE", "COLLECT"):
        cnt = belief.inventory_count
        cap = belief.inventory_max
        if cnt >= cap:
            return Verdict(Ruling.DENY,
                           f"{proposed} refused: inventory {cnt}/{cap} full",
                           1, proposed, substitute="EXPLORE")
    return None


def inv_stale_perception(proposed: str, current: str, belief: BeliefProxy):
    """Acting on stale vision is how agents walk into lava."""
    age = time.time() - belief.last_vision_ts
    if age > 3.0 and proposed in ("APPROACH", "HUNT", "COMBAT", "MINE"):
        return Verdict(Ruling.ESCALATE,
                       f"perception stale by {age:.1f}s; refusing committed action",
                       1, proposed, substitute="EXPLORE",
                       meta={"vision_age_s": round(age, 2)})
    return None


def inv_no_orientation(proposed: str, current: str, belief: BeliefProxy):
    """No heading → can't safely commit to directional approach."""
    if belief.orientation is None and proposed in ("APPROACH", "HUNT"):
        return Verdict(Ruling.ESCALATE,
                       "no facing in belief state; cannot plan approach",
                       1, proposed)
    return None


INVARIANTS = [
    inv_combat_while_weak,
    inv_no_dig_down,
    inv_starving,
    inv_inventory_full,
    inv_stale_perception,
    inv_no_orientation,
]

# Transitions cheap enough to never need Tier 2.
FAST_PATH = frozenset({"EXPLORE", "EAT", "COLLECT"})


# ── Supervisor ────────────────────────────────────────────────────────────────

class Supervisor:
    def __init__(self,
                 escalate_cb: Optional[Callable[["Verdict"], bool]] = None,
                 loop_threshold: int = 3,
                 enable_tier2: bool = False):
        self.escalate_cb    = escalate_cb
        self.loop_threshold = loop_threshold
        self.enable_tier2   = enable_tier2
        self.stats          = {"allow": 0, "deny": 0, "escalate": 0,
                               "tier2_calls": 0}

    def review(self, proposed: str, current: str, belief: BeliefProxy) -> Verdict:
        t0 = time.perf_counter()

        for inv in INVARIANTS:
            v = inv(proposed, current, belief)
            if v is not None:
                v.latency_ms = (time.perf_counter() - t0) * 1000
                return self._finish(v)

        if self.enable_tier2 and proposed not in FAST_PATH:
            v = self._tier2(proposed, current, belief)
            if v is not None:
                v.latency_ms = (time.perf_counter() - t0) * 1000
                return self._finish(v)

        return self._finish(Verdict(
            Ruling.ALLOW, "pass", 1, proposed,
            latency_ms=(time.perf_counter() - t0) * 1000))

    # ----------------------------------------------------------------- private

    def _tier2(self, proposed: str, current: str, belief: BeliefProxy):
        self.stats["tier2_calls"] += 1
        prompt = (
            "You are the safety supervisor for an embodied Minecraft agent. "
            "Approve or refuse the proposed FSM state transition.\n\n"
            f"current_state: {current}\n"
            f"proposed_state: {proposed}\n"
            f"health: {belief.health:.0f}  hunger: {belief.hunger:.0f}\n"
            f"orientation: {belief.orientation}\n"
            f"threats: {belief.threats}\n"
            f"light_level: {belief.light_level}\n"
            f"inventory: {belief.inventory_count}/{belief.inventory_max}\n\n"
            'Reply JSON only: {"ruling":"allow|deny|escalate",'
            '"reason":"<12 words max>","substitute":"<state or null>"}'
        )
        body = json.dumps({
            "model": MESH_LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 80,
            "temperature": 0.0,
        }).encode()
        try:
            req = urllib.request.Request(
                MESH_LLM_URL, data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=MESH_LLM_TIMEOUT) as r:
                raw = json.loads(r.read())
            txt = (raw["choices"][0]["message"]["content"]
                   .replace("```json", "").replace("```", "").strip())
            out  = json.loads(txt)
            ruling = Ruling(out.get("ruling", "allow"))
            if ruling is Ruling.ALLOW:
                return None
            return Verdict(ruling, out.get("reason", "mesh-llm refusal"),
                           2, proposed, substitute=out.get("substitute"))
        except Exception as e:
            print(f"[supervisor] tier2 unavailable ({e}) — failing open")
            return None

    def _finish(self, v: Verdict) -> Verdict:
        self.stats[v.ruling.value] += 1
        if v.ruling is not Ruling.ALLOW:
            print(f'[SUPERVISOR] {v.ruling.value.upper()} {v.proposed}'
                  f' → {v.substitute or "—"} | {v.reason}'
                  f' (tier={v.tier}, {v.latency_ms:.2f}ms)')
        if v.ruling is Ruling.ESCALATE and self.escalate_cb is not None:
            try:
                if self.escalate_cb(v):   # human said proceed
                    v.ruling = Ruling.ALLOW
                    v.reason += " | human override"
            except Exception:
                pass
        return v
