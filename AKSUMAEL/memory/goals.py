"""Short-horizon goal stack. Claude can push/pop goals; runtime injects current goal into prompt."""

from collections import deque
import json, os, time

import config

GOALS_PATH = "data/goals.json"
RETIRED_GOALS_LOG = os.path.join(config.MEMORY_DIR, "retired_goals.jsonl")
INJECTED_GOALS_PATH = "data/injected_goals.json"

# Goals that never retire regardless of age — the base/standing goals.
_NEVER_RETIRE = frozenset({"survive", "survive_night", "explore", "eat",
                            "flee_danger", "find_shelter", "return_to_base"})

# Materials required per pickaxe-crafting tier (mirrors suggest_craft_goal).
_CRAFT_REQUIREMENTS = {
    "craft_wood_pickaxe":    {"planks": 3, "stick": 2},
    "craft_stone_pickaxe":   {"cobblestone": 3, "stick": 2},
    "craft_iron_pickaxe":    {"iron_ingot": 3, "stick": 2},
    "craft_diamond_pickaxe": {"diamond": 3, "stick": 2},
}
_CRAFT_RESULT_ITEM = {
    "craft_wood_pickaxe":    "wooden_pickaxe",
    "craft_stone_pickaxe":   "stone_pickaxe",
    "craft_iron_pickaxe":    "iron_pickaxe",
    "craft_diamond_pickaxe": "diamond_pickaxe",
}
_PLANK_VARIANTS = ("oak_planks", "spruce_planks", "birch_planks",
                   "jungle_planks", "acacia_planks", "dark_oak_planks")

GOAL_PRIORITIES = {
    "survive_night": 10,
    "eat": 9,
    "flee_danger": 8,
    "return_to_base": 8,
    "find_shelter": 7,
    "mine_diamonds": 5,
    "mine_coal": 4,
    "find_and_chop_tree": 3,
    "find_food": 3,
    "fish_for_food": 2,
    "plant_crops": 2,
    "explore": 2,
    "idle": 0,
}

class GoalStack:
    def __init__(self):
        self.stack: deque = deque(maxlen=5)
        self.current = "explore"
        # ── Goal retirement (v1.1) — tick a goal was first observed as
        # current, tracked by name. Detected via check_retirement() so it
        # works whether the goal changed via push()/pop() or a direct
        # `goals.current = "..."` assignment (both patterns are used).
        self._pushed_at   = {}
        self._last_seen   = None
        self.last_retirement = None   # {'goal','reason','ticks_active'} of the most recent retirement
        # Consecutive-timeout tracking for find_and_chop_tree — after 3
        # timeouts in a row, back off from re-queueing it for a while so
        # AKSUMAEL doesn't spin in a tree-search loop forever.
        self._chop_tree_fail_streak   = 0
        self._chop_tree_blocked_until = 0
        self._load()

    def _load(self):
        if os.path.exists(GOALS_PATH):
            with open(GOALS_PATH) as f:
                data = json.load(f)
                self.current = data.get("current", "explore")
                self.stack = deque(data.get("stack", []), maxlen=5)

    def save(self):
        os.makedirs(os.path.dirname(GOALS_PATH), exist_ok=True)
        with open(GOALS_PATH, "w") as f:
            json.dump({"current": self.current, "stack": list(self.stack)}, f)

    def push(self, goal: str):
        self.stack.append(self.current)
        self.current = goal
        self.save()

    def pop(self):
        if self.stack:
            self.current = self.stack.pop()
        else:
            self.current = "explore"
        self.save()

    def auto_update(self, world_memory, inventory, tick: int = 0):
        """Heuristic goal updates based on world state."""
        # Hunger overrides everything
        if hasattr(world_memory, 'hunger_level') and world_memory.hunger_level < 6:
            if self.current != "eat":
                self.push("eat")
        elif self.current == "eat" and hasattr(world_memory, 'hunger_level') and world_memory.hunger_level > 14:
            self.pop()

        # Diamond goal if we have a pickaxe and not too many diamonds
        diamonds = inventory.items.get("diamond", 0)
        if diamonds < 3 and self.current == "explore":
            self.push("mine_diamonds")

        # Need wood — if no logs seen recently (wood_count is low this session)
        wood_count = getattr(world_memory, 'wood_count', 0)
        inv_logs = (inventory.items.get('log', 0)
                    + inventory.items.get('oak_log', 0)
                    + inventory.items.get('wood', 0))
        if (wood_count == 0 and inv_logs < 4 and self.current == "explore"
                and tick >= self._chop_tree_blocked_until):
            self.push("find_and_chop_tree")

        # Need food — hunger below 60% and no food in inventory
        hunger_frac = (world_memory.hunger_level / 20.0
                       if hasattr(world_memory, 'hunger_level') else 1.0)
        food_items = getattr(world_memory, 'food_items', [])
        has_food = bool(food_items) or any(
            inventory.items.get(f, 0) > 0
            for f in ('cooked_beef', 'cooked_porkchop', 'cooked_chicken',
                       'bread', 'apple', 'carrot', 'potato', 'cooked_mutton',
                       'salmon', 'cooked_salmon', 'cooked_cod', 'cod')
        )
        if hunger_frac < 0.60 and not has_food and self.current not in ("eat", "find_food", "fish_for_food"):
            self.push("find_food")

        # Fishing opportunity — has fishing rod and near water
        has_rod = inventory.items.get('fishing_rod', 0) > 0
        near_water = getattr(world_memory, 'near_water', False)
        if has_rod and near_water and not has_food and self.current == "explore":
            self.push("fish_for_food")

        # Farming opportunity — has seeds and found farmland
        has_seeds = (inventory.items.get('wheat_seeds', 0) > 0
                     or inventory.items.get('carrot', 0) > 0
                     or inventory.items.get('potato', 0) > 0)
        seen_farmland = world_memory.seen_objects.get('farmland', 0) > 0 if hasattr(world_memory, 'seen_objects') else False
        if has_seeds and seen_farmland and self.current == "explore":
            self.push("plant_crops")

    def current_goal(self) -> str:
        """Return the current goal string."""
        return self.current

    def has_goal(self, goal: str) -> bool:
        """Return True if goal is the active goal or anywhere in the stack."""
        return self.current == goal or goal in self.stack

    # Any goal name starting with 'craft_', plus these legacy aliases, counts
    # as "we are in the middle of crafting something" for gating purposes.
    _CRAFT_ALIASES = {'craft_tool', 'crafting'}

    def is_craft_goal(self, goal: str | None = None) -> bool:
        """True if `goal` (default: current goal) is any crafting-related goal."""
        g = self.current if goal is None else goal
        return g.startswith('craft_') or g in self._CRAFT_ALIASES

    def has_craft_goal(self) -> bool:
        """True if a crafting goal is active or queued anywhere in the stack."""
        return self.is_craft_goal(self.current) or any(self.is_craft_goal(g) for g in self.stack)

    def suggest_craft_goal(self, cached_inv: dict, chest_inv: dict | None = None):
        """Auto-push the highest-tier craft_*_pickaxe goal the instant inventory
        (or the base chest) has enough materials — replaces waiting for the LLM
        to notice and decide.  Escalates wood -> stone -> iron -> diamond,
        skipping any tier whose pickaxe (or better) is already owned.  Called
        from the runtime loop after a successful inventory read (uses the
        {item:count} flat dict).  chest_inv, if given, is a ChestManager-style
        {item: {count, slot}} dict — its counts are added to cached_inv's when
        checking totals.  Hard-limits to one craft_* goal anywhere in the goal
        state at a time."""
        chest_inv = chest_inv or {}

        def _chest_count(item: str) -> int:
            v = chest_inv.get(item, 0)
            return v.get('count', 0) if isinstance(v, dict) else v

        def _total(item: str) -> int:
            return cached_inv.get(item, 0) + _chest_count(item)

        # Don't push if inventory read returned nothing (failed scan)
        if not cached_inv and not chest_inv:
            return

        if self.has_craft_goal():
            return   # already queued — don't stack duplicates

        TIERS = ('wooden', 'stone', 'iron', 'diamond')
        best_owned = -1
        for i, tier in enumerate(TIERS):
            if _total(f'{tier}_pickaxe') > 0:
                best_owned = i

        # Diamond pickaxe: 3 diamond + 2 stick
        if best_owned < 3 and _total('diamond') >= 3 and _total('stick') >= 2:
            print('[GOALS] auto-push craft_diamond_pickaxe (has diamond+sticks)')
            self.push('craft_diamond_pickaxe')
            return

        # Iron pickaxe: 3 iron_ingot + 2 stick
        if best_owned < 2 and _total('iron_ingot') >= 3 and _total('stick') >= 2:
            print('[GOALS] auto-push craft_iron_pickaxe (has iron+sticks)')
            self.push('craft_iron_pickaxe')
            return

        # Stone pickaxe: 3 cobblestone + 2 stick
        if best_owned < 1 and _total('cobblestone') >= 3 and _total('stick') >= 2:
            print('[GOALS] auto-push craft_stone_pickaxe (has cobblestone+sticks)')
            self.push('craft_stone_pickaxe')
            return

        # Wooden pickaxe: 3 planks + 2 stick
        planks = sum(_total(p) for p in (
            'oak_planks', 'spruce_planks', 'birch_planks',
            'jungle_planks', 'acacia_planks', 'dark_oak_planks',
        ))
        if best_owned < 0 and planks >= 3 and _total('stick') >= 2:
            print('[GOALS] auto-push craft_wood_pickaxe (has planks+sticks)')
            self.push('craft_wood_pickaxe')

    def context_summary(self) -> str:
        return f"Current goal: {self.current}" + (f" (queued: {', '.join(list(self.stack)[-2:])})" if self.stack else "")

    # ── Goal retirement ──────────────────────────────────────────
    def check_retirement(self, tick: int, world=None, inventory=None):
        """Call once per runtime tick. Retires the current goal (pops it)
        when it's either been achieved (success) or looks unachievable
        after sitting active too long (timeout). Logs every retirement to
        RETIRED_GOALS_LOG for later analysis. No-op for standing goals in
        _NEVER_RETIRE (survive/explore/etc — those are never "achieved")."""
        goal = self.current
        if goal != self._last_seen:
            self._pushed_at[goal] = tick
            self._last_seen = goal
        if goal in _NEVER_RETIRE:
            return

        age = tick - self._pushed_at.get(goal, tick)
        items = getattr(inventory, "items", {}) if inventory is not None else {}

        # ── Success retirement — postcondition already met ─────────
        if goal in _CRAFT_RESULT_ITEM:
            if items.get(_CRAFT_RESULT_ITEM[goal], 0) > 0:
                self._retire(tick, goal, "success: already crafted", age, world)
                return
        elif goal.startswith("mine_"):
            ore_item = _mine_goal_to_item(goal)
            base_item = ore_item.replace("_ore", "")
            if items.get(base_item, 0) > 0 or items.get(ore_item, 0) > 0:
                self._retire(tick, goal, "success: ore already in inventory", age, world)
                return

        # ── Timeout retirement — goal looks unachievable ────────────
        if goal.startswith("mine_") and age > 60 and world is not None:
            ore_label = _mine_goal_to_item(goal)
            if world.nearest_ore(ore_label) is None:
                self._retire(tick, goal, f"timeout: no known {ore_label} location", age, world)
                return

        if self.is_craft_goal(goal) and age > 100:
            req = _CRAFT_REQUIREMENTS.get(goal)
            # req is None for craft goals outside the tiered pickaxe map
            # (e.g. "craft_pickaxe" pushed by the keyword-based goal
            # interpreter) — fall through to the generic max-age check
            # below instead of leaving those goals stuck forever.
            if req is not None:
                have = dict(items)
                have["planks"] = sum(have.get(p, 0) for p in _PLANK_VARIANTS)
                if any(have.get(item, 0) < count for item, count in req.items()):
                    self._retire(tick, goal, "timeout: materials still missing", age, world)
                    return

        if age > config.GOAL_MAX_AGE_TICKS:
            self._retire(tick, goal, "timeout: max age exceeded", age, world)

    def _retire(self, tick: int, goal: str, reason: str, ticks_active: int, world=None):
        position = list(world.position) if world is not None and getattr(world, 'position', None) else None
        print(f'[GOALS] retiring "{goal}" — {reason} ({ticks_active} ticks active)')
        self._pushed_at.pop(goal, None)

        if goal == "find_and_chop_tree":
            if reason.startswith("timeout"):
                self._chop_tree_fail_streak += 1
                if self._chop_tree_fail_streak >= 3:
                    self._chop_tree_blocked_until = tick + 500
                    self._chop_tree_fail_streak = 0
                    print('[GOALS] find_and_chop_tree timed out 3x in a row — '
                          'backing off for 500 ticks')
            else:
                self._chop_tree_fail_streak = 0
        # A stuck craft_pickaxe goal (see is_craft_goal retirement above)
        # is usually downstream of repeated tree-finding failures — count
        # it toward the same streak so the backoff kicks in either way.
        elif goal == "craft_pickaxe" and reason.startswith("timeout"):
            self._chop_tree_fail_streak += 1
            if self._chop_tree_fail_streak >= 3:
                self._chop_tree_blocked_until = tick + 500
                self._chop_tree_fail_streak = 0
                print('[GOALS] craft_pickaxe timed out 3x in a row — '
                      'backing off find_and_chop_tree for 500 ticks')
        self.last_retirement = {'goal': goal, 'reason': reason, 'ticks_active': ticks_active}
        self.pop()
        try:
            os.makedirs(config.MEMORY_DIR, exist_ok=True)
            with open(RETIRED_GOALS_LOG, "a") as f:
                f.write(json.dumps({
                    "goal": goal, "reason": reason,
                    "ticks_active": ticks_active, "position": position,
                    "tick": tick, "ts": time.time(),
                }) + "\n")
        except Exception as e:
            print(f'[GOALS] retired-goal log error: {e}')

    # ── Mastermind hive goal injection ─────────────────────────
    def check_injected_goals(self):
        """Call once per runtime tick (alongside check_retirement). Drains
        data/injected_goals.json — written by mastermind/agent_client.py
        when the hive coordinator assigns this agent a goal — and pushes
        each queued goal onto the stack in order. No-op, and cheap, when
        the hive isn't enabled or the queue is empty."""
        if not os.path.exists(INJECTED_GOALS_PATH):
            return
        try:
            with open(INJECTED_GOALS_PATH) as f:
                data = json.load(f)
            queue = data.get("queue", [])
            if not queue:
                return
            for item in queue:
                goal = item.get("goal")
                if goal:
                    print(f"[GOALS] hive-injected goal: {goal} ({item.get('reason', 'mastermind')})")
                    self.push(goal)
            os.remove(INJECTED_GOALS_PATH)
        except Exception as e:
            print(f"[GOALS] injected-goals read error: {e}")


def _mine_goal_to_item(goal: str) -> str:
    """'mine_diamonds' -> 'diamond_ore', 'mine_coal' -> 'coal_ore'."""
    base = goal[len("mine_"):].rstrip("s")
    return base if base.endswith("_ore") else f"{base}_ore"
