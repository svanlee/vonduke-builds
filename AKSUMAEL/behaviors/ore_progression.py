# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Ore Progression                    ║
# ║  Sequences mining goals to collect one stack (64)    ║
# ║  of each ore tier, crafting the required pickaxe     ║
# ║  along the way.                                       ║
# ╚══════════════════════════════════════════════════════╝

"""
Ore progression order (lowest → highest pickaxe tier):

  Wood pickaxe:   coal ore, iron ore
  Stone pickaxe:  copper ore, lapis ore
  Iron pickaxe:   gold ore, redstone ore, emerald ore, diamond ore

The module reads the InventoryTracker for collected counts and a small
state file (data/ore_progress.json) to track crafted pickaxe tiers
(since the tracker stores pickaxes under weird recipe-name keys).
"""

import json
import os

_STATE_PATH = os.path.join('data', 'ore_progress.json')

# (tracker_item_key, mine_goal, required_pickaxe_tier)
ORE_SEQUENCE = [
    ('coal',         'mine_coal_ore',     'wood'),
    ('iron_ore',     'mine_iron_ore',     'wood'),
    ('copper_ingot', 'mine_copper_ore',   'stone'),
    ('lapis',        'mine_lapis_ore',    'stone'),
    ('gold_ore',     'mine_gold_ore',     'iron'),
    ('redstone',     'mine_redstone_ore', 'iron'),
    ('emerald',      'mine_emerald_ore',  'iron'),
    ('diamond',      'mine_diamond_ore',  'iron'),
]

STACK = 64   # items per "one stack" target

_TIER_RANK = {'wood': 0, 'stone': 1, 'iron': 2, 'diamond': 3}

# Craft goal that produces each tier
_CRAFT_FOR_TIER = {
    'wood':    'craft_wood_pickaxe',
    'stone':   'craft_stone_pickaxe',
    'iron':    'craft_iron_pickaxe',
    'diamond': 'craft_diamond_pickaxe',
}

# Item keys as stored by InventoryTracker.on_craft_success()
# (the fallback branch sets item = recipe_name since _CRAFT_YIELDS has no pickaxe entries)
_PICKAXE_INV_KEY = {
    'wood':    'craft_wood_pickaxe',
    'stone':   'craft_stone_pickaxe',
    'iron':    'craft_iron_pickaxe',
    'diamond': 'craft_diamond_pickaxe',
}

# All mine_ goals we manage (used for duplicate-push guard in runtime.py)
ALL_MINE_GOALS = frozenset(g for _, g, _ in ORE_SEQUENCE)


# ── State persistence ────────────────────────────────────────────

def _load() -> dict:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state: dict):
    os.makedirs(os.path.dirname(_STATE_PATH) or '.', exist_ok=True)
    with open(_STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


def mark_pickaxe_crafted(tier: str):
    """Call from runtime when a pickaxe craft succeeds."""
    state = _load()
    state[f'crafted_{tier}'] = True
    _save(state)
    print(f'[ORE_PROG] recorded {tier} pickaxe crafted')


# ── Core API ─────────────────────────────────────────────────────

def best_pickaxe_tier(inv: dict, state: dict) -> str | None:
    """Highest pickaxe tier available — checks tracker AND state file."""
    for tier in ('diamond', 'iron', 'stone', 'wood'):
        if inv.get(_PICKAXE_INV_KEY[tier], 0) > 0 or state.get(f'crafted_{tier}'):
            return tier
    return None


def next_goal(inv_tracker) -> tuple[str | None, str]:
    """Return (goal_str, reason) for the next ore-progression step.

    May return a craft_* goal when a better pickaxe is needed.
    Returns (None, 'complete') when all stacks are collected.
    """
    inv   = dict(inv_tracker.items)
    state = _load()
    tier  = best_pickaxe_tier(inv, state)
    rank  = _TIER_RANK.get(tier, -1) if tier else -1

    for ore_item, mine_goal, req_tier in ORE_SEQUENCE:
        collected = inv.get(ore_item, 0)
        if collected >= STACK:
            continue   # stack complete — skip

        req_rank = _TIER_RANK[req_tier]

        if rank < req_rank:
            # Need a better pickaxe before we can mine this ore
            craft = _CRAFT_FOR_TIER[req_tier]
            print(f'[ORE_PROG] need {req_tier} pickaxe for {ore_item} → {craft}')
            return craft, f'need {req_tier} pickaxe to mine {ore_item}'

        print(f'[ORE_PROG] target: {ore_item} ({collected}/{STACK}) → {mine_goal}')
        return mine_goal, f'{ore_item} {collected}/{STACK}'

    return None, 'all ore stacks complete!'


def progress_summary(inv_tracker) -> str:
    """Short progress string for the log."""
    inv = dict(inv_tracker.items)
    parts = []
    for ore_item, _, _ in ORE_SEQUENCE:
        n = min(inv.get(ore_item, 0), STACK)
        parts.append(f'{ore_item.split("_")[0]}:{n}/{STACK}')
    return ' | '.join(parts)
