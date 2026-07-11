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

        # Need wood — if no logs seen recently (wood_count is low this session)
        wood_count = getattr(world_memory, 'wood_count', 0)
        inv_logs = (inventory.items.get('log', 0)
                    + inventory.items.get('oak_log', 0)
                    + inventory.items.get('wood', 0))
        if wood_count == 0 and inv_logs < 4 and self.current == "explore":
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

    def context_summary(self) -> str:
        return f"Current goal: {self.current}" + (f" (queued: {', '.join(list(self.stack)[-2:])})" if self.stack else "")
