# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Crafting Behavior                   ║
# ║  Approach table → open UI → place recipe → collect    ║
# ╚══════════════════════════════════════════════════════╝
#
# Fires when a crafting_table is in view and the pickaxe is close to
# breaking (see core/runtime.py). The JSON `craft_pickaxe` skill
# (data/skills/craft_pickaxe.json) only walks up and opens/closes the
# table; this behavior additionally clicks the recipe into the 3x3 grid
# and pulls the result out.
#
# Clicks use the same [x_pct, y_pct] 0-100 convention as every other
# action dict in this codebase — see uart/kb2040_packer.pack_mouse_absolute.
# There's no pixel math and no relative-delta mouse hack needed; the HID
# layer already supports absolute positioning via TYPE_MOUSE_A packets.

import time

# Crafting grid slot positions as [0-100] percent of screen, approximating
# the vanilla Java crafting-table GUI centered on a 16:9 window. Adjust to
# taste if your GUI scale differs.
CRAFT_GRID = {
    (0, 0): (39.5, 34.0), (0, 1): (44.0, 34.0), (0, 2): (48.5, 34.0),
    (1, 0): (39.5, 41.0), (1, 1): (44.0, 41.0), (1, 2): (48.5, 41.0),
    (2, 0): (39.5, 48.0), (2, 1): (44.0, 48.0), (2, 2): (48.5, 48.0),
}
RESULT_SLOT = (63.0, 41.0)

# Stone pickaxe recipe: cobblestone across the top row, sticks down the
# middle column.
PICKAXE_RECIPE = {
    (0, 0): 'cobblestone', (0, 1): 'cobblestone', (0, 2): 'cobblestone',
    (1, 1): 'stick',
    (2, 1): 'stick',
}


class CraftingBehavior:
    """Approaches a crafting table and crafts a stone pickaxe.

    There's no reliable way to read hotbar contents from video, so this
    trusts a fixed recipe layout and assumes the player already has
    cobblestone + sticks in inventory.
    """

    COOLDOWN_SEC = 30.0

    def __init__(self, executor):
        self.executor = executor
        self._last_craft = 0.0

    def should_trigger(self, objects: list) -> bool:
        if time.time() - self._last_craft < self.COOLDOWN_SEC:
            return False
        return any(o.get('label') == 'crafting_table' for o in objects)

    def run(self):
        """Execute the full craft-pickaxe sequence."""
        print('[CRAFT] approaching crafting table')
        self._tap('w', 400)
        self._tap('w', 400)
        self._tap('w', 300)

        print('[CRAFT] opening table')
        self._click(50.0, 50.0, button='right', wait=0.8)

        print('[CRAFT] placing recipe')
        for slot, _item in PICKAXE_RECIPE.items():
            x_pct, y_pct = CRAFT_GRID[slot]
            self._click(x_pct, y_pct, wait=0.15)

        print('[CRAFT] collecting result')
        self._click(*RESULT_SLOT, wait=0.2)

        print('[CRAFT] closing inventory')
        self._tap('e', 150)

        self._last_craft = time.time()
        print('[CRAFT] done — pickaxe crafted (if materials were available)')

    def _tap(self, key: str, wait_ms: int):
        self.executor.execute({
            'key': key, 'click': None, 'gamepad': None, 'source': 'crafting',
        })
        time.sleep(wait_ms / 1000.0)

    def _click(self, x_pct: float, y_pct: float, button: str = 'left', wait: float = 0.15):
        action = {
            'key': None, 'click': [x_pct, y_pct],
            'gamepad': None, 'source': 'crafting',
        }
        if button == 'right':
            action['button'] = 'right'
        self.executor.execute(action)
        time.sleep(wait)
