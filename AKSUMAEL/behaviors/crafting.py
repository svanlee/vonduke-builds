# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Crafting Behavior                   ║
# ║  Smart crafting: reads inventory, picks recipe,       ║
# ║  handles 2x2 (inventory) and 3x3 (crafting table)     ║
# ╚══════════════════════════════════════════════════════╝

import time

# ── 3x3 crafting-table grid (x_pct, y_pct) ────────────────────
# Vanilla Java Edition crafting-table GUI centred on 1920×1080, GUI scale 2.
# Each cell is ~4.5% wide × ~7% tall.  Calibrate if your setup differs.
CRAFT_GRID_3x3 = {
    (0, 0): (39.5, 34.0), (0, 1): (44.0, 34.0), (0, 2): (48.5, 34.0),
    (1, 0): (39.5, 41.0), (1, 1): (44.0, 41.0), (1, 2): (48.5, 41.0),
    (2, 0): (39.5, 48.0), (2, 1): (44.0, 48.0), (2, 2): (48.5, 48.0),
}
RESULT_SLOT_3x3 = (63.0, 41.0)

# ── 2x2 inventory crafting grid (x_pct, y_pct) ────────────────
# These appear in the top-right of the inventory screen (press E).
# Vanilla 1920×1080, GUI scale 2.  Calibrate if offsets look wrong.
CRAFT_GRID_2x2 = {
    (0, 0): (51.0, 38.0), (0, 1): (52.9, 38.0),
    (1, 0): (51.0, 41.3), (1, 1): (52.9, 41.3),
}
RESULT_SLOT_2x2 = (56.9, 39.8)

# ── Recipes ───────────────────────────────────────────────────
# Keys are (row, col); values are item IDs matching inventory_reader output.
# 2x2 recipes — crafted directly from inventory (no crafting table needed)
RECIPES_2x2 = {
    # Planks from any log variant (one log yields 4 planks)
    'oak_planks': {
        (0, 0): 'oak_log',
    },
    'spruce_planks': {
        (0, 0): 'spruce_log',
    },
    'birch_planks': {
        (0, 0): 'birch_log',
    },
    'jungle_planks': {
        (0, 0): 'jungle_log',
    },
    'acacia_planks': {
        (0, 0): 'acacia_log',
    },
    'dark_oak_planks': {
        (0, 0): 'dark_oak_log',
    },
    # Sticks from planks (any variant, placed in 2×1)
    'stick': {
        (0, 0): 'oak_planks',
        (1, 0): 'oak_planks',
    },
    # Crafting table from 4 planks (2×2 square)
    'crafting_table': {
        (0, 0): 'oak_planks', (0, 1): 'oak_planks',
        (1, 0): 'oak_planks', (1, 1): 'oak_planks',
    },
}

# 3x3 recipes — require a crafting table
RECIPES_3x3 = {
    # Wooden pickaxe: 3 planks across top, 2 sticks down middle
    'wooden_pickaxe': {
        (0, 0): 'oak_planks', (0, 1): 'oak_planks', (0, 2): 'oak_planks',
        (1, 1): 'stick',
        (2, 1): 'stick',
    },
    # Stone pickaxe: 3 cobblestone across top, 2 sticks down middle
    'stone_pickaxe': {
        (0, 0): 'cobblestone', (0, 1): 'cobblestone', (0, 2): 'cobblestone',
        (1, 1): 'stick',
        (2, 1): 'stick',
    },
    # Iron pickaxe
    'iron_pickaxe': {
        (0, 0): 'iron_ingot', (0, 1): 'iron_ingot', (0, 2): 'iron_ingot',
        (1, 1): 'stick',
        (2, 1): 'stick',
    },
    # Wooden axe
    'wooden_axe': {
        (0, 0): 'oak_planks', (0, 1): 'oak_planks',
        (1, 0): 'oak_planks', (1, 1): 'stick',
        (2, 1): 'stick',
    },
    # Stone axe
    'stone_axe': {
        (0, 0): 'cobblestone', (0, 1): 'cobblestone',
        (1, 0): 'cobblestone', (1, 1): 'stick',
        (2, 1): 'stick',
    },
    # Wooden sword
    'wooden_sword': {
        (0, 1): 'oak_planks',
        (1, 1): 'oak_planks',
        (2, 1): 'stick',
    },
    # Stone sword
    'stone_sword': {
        (0, 1): 'cobblestone',
        (1, 1): 'cobblestone',
        (2, 1): 'stick',
    },
    # Furnace
    'furnace': {
        (0, 0): 'cobblestone', (0, 1): 'cobblestone', (0, 2): 'cobblestone',
        (1, 0): 'cobblestone',                         (1, 2): 'cobblestone',
        (2, 0): 'cobblestone', (2, 1): 'cobblestone', (2, 2): 'cobblestone',
    },
    # Chest
    'chest': {
        (0, 0): 'oak_planks', (0, 1): 'oak_planks', (0, 2): 'oak_planks',
        (1, 0): 'oak_planks',                        (1, 2): 'oak_planks',
        (2, 0): 'oak_planks', (2, 1): 'oak_planks', (2, 2): 'oak_planks',
    },
    # Torch (4 at a time)
    'torch': {
        (0, 1): 'coal',
        (1, 1): 'stick',
    },
    # Torch from charcoal
    'torch_charcoal': {
        (0, 1): 'charcoal',
        (1, 1): 'stick',
    },
}

# ── Material requirements (counts) ────────────────────────────
# How many of each item does each recipe consume?  Used by the
# inventory check before deciding what to craft.
def _requirements(recipe: dict) -> dict[str, int]:
    """Count how many of each material a recipe needs."""
    counts: dict[str, int] = {}
    for item in recipe.values():
        counts[item] = counts.get(item, 0) + 1
    return counts


# ── Plank synonym map ──────────────────────────────────────────
# Any planks variant can substitute for oak_planks in recipes.
_PLANK_VARIANTS = {
    'oak_planks', 'spruce_planks', 'birch_planks',
    'jungle_planks', 'acacia_planks', 'dark_oak_planks',
    'mangrove_planks', 'bamboo_planks', 'cherry_planks',
}
_LOG_TO_PLANK = {
    'oak_log':      'oak_planks',
    'spruce_log':   'spruce_planks',
    'birch_log':    'birch_planks',
    'jungle_log':   'jungle_planks',
    'acacia_log':   'acacia_planks',
    'dark_oak_log': 'dark_oak_planks',
    'mangrove_log': 'mangrove_planks',
    'bamboo_block': 'bamboo_planks',
    'cherry_log':   'cherry_planks',
}


def _has_planks(inv: dict, min_count: int = 1) -> tuple[bool, str | None]:
    """Return (True, plank_id) if inventory has enough of any plank type."""
    for plank in _PLANK_VARIANTS:
        if inv.get(plank, 0) >= min_count:
            return True, plank
    return False, None


def _normalize_recipe(recipe: dict, inv: dict) -> dict:
    """Substitute oak_planks in a recipe with whatever plank type we have."""
    ok, plank = _has_planks(inv)
    if not ok or plank == 'oak_planks':
        return recipe
    return {slot: (plank if item == 'oak_planks' else item)
            for slot, item in recipe.items()}


# ── Decision tree: what should AKSUMAEL craft next? ───────────
def decide_what_to_craft(inv: dict) -> tuple[str | None, str]:
    """
    Given inventory contents, return (recipe_name, grid_type) where
    grid_type is '2x2' or '3x3'.  Returns (None, '') when there's nothing
    useful to craft or materials are insufficient.

    Priority order (lowest → highest tool tier as quickly as possible):
    1. stone pickaxe   (best current option if we have cobblestone + sticks)
    2. wooden pickaxe  (if planks + sticks, no cobblestone)
    3. sticks          (if planks available)
    4. planks          (if logs available)
    5. crafting table  (if only planks, no table visible — handled by FSM)
    """
    # Helper: does inventory have all required materials?
    def can_craft(recipe: dict) -> bool:
        for item, count in _requirements(recipe).items():
            # plank substitution
            if item == 'oak_planks':
                ok, _ = _has_planks(inv, count)
                if not ok:
                    return False
            else:
                if inv.get(item, 0) < count:
                    return False
        return True

    # Stone pickaxe (best mid-game tool)
    if can_craft(RECIPES_3x3['stone_pickaxe']):
        return 'stone_pickaxe', '3x3'

    # Iron pickaxe
    if can_craft(RECIPES_3x3['iron_pickaxe']):
        return 'iron_pickaxe', '3x3'

    # Wooden pickaxe
    if can_craft(RECIPES_3x3['wooden_pickaxe']):
        return 'wooden_pickaxe', '3x3'

    # Stone sword
    if can_craft(RECIPES_3x3['stone_sword']):
        return 'stone_sword', '3x3'

    # Wooden sword
    if can_craft(RECIPES_3x3['wooden_sword']):
        return 'wooden_sword', '3x3'

    # Torches
    if can_craft(RECIPES_3x3['torch']):
        return 'torch', '3x3'
    if can_craft(RECIPES_3x3['torch_charcoal']):
        return 'torch_charcoal', '3x3'

    # Crafting table
    if can_craft(RECIPES_2x2['crafting_table']):
        return 'crafting_table', '2x2'

    # Sticks — detect any plank variant
    stick_recipe = {(0, 0): 'oak_planks', (1, 0): 'oak_planks'}
    if can_craft(stick_recipe):
        return 'stick', '2x2'

    # Planks from logs — pick whichever log we have
    for log, plank in _LOG_TO_PLANK.items():
        if inv.get(log, 0) >= 1:
            return plank, '2x2'

    return None, ''


class CraftingBehavior:
    """Smart crafting: reads inventory, picks the best recipe, executes it.

    Supports both 2x2 (inventory crafting) and 3x3 (crafting table).
    Uses InventoryReader to know what materials are available.
    """

    COOLDOWN_SEC        = 20.0   # min seconds between crafting attempts
    TABLE_APPROACH_DIST = 3      # number of forward steps to approach table

    def __init__(self, executor, inventory_reader=None):
        """
        Args:
            executor:         action executor
            inventory_reader: InventoryReader instance (optional; falls back to
                              assuming materials are present if None)
        """
        self.executor  = executor
        self.inv_reader = inventory_reader
        self._last_craft   = 0.0
        self._last_recipe  = None

    # ── Public API ───────────────────────────────────────────────

    def should_trigger(self, objects: list) -> bool:
        """Return True if conditions are right to craft something."""
        if time.time() - self._last_craft < self.COOLDOWN_SEC:
            return False
        # Needs crafting table in view for 3x3 recipes
        table_visible = any(o.get('label') == 'crafting_table' for o in objects)
        if not table_visible:
            return False
        return True

    def run(self, objects: list | None = None) -> str | None:
        """Decide what to craft, then execute.  Returns recipe name or None."""
        inv = self._read_inventory()

        recipe_name, grid = decide_what_to_craft(inv)
        if recipe_name is None:
            print('[CRAFT] nothing useful to craft with current materials')
            return None

        print(f'[CRAFT] decided: {recipe_name} via {grid} grid '
              f'(inv={dict(list(inv.items())[:8])}...)')

        if grid == '3x3':
            self._approach_table()
            self._open_table()
            recipe = RECIPES_3x3[recipe_name]
            recipe = _normalize_recipe(recipe, inv)
            self._place_recipe_3x3(recipe)
            self._collect(RESULT_SLOT_3x3)
            self._close_ui()
        else:
            self._open_inventory()
            recipe = RECIPES_2x2[recipe_name]
            recipe = _normalize_recipe(recipe, inv)
            self._place_recipe_2x2(recipe)
            self._collect(RESULT_SLOT_2x2)
            self._close_ui()

        self._last_recipe = recipe_name
        self._last_craft  = time.time()

        # Invalidate inventory cache so next read reflects new items
        if self.inv_reader is not None:
            self.inv_reader.invalidate()

        print(f'[CRAFT] done — {recipe_name} crafted')
        return recipe_name

    def can_craft_anything(self) -> bool:
        """Quick check (uses cache) for whether any recipe is possible."""
        inv = self._read_inventory()
        name, _ = decide_what_to_craft(inv)
        return name is not None

    # ── Crafting sequences ───────────────────────────────────────

    def _approach_table(self):
        print('[CRAFT] approaching crafting table')
        for _ in range(self.TABLE_APPROACH_DIST):
            self._tap('w', 300)

    def _open_table(self):
        print('[CRAFT] opening crafting table')
        self._click(50.0, 50.0, button='right', wait=0.8)

    def _open_inventory(self):
        print('[CRAFT] opening inventory for 2x2 craft')
        self._tap('e', 600)
        time.sleep(0.3)   # let UI render

    def _place_recipe_3x3(self, recipe: dict):
        print('[CRAFT] placing recipe (3×3)')
        for slot, _item in recipe.items():
            x_pct, y_pct = CRAFT_GRID_3x3[slot]
            self._click(x_pct, y_pct, wait=0.15)

    def _place_recipe_2x2(self, recipe: dict):
        print('[CRAFT] placing recipe (2×2)')
        for slot, _item in recipe.items():
            x_pct, y_pct = CRAFT_GRID_2x2[slot]
            self._click(x_pct, y_pct, wait=0.15)

    def _collect(self, result_slot: tuple):
        print('[CRAFT] collecting result')
        self._click(*result_slot, wait=0.3)

    def _close_ui(self):
        print('[CRAFT] closing UI')
        self._tap('e', 200)

    # ── Inventory ────────────────────────────────────────────────

    def _read_inventory(self) -> dict:
        if self.inv_reader is not None:
            return self.inv_reader.read()
        # Fallback: assume we have everything needed for stone pickaxe
        # (old behaviour when no inventory reader is wired up)
        print('[CRAFT] no inventory reader — assuming stone pickaxe materials')
        return {'cobblestone': 99, 'stick': 99}

    # ── Input helpers ────────────────────────────────────────────

    def _tap(self, key: str, wait_ms: int):
        self.executor.execute({
            'key': key, 'click': None, 'gamepad': None, 'source': 'crafting',
        })
        time.sleep(wait_ms / 1000.0)

    def _click(self, x_pct: float, y_pct: float,
               button: str = 'left', wait: float = 0.15):
        action = {
            'key': None, 'click': [x_pct, y_pct],
            'gamepad': None, 'source': 'crafting',
        }
        if button == 'right':
            action['button'] = 'right'
        self.executor.execute(action)
        time.sleep(wait)
