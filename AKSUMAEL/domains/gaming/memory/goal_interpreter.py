"""
Translates Claude's natural-language goal strings into primitive behaviors.
Uses keyword matching — no extra LLM call needed.
"""

import re

# Keyword -> behavior name mappings
GOAL_MAP = [
    # (regex pattern, behavior_name, params)
    (r"go deeper|dig down|find diamonds|mine down", "dig_down", {}),
    (r"find shelter|build shelter|get inside|night", "find_shelter", {}),
    (r"explore|look around|find cave|search", "explore", {}),
    (r"flee|run away|escape|danger", "flee", {}),
    (r"eat|hungry|food", "eat", {}),
    (r"craft|pickaxe|tools", "craft_pickaxe", {}),
    (r"light|torch|dark", "place_torch", {}),
    (r"swim|water|surface", "swim_up", {}),
]

BEHAVIOR_ACTIONS = {
    "dig_down": [
        {"key": "s", "hold_ms": 200},  # back up first
        {"key": "space"},               # jump
        {"key": "w", "hold_ms": 300},   # forward
        # Then mine — Claude will handle next tick
    ],
    "find_shelter": [
        {"key": "w", "hold_ms": 500},
        {"key": "a", "hold_ms": 300},
    ],
    "explore": [
        {"key": "w", "hold_ms": 800},
        {"look": {"dx": 20, "dy": 0}},  # look right
    ],
    "flee": [
        {"key": "s", "hold_ms": 600},
        {"key": "d", "hold_ms": 300},
        {"key": "space"},
    ],
    "swim_up": [
        {"key": "space"},
        {"key": "space"},
        {"key": "w", "hold_ms": 400},
    ],
}


class GoalInterpreter:
    def __init__(self, goal_stack, crafting_behavior=None):
        self.goal_stack = goal_stack
        self.crafting = crafting_behavior
        self._last_goal_text = ""

    def interpret(self, goal_text: str, objects: list[dict]) -> str | None:
        """
        Parse goal_text, update goal_stack, return behavior name to trigger (or None).
        """
        if not goal_text or goal_text == self._last_goal_text:
            return None
        self._last_goal_text = goal_text
        goal_lower = goal_text.lower()

        for pattern, behavior, params in GOAL_MAP:
            if re.search(pattern, goal_lower):
                print(f"[GOAL] '{goal_text}' -> {behavior}")
                self.goal_stack.push(behavior)
                return behavior

        # Unknown goal — just push it as-is for context
        self.goal_stack.push(goal_text[:32])
        return None

    def execute_behavior(self, behavior_name: str, executor, objects: list[dict]):
        """Execute a simple behavior by name."""
        actions = BEHAVIOR_ACTIONS.get(behavior_name, [])
        for action in actions:
            try:
                executor.execute(action)
            except Exception as e:
                print(f"[GOAL] execute error: {e}")
