"""Simple inventory tracker — AKSUMAEL can't read the inventory screen directly,
so we track what it mines/picks up and inject a summary into Claude's context."""

from collections import defaultdict
import json, os

INVENTORY_PATH = "data/inventory.json"

# Low-value blocks worth dropping once the inventory is full.
JUNK_ITEMS = ("cobblestone", "dirt", "gravel")

# This tracker has no per-slot model of the real 36-slot inventory (27 main
# + 9 hotbar) — it only counts inferred item gains. Treat total tracked
# count exceeding this as a rough proxy for "probably full."
FULL_INVENTORY_SLOTS = 27

class InventoryTracker:
    def __init__(self):
        self.items = defaultdict(int)
        self._load()

    def _load(self):
        if os.path.exists(INVENTORY_PATH):
            with open(INVENTORY_PATH) as f:
                self.items.update(json.load(f))

    def save(self):
        os.makedirs(os.path.dirname(INVENTORY_PATH), exist_ok=True)
        with open(INVENTORY_PATH, "w") as f:
            json.dump(dict(self.items), f)

    def on_skill_fired(self, skill_name: str):
        """Infer item gains from skill names."""
        gains = {
            "mine_diamond_ore": ("diamond", 1),
            "mine_emerald_ore": ("emerald", 1),
            "mine_redstone_ore": ("redstone", 4),
            "mine_copper_ore": ("copper_ingot", 1),
            "mine_coal_ore": ("coal", 1),
            "mine_iron_ore": ("iron_ore", 1),
            "mine_gold_ore": ("gold_ore", 1),
            "mine_lapis_ore": ("lapis", 6),
            "chop_tree": ("oak_log", 1),
            "chop_birch_tree": ("birch_log", 1),
        }
        if skill_name in gains:
            item, qty = gains[skill_name]
            before = self.items[item]
            self.items[item] += qty
            print(f'[INV] {item}: {before} -> {self.items[item]} (+{qty} from {skill_name})')
            # 'wood' is a species-agnostic aggregate every log gain rolls
            # into, so the threshold logic in wood_subgoal() doesn't need to
            # enumerate every log variant name.
            if item.endswith('_log') or item == 'wood':
                before_wood = self.items['wood']
                self.items['wood'] += qty
                print(f'[INV] wood: {before_wood} -> {self.items["wood"]}')

    # Approximate output count per successful craft (behaviors/crafting.py
    # CraftingBehavior.run() only confirms a recipe fired, not the exact
    # stack size produced — these mirror vanilla yields closely enough for
    # the estimate this tracker already is).
    _CRAFT_YIELDS = {
        'oak_planks': ('oak_planks', 4), 'spruce_planks': ('spruce_planks', 4),
        'birch_planks': ('birch_planks', 4), 'jungle_planks': ('jungle_planks', 4),
        'acacia_planks': ('acacia_planks', 4), 'dark_oak_planks': ('dark_oak_planks', 4),
        'stick': ('stick', 4),
        'torch': ('torch', 4), 'torch_charcoal': ('torch', 4),
    }

    def on_craft_success(self, recipe_name: str):
        """Record the output of a successful CraftingBehavior.run() (see
        core/runtime.py) — recipe_name is whatever it returned. Defaults to
        +1 of the recipe's own name (true for tools/table/furnace/chest)."""
        item, qty = self._CRAFT_YIELDS.get(recipe_name, (recipe_name, 1))
        before = self.items[item]
        self.items[item] += qty
        print(f'[INV] {item}: {before} -> {self.items[item]} (+{qty} from craft:{recipe_name})')

    def wood_count(self) -> int:
        """Total logs across every tracked wood key."""
        return (self.items.get('wood', 0) + self.items.get('oak_log', 0)
                + self.items.get('birch_log', 0))

    def wood_subgoal(self):
        """Return the next crafting sub-goal implied by current wood-chain
        stock, or None if nothing is due yet. Checked from the far end of
        the chain backward so the highest-value pending step wins (e.g. if
        we already have enough for a crafting table, that beats
        re-suggesting sticks)."""
        planks = self.items.get('planks', 0) + self.items.get('oak_planks', 0)
        sticks = self.items.get('stick', 0)
        if sticks >= 4 and planks >= 3:
            return 'craft_crafting_table'
        if planks >= 8:
            return 'craft_sticks'
        if self.wood_count() >= 8:
            return 'craft_planks'
        return None

    def should_drop_junk(self) -> bool:
        """True once total tracked item count exceeds a full inventory."""
        return sum(self.items.values()) > FULL_INVENTORY_SLOTS

    def junk_to_drop(self) -> list:
        """Junk item names (cobblestone/dirt/gravel) currently held, in the
        order they should be dropped."""
        return [item for item in JUNK_ITEMS if self.items.get(item, 0) > 0]

    def context_summary(self) -> str:
        if not self.items:
            return "Inventory: empty (nothing mined yet this session)"
        top = sorted(self.items.items(), key=lambda x: -x[1])[:8]
        parts = ", ".join(f"{k}×{v}" for k, v in top)
        return f"Inventory (estimated): {parts}"
