# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Hotbar Tracker                            ║
# ║  Tracks the 9 hotbar slots via key-press observation  ║
# ║  and skill-to-item association. Vision-based reading  ║
# ║  of the UI is not used — Qwen is in GUI detection     ║
# ║  mode and cannot parse Minecraft inventory screens.   ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time

_HOTBAR_PATH = os.path.join('data', 'cognitive', 'hotbar.json')

# Skills imply specific tools in hand. When a skill fires, we know
# what the bot must be holding to execute it.
_SKILL_IMPLIES_ITEM = {
    'mine_diamond_ore':  'iron_pickaxe',
    'mine_emerald_ore':  'iron_pickaxe',
    'mine_redstone_ore': 'iron_pickaxe',
    'mine_copper_ore':   'stone_pickaxe',
    'mine_coal_ore':     'wooden_pickaxe',
    'mine_iron_ore':     'stone_pickaxe',
    'mine_gold_ore':     'iron_pickaxe',
    'mine_lapis_ore':    'stone_pickaxe',
    'chop_tree':         'wooden_axe',
    'chop_birch_tree':   'wooden_axe',
    'animal_mob':        'wooden_sword',
    'dig_up':            'iron_pickaxe',
    'mine_up':           'iron_pickaxe',
}


class HotbarReader:
    """Tracks hotbar state via key-press observation and skill inference.

    Since Qwen3.5-4B-Vision enters GUI detection mode for any UI screenshot
    and cannot be coerced into inventory parsing, this class infers hotbar
    contents from:
      - Explicit slot-select key presses (1-9) observed by on_key()
      - Skills that fired → implies tool in active slot (on_skill())
      - Manual slot assignment via assign_slot()
    """

    def __init__(self):
        self._slots: dict[int, dict] = {}   # {slot(1-9): {item, count}}
        self._active: int = 1
        self._load()

    # ── Observation hooks (call from executor / skill system) ─────

    def on_key(self, key: str):
        """Call whenever the executor sends a key press. Slot-select keys
        (1-9) update the active slot."""
        if key in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
            self._active = int(key)

    def on_skill(self, skill_name: str):
        """Call when a skill fires. Associates the implied tool with the
        currently active slot."""
        item = _SKILL_IMPLIES_ITEM.get(skill_name)
        if item and self._active not in self._slots:
            self._slots[self._active] = {'item': item, 'count': 1}
            print(f'[HOTBAR] inferred slot {self._active} = {item} (from skill {skill_name})')
            self.save()

    def assign_slot(self, slot: int, item: str, count: int = 1):
        """Explicitly assign an item to a hotbar slot (e.g. after crafting
        places a new tool in hand)."""
        self._slots[slot] = {'item': item, 'count': count}
        print(f'[HOTBAR] assigned slot {slot} = {item}×{count}')
        self.save()

    def on_item_used(self, slot: int, count_delta: int = -1):
        """Decrement count in a slot (e.g. torch placed, food eaten)."""
        if slot in self._slots:
            self._slots[slot]['count'] = max(0, self._slots[slot]['count'] + count_delta)
            if self._slots[slot]['count'] == 0:
                del self._slots[slot]
            self.save()

    # ── Public API ────────────────────────────────────────────────

    @property
    def active_slot(self) -> int:
        return self._active

    def held_item(self) -> str | None:
        return self._slots.get(self._active, {}).get('item')

    def item_in_slot(self, slot: int) -> str | None:
        return self._slots.get(slot, {}).get('item')

    def all_items(self) -> dict:
        """Return {item_name: total_count} across all hotbar slots."""
        totals: dict = {}
        for d in self._slots.values():
            name = d.get('item')
            cnt  = d.get('count', 1)
            if name:
                totals[name] = totals.get(name, 0) + cnt
        return totals

    def read(self, force: bool = False) -> dict:
        """Return {slot_str: {item, count}} — kept for API compatibility."""
        return {str(k): v for k, v in self._slots.items()}

    def context_summary(self) -> str:
        if not self._slots:
            return 'Hotbar: unknown (no skills fired yet)'
        parts = []
        for s in range(1, 10):
            d = self._slots.get(s)
            if d:
                mark = '*' if s == self._active else ''
                parts.append(f'{mark}[{s}]{d["item"]}×{d["count"]}')
        held = self.held_item()
        return 'Hotbar: ' + ' '.join(parts) + (f' (holding {held})' if held else '')

    # ── Persistence ───────────────────────────────────────────────

    def save(self):
        os.makedirs(os.path.dirname(_HOTBAR_PATH), exist_ok=True)
        with open(_HOTBAR_PATH, 'w') as f:
            json.dump({'active': self._active,
                       'slots': {str(k): v for k, v in self._slots.items()}}, f)

    def _load(self):
        if not os.path.exists(_HOTBAR_PATH):
            return
        try:
            with open(_HOTBAR_PATH) as f:
                data = json.load(f)
            self._active = int(data.get('active', 1))
            self._slots  = {int(k): v for k, v in data.get('slots', {}).items()}
        except Exception:
            pass
