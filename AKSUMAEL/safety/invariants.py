"""
safety/invariants.py — Tier 1 deterministic invariants for AKSUMAEL.

Each invariant is a callable:
    (proposed: str, current: str, belief: BeliefBase) -> Verdict | None

Return a Verdict to veto; return None to pass through to the next invariant.

Thresholds are module-level constants so they're easy to tune per deployment
without touching the logic.

To port to a new agent:
    1. Copy this file to your project.
    2. Adjust the THRESHOLD_* constants.
    3. Adjust the state names in each condition.
    4. Pass your invariant list to Supervisor(invariants=[...]).
"""

import time

from safety.core import Ruling, Verdict

# ── Thresholds (tune per deployment) ─────────────────────────────────────────
THRESHOLD_HEALTH_FLOOR   = 8.0    # 0–20 scale; COMBAT blocked at or below this
THRESHOLD_PITCH_DIG_DOWN = -75.0  # degrees; MINE blocked below this (downward)
THRESHOLD_HUNGER_FLOOR   = 3.0    # 0–20 scale; force EAT at or below this
THRESHOLD_VISION_STALE_S = 3.0    # seconds; committed actions blocked if older

# States that are "committed" actions — risky without fresh perception
COMMITTED_STATES = frozenset({"APPROACH", "HUNT", "COMBAT", "MINE"})

# States that never trigger force-EAT redirect
EAT_EXEMPT = frozenset({"EAT", "FLEE"})

# States where a full inventory is a problem
INVENTORY_BLOCKED = frozenset({"MINE", "COLLECT"})

# States where missing orientation is dangerous
ORIENTATION_REQUIRED = frozenset({"APPROACH", "HUNT"})

# Transitions cheap enough to skip Tier 2 review
FAST_PATH = frozenset({"EXPLORE", "EAT", "COLLECT"})


# ── Invariants ────────────────────────────────────────────────────────────────

def inv_combat_while_weak(proposed: str, current: str, belief) -> Verdict | None:
    """Block COMBAT when health is critically low — FLEE instead."""
    if proposed != "COMBAT":
        return None
    hp = belief.health
    if hp <= THRESHOLD_HEALTH_FLOOR:
        return Verdict(Ruling.DENY,
                       f"COMBAT refused at health={hp:.0f}; below survival floor",
                       1, proposed, substitute="FLEE")
    return None


def inv_no_dig_down(proposed: str, current: str, belief) -> Verdict | None:
    """Refuse near-vertical downward MINE — void/lava risk."""
    if proposed != "MINE":
        return None
    if belief.mine_pitch < THRESHOLD_PITCH_DIG_DOWN:
        return Verdict(Ruling.DENY,
                       "MINE refused: near-vertical downward pitch, void/lava risk",
                       1, proposed, substitute="EXPLORE")
    return None


def inv_starving(proposed: str, current: str, belief) -> Verdict | None:
    """Force EAT when hunger is critical and food is available."""
    hunger = belief.hunger
    if hunger <= THRESHOLD_HUNGER_FLOOR and proposed not in EAT_EXEMPT:
        if belief.has_food:
            return Verdict(Ruling.DENY,
                           f"{proposed} refused: hunger={hunger:.0f}, food in inventory",
                           1, proposed, substitute="EAT")
    return None


def inv_inventory_full(proposed: str, current: str, belief) -> Verdict | None:
    """Block gather/mine actions when inventory is full."""
    if proposed in INVENTORY_BLOCKED:
        cnt = belief.inventory_count
        cap = belief.inventory_max
        if cnt >= cap:
            return Verdict(Ruling.DENY,
                           f"{proposed} refused: inventory {cnt}/{cap} full",
                           1, proposed, substitute="EXPLORE")
    return None


def inv_stale_perception(proposed: str, current: str, belief) -> Verdict | None:
    """Acting on stale vision risks walking into lava — escalate instead."""
    age = time.time() - belief.last_vision_ts
    if age > THRESHOLD_VISION_STALE_S and proposed in COMMITTED_STATES:
        return Verdict(Ruling.ESCALATE,
                       f"perception stale by {age:.1f}s; refusing committed action",
                       1, proposed, substitute="EXPLORE",
                       meta={"vision_age_s": round(age, 2)})
    return None


def inv_no_orientation(proposed: str, current: str, belief) -> Verdict | None:
    """No heading = can't safely commit to directional movement."""
    if belief.orientation is None and proposed in ORIENTATION_REQUIRED:
        return Verdict(Ruling.ESCALATE,
                       "no facing in belief state; cannot plan approach",
                       1, proposed)
    return None


# Ordered: life-safety → loop-breaking → perceptual uncertainty
INVARIANTS = [
    inv_combat_while_weak,
    inv_no_dig_down,
    inv_starving,
    inv_inventory_full,
    inv_stale_perception,
    inv_no_orientation,
]
