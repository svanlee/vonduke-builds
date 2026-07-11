"""Short-horizon goal stack. Claude can push/pop goals; runtime injects current goal into prompt."""

from collections import deque
import json, os

GOALS_PATH = "data/goals.json"

GOAL_PRIORITIES = {
    "survive_night": 10,
    "eat": 9,
    "flee_danger": 8,
    "find_shelter": 7,
    "mine_diamonds": 5,
    "mine_coal": 4,
    "explore": 2,
    "idle": 0,
}

class GoalStack:
    def __init__(self):
        self.stack: deque = deque(maxlen=5)
        self.current = "explore"
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

    def auto_update(self, world_memory, inventory):
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

    def context_summary(self) -> str:
        return f"Current goal: {self.current}" + (f" (queued: {', '.join(list(self.stack)[-2:])})" if self.stack else "")
