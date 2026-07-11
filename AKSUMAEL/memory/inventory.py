"""Simple inventory tracker — AKSUMAEL can't read the inventory screen directly,
so we track what it mines/picks up and inject a summary into Claude's context."""

from collections import defaultdict
import json, os

INVENTORY_PATH = "data/inventory.json"

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
        }
        if skill_name in gains:
            item, qty = gains[skill_name]
            self.items[item] += qty

    def context_summary(self) -> str:
        if not self.items:
            return "Inventory: empty (nothing mined yet this session)"
        top = sorted(self.items.items(), key=lambda x: -x[1])[:8]
        parts = ", ".join(f"{k}×{v}" for k, v in top)
        return f"Inventory (estimated): {parts}"
