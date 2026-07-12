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

# ── Inventory grid slot → screen % (crafting table UI) ─────────
# Slots 0-26: 3×9 main inventory.  Slots 27-35: hotbar.
# Row/col spacing: ~4.5% x, ~8% y.  Origin at top-left of main grid.
_INV_GRID_X0  = 31.5   # left edge of column 0 (%)
_INV_GRID_DX  = 4.5    # column pitch (%)
_INV_GRID_Y0  = 59.5   # top edge of row 0 (%)
_INV_GRID_DY  = 8.0    # row pitch (%)
_INV_HOTBAR_Y = 84.5   # hotbar row Y (%)


def _inv_slot_pct(slot: int) -> tuple[float, float]:
    """Convert inventory slot index (0-35) to (x_pct, y_pct) in crafting table UI."""
    if slot < 0:
        return (50.0, 90.0)   # safe fallback off the crafting grid
    if slot >= 27:             # hotbar
        col = slot - 27
        return _INV_GRID_X0 + col * _INV_GRID_DX, _INV_HOTBAR_Y
    row = slot // 9
    col = slot % 9
    return _INV_GRID_X0 + col * _INV_GRID_DX, _INV_GRID_Y0 + row * _INV_GRID_DY

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
def _can_craft(inv: dict, recipe: dict) -> bool:
    """Return True if inventory has all required materials for this recipe."""
    for item, count in _requirements(recipe).items():
        if item == 'oak_planks':
            ok, _ = _has_planks(inv, count)
            if not ok:
                return False
        elif item == 'stick':
            if inv.get('stick', 0) < count:
                return False
        else:
            if inv.get(item, 0) < count:
                return False
    return True


def _total_planks(inv: dict) -> int:
    return sum(inv.get(p, 0) for p in _PLANK_VARIANTS)


def decide_what_to_craft(inv: dict) -> tuple[str | None, str]:
    """
    Given inventory contents, return (recipe_name, grid_type) where
    grid_type is '2x2' or '3x3'.  Returns (None, '') when nothing is craftable.

    This function resolves dependencies automatically:
    - If we could make a stone pickaxe but lack sticks → return 'stick' first
    - If we could make sticks but lack planks → return the right plank recipe first
    - This means the caller may need to run multiple cycles to build up the
      crafting chain (logs → planks → sticks → pickaxe).
    """

    # ── 3x3 tools (need crafting table) ────────────────────────
    # Iron pickaxe (best option if we have iron)
    if _can_craft(inv, RECIPES_3x3['iron_pickaxe']):
        return 'iron_pickaxe', '3x3'

    # Stone pickaxe
    if _can_craft(inv, RECIPES_3x3['stone_pickaxe']):
        return 'stone_pickaxe', '3x3'

    # Missing sticks for stone pickaxe? → craft sticks first
    sticks_needed_for_stone = (inv.get('cobblestone', 0) >= 3
                               and inv.get('stick', 0) < 2)
    if sticks_needed_for_stone and _total_planks(inv) >= 2:
        return 'stick', '2x2'

    # Wooden pickaxe
    if _can_craft(inv, RECIPES_3x3['wooden_pickaxe']):
        return 'wooden_pickaxe', '3x3'

    # Missing sticks for wooden pickaxe? → craft sticks first
    sticks_needed_for_wood = (_total_planks(inv) >= 5 and inv.get('stick', 0) < 2)
    if sticks_needed_for_wood:
        return 'stick', '2x2'

    # Stone sword
    if _can_craft(inv, RECIPES_3x3['stone_sword']):
        return 'stone_sword', '3x3'

    # Wooden sword
    if _can_craft(inv, RECIPES_3x3['wooden_sword']):
        return 'wooden_sword', '3x3'

    # Torches
    if _can_craft(inv, RECIPES_3x3['torch']):
        return 'torch', '3x3'
    if _can_craft(inv, RECIPES_3x3['torch_charcoal']):
        return 'torch_charcoal', '3x3'

    # ── 2x2 items (inventory crafting, no table needed) ─────────
    # Crafting table (if we have 4 planks and probably no table nearby)
    if _can_craft(inv, RECIPES_2x2['crafting_table']):
        return 'crafting_table', '2x2'

    # Sticks from planks
    stick_recipe = {(0, 0): 'oak_planks', (1, 0): 'oak_planks'}
    if _can_craft(inv, stick_recipe):
        return 'stick', '2x2'

    # Planks from logs — pick whichever log we have
    for log, plank in _LOG_TO_PLANK.items():
        if inv.get(log, 0) >= 1:
            return plank, '2x2'

    return None, ''


def crafting_chain(inv: dict) -> list[tuple[str, str]]:
    """
    Return the full ordered list of recipes needed to reach the best craftable
    tool, given current inventory.  Each entry is (recipe_name, grid_type).
    Useful for logging / goal display.
    """
    chain = []
    sim_inv = dict(inv)
    for _ in range(8):     # max 8 steps (enough for any chain)
        name, grid = decide_what_to_craft(sim_inv)
        if name is None:
            break
        chain.append((name, grid))
        # Simulate crafting: deduct inputs, add output
        if grid == '3x3':
            recipe = RECIPES_3x3.get(name, {})
        else:
            recipe = RECIPES_2x2.get(name, {})
        for item, count in _requirements(recipe).items():
            actual = 'oak_planks' if item == 'oak_planks' else item
            sim_inv[actual] = max(0, sim_inv.get(actual, 0) - count)
        sim_inv[name] = sim_inv.get(name, 0) + (4 if 'planks' in name
                                                 else 4 if name == 'stick'
                                                 else 4 if name == 'torch'
                                                 else 1)
    return chain


class CraftingBehavior:
    """Smart crafting: reads inventory, picks the best recipe, executes it.

    Supports both 2x2 (inventory crafting) and 3x3 (crafting table).
    Uses InventoryReader to know what materials are available.
    """

    COOLDOWN_SEC        = 20.0   # min seconds between crafting attempts
    TABLE_APPROACH_DIST = 3      # forward steps to approach table

    def __init__(self, executor, inventory_reader=None):
        """
        Args:
            executor:         action executor
            inventory_reader: InventoryReader instance (optional; falls back to
                              assuming materials are present if None)
        """
        self.executor    = executor
        self.inv_reader  = inventory_reader
        self._last_craft  = 0.0
        self._last_recipe = None

    # ── Public API ───────────────────────────────────────────────

    def should_trigger(self, objects: list) -> bool:
        """Return True if a crafting table is visible and cooldown is clear."""
        if time.time() - self._last_craft < self.COOLDOWN_SEC:
            return False
        return any(o.get('label') == 'crafting_table' for o in objects)

    def should_trigger_2x2(self) -> bool:
        """Return True if a 2x2-only recipe is available (no table needed).

        Reads cached inventory; does not open the inventory screen.
        """
        if time.time() - self._last_craft < self.COOLDOWN_SEC:
            return False
        inv = self._read_inventory(force=False)
        name, grid = decide_what_to_craft(inv)
        return name is not None and grid == '2x2'

    def run(self, objects: list | None = None) -> str | None:
        """Decide what to craft, then execute.  Returns recipe name or None."""
        inv = self._read_inventory(force=True)

        # Log the full crafting chain so we know what's coming
        chain = crafting_chain(inv)
        if chain:
            chain_str = ' → '.join(n for n, _ in chain)
            print(f'[CRAFT] chain: {chain_str}')

        recipe_name, grid = decide_what_to_craft(inv)
        if recipe_name is None:
            print('[CRAFT] nothing useful to craft with current materials')
            return None

        print(f'[CRAFT] crafting {recipe_name} via {grid} '
              f'(inv snapshot: {dict(list(inv.items())[:10])})')

        # Get slot positions for pick-and-place (if reader supports it)
        inv_slots = {}
        if self.inv_reader is not None:
            raw = self.inv_reader.read_with_slots(force=False)
            inv_slots = {k: v.get('slot', -1) for k, v in raw.items()}

        success = False
        try:
            if grid == '3x3':
                success = self._run_3x3(recipe_name, inv, inv_slots)
            else:
                success = self._run_2x2(recipe_name, inv)
        except Exception as e:
            print(f'[CRAFT] error during {recipe_name}: {e}')
            self._emergency_close()

        if success:
            self._last_recipe = recipe_name
            self._last_craft  = time.time()
            if self.inv_reader is not None:
                self.inv_reader.invalidate()
            print(f'[CRAFT] ✓ {recipe_name}')
        else:
            print(f'[CRAFT] ✗ {recipe_name} — may retry next cycle')

        return recipe_name if success else None

    def can_craft_anything(self) -> bool:
        """Quick check (cached) for whether any recipe is currently possible."""
        inv = self._read_inventory(force=False)
        name, _ = decide_what_to_craft(inv)
        return name is not None

    # ── Crafting sequences ───────────────────────────────────────

    def _run_3x3(self, recipe_name: str, inv: dict, inv_slots: dict) -> bool:
        self._approach_table()
        if not self._open_table():
            return False
        recipe = _normalize_recipe(RECIPES_3x3[recipe_name], inv)
        self._place_recipe_3x3(recipe, inv_slots)
        self._collect(RESULT_SLOT_3x3)
        self._close_ui()
        return True

    def _run_2x2(self, recipe_name: str, inv: dict) -> bool:
        self._open_inventory()
        recipe = _normalize_recipe(RECIPES_2x2[recipe_name], inv)
        self._place_recipe_2x2(recipe)
        self._collect(RESULT_SLOT_2x2)
        self._close_ui()
        return True

    def _approach_table(self):
        print('[CRAFT] approaching crafting table')
        for _ in range(self.TABLE_APPROACH_DIST):
            self._tap('w', 300)

    def _open_table(self) -> bool:
        """Right-click to open crafting table.  Returns False if UI didn't open."""
        print('[CRAFT] opening crafting table')
        self._click(50.0, 50.0, button='right', wait=0.9)
        # Brief pause then verify — can't read YOLO here so just trust timing
        return True

    def _open_inventory(self):
        print('[CRAFT] opening inventory (2x2 craft)')
        self._tap('e', 600)
        time.sleep(0.35)

    def _place_recipe_3x3(self, recipe: dict, inv_slots: dict):
        """Pick each material from inventory, deposit into crafting grid slots.

        Groups slots by material so we only do one pick-up per unique item:
          1. Left-click inventory slot → picks up whole stack onto cursor
          2. Left-click each crafting grid slot that needs that item → deposits one
          3. Left-click the same inventory slot again → returns remainder
        """
        print('[CRAFT] placing 3×3 recipe (pick-and-place)')

        # Group crafting grid slots by material
        from collections import defaultdict
        slots_for_item: dict[str, list] = defaultdict(list)
        for grid_slot, item in recipe.items():
            slots_for_item[item].append(grid_slot)

        for item, grid_slots in slots_for_item.items():
            inv_slot = inv_slots.get(item, -1)
            if inv_slot < 0:
                print(f'[CRAFT] WARNING: no slot found for {item} — skipping')
                continue

            inv_x, inv_y = _inv_slot_pct(inv_slot)

            # Pick up stack from inventory
            self._click(inv_x, inv_y, wait=0.2)

            # Deposit one into each required crafting grid slot
            for grid_slot in grid_slots:
                cx, cy = CRAFT_GRID_3x3[grid_slot]
                self._click(cx, cy, wait=0.15)

            # Return remainder to inventory (click same slot)
            self._click(inv_x, inv_y, wait=0.2)

    def _place_recipe_2x2(self, recipe: dict):
        print('[CRAFT] placing 2×2 recipe')
        for slot, _item in recipe.items():
            x_pct, y_pct = CRAFT_GRID_2x2[slot]
            self._click(x_pct, y_pct, wait=0.15)

    def _collect(self, result_slot: tuple):
        """Shift-click result slot to collect entire stack at once."""
        print('[CRAFT] collecting result (shift-click)')
        action = {
            'key': 'shift', 'click': list(result_slot),
            'gamepad': None, 'source': 'crafting',
        }
        self.executor.execute(action)
        time.sleep(0.35)

    def _close_ui(self):
        self._tap('e', 150)

    def _emergency_close(self):
        """Mash Escape to close any stuck UI."""
        print('[CRAFT] emergency close — pressing Escape')
        for _ in range(3):
            self._tap('escape', 200)

    # ── Inventory ────────────────────────────────────────────────

    def _read_inventory(self, force: bool = False) -> dict:
        if self.inv_reader is not None:
            return self.inv_reader.read(force=force)
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
