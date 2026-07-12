# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.1.0 — HTN-style Compositional Planner    ║
# ║  Decomposes high-level goals into the ordered chain   ║
# ║  of GoalStack goal-strings needed to reach them.      ║
# ╚══════════════════════════════════════════════════════╝
#
# This does NOT replace the reactive FSM/skill system — mining/crafting
# execution is still handled by GameFSM, SkillSystem, and CraftingBehavior.
# The planner only decides WHICH goal string to push next onto GoalStack so
# those existing systems have the right thing to work toward (e.g. pushing
# "mine_iron" before "craft_iron_pickaxe" when no iron ore has been mined
# yet), instead of waiting for the LLM to notice the dependency itself.

_PLANK_VARIANTS = ('oak_planks', 'spruce_planks', 'birch_planks',
                    'jungle_planks', 'acacia_planks', 'dark_oak_planks')


def _total_planks(inv: dict) -> int:
    return sum(inv.get(p, 0) for p in _PLANK_VARIANTS)


def _have(inv: dict, item: str, count: int) -> bool:
    if item == 'planks_any':
        return _total_planks(inv) >= count
    return inv.get(item, 0) >= count


# node_key -> {goal, check_item, check_count, prereq, needs}
# `needs` (optional) documents the materials the goal itself requires —
# informational for now, satisfaction is judged by check_item/check_count
# (i.e. "has the output already been produced").
TECH_TREE = {
    'planks': {
        'goal': 'find_and_chop_tree', 'check_item': 'planks_any', 'check_count': 3,
        'prereq': None, 'needs': {},
    },
    'wood_pickaxe': {
        'goal': 'craft_wood_pickaxe', 'check_item': 'wooden_pickaxe', 'check_count': 1,
        'prereq': 'planks', 'needs': {'planks_any': 3, 'stick': 2},
    },
    'cobblestone': {
        'goal': 'mine_stone', 'check_item': 'cobblestone', 'check_count': 3,
        'prereq': 'wood_pickaxe', 'needs': {},
    },
    'stone_pickaxe': {
        'goal': 'craft_stone_pickaxe', 'check_item': 'stone_pickaxe', 'check_count': 1,
        'prereq': 'cobblestone', 'needs': {'cobblestone': 3, 'stick': 2},
    },
    'iron_ore': {
        'goal': 'mine_iron', 'check_item': 'iron_ore', 'check_count': 1,
        'prereq': 'stone_pickaxe', 'needs': {},
    },
    'iron_ingot': {
        'goal': 'smelt_iron', 'check_item': 'iron_ingot', 'check_count': 3,
        'prereq': 'iron_ore', 'needs': {'iron_ore': 3, 'coal': 1},
    },
    'iron_pickaxe': {
        'goal': 'craft_iron_pickaxe', 'check_item': 'iron_pickaxe', 'check_count': 1,
        'prereq': 'iron_ingot', 'needs': {'iron_ingot': 3, 'stick': 2},
    },
    'diamond': {
        'goal': 'mine_diamonds', 'check_item': 'diamond', 'check_count': 3,
        'prereq': 'iron_pickaxe', 'needs': {},
    },
    'diamond_pickaxe': {
        'goal': 'craft_diamond_pickaxe', 'check_item': 'diamond_pickaxe', 'check_count': 1,
        'prereq': 'diamond', 'needs': {'diamond': 3, 'stick': 2},
    },
}

# Base-to-summit order — the frontier of capability walk in next_achievable().
_ORDER = ['planks', 'wood_pickaxe', 'cobblestone', 'stone_pickaxe',
          'iron_ore', 'iron_ingot', 'iron_pickaxe', 'diamond', 'diamond_pickaxe']

# goal string -> node key, for callers that only know the goal name.
_GOAL_TO_NODE = {node['goal']: key for key, node in TECH_TREE.items()}


class Planner:
    """HTN-style planner over TECH_TREE."""

    def _resolve_node(self, goal_or_node: str) -> str | None:
        if goal_or_node in TECH_TREE:
            return goal_or_node
        return _GOAL_TO_NODE.get(goal_or_node)

    def _satisfied(self, node_key: str, inventory: dict) -> bool:
        node = TECH_TREE.get(node_key)
        if node is None:
            return True
        return _have(inventory, node['check_item'], node['check_count'])

    def decompose(self, goal: str, inventory: dict, world=None) -> list:
        """Return the ordered list of sub-goals (goal strings) still needed
        to achieve `goal`, root-prerequisite first. Empty list means either
        `goal` is already satisfied or it isn't a tech-tree goal at all (in
        which case the reactive/LLM layer handles it directly)."""
        target = self._resolve_node(goal)
        if target is None:
            return []

        chain: list = []
        seen: set = set()

        def _walk(node_key):
            if node_key is None or node_key in seen:
                return
            seen.add(node_key)
            node = TECH_TREE.get(node_key)
            if node is None:
                return
            _walk(node['prereq'])
            if not self._satisfied(node_key, inventory):
                chain.append(node['goal'])

        _walk(target)
        return chain

    def next_achievable(self, inventory: dict, world=None) -> str | None:
        """Given current inventory, return the goal at the frontier of
        capability. TECH_TREE's _ORDER is a linear progression, so the
        frontier is simply the node right after the furthest node already
        satisfied — this avoids suggesting a goal (e.g. "get more planks")
        that's technically unsatisfied in isolation but already obsolete
        because a later node (e.g. the stone pickaxe) is done. Returns None
        once everything in the tree is satisfied (diamond pickaxe in hand —
        beyond this, the LLM/curriculum should aim at the Nether/End)."""
        highest_satisfied = -1
        for i, node_key in enumerate(_ORDER):
            if self._satisfied(node_key, inventory):
                highest_satisfied = i
        if highest_satisfied + 1 >= len(_ORDER):
            return None
        return TECH_TREE[_ORDER[highest_satisfied + 1]]['goal']
