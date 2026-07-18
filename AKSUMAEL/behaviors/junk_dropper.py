# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Junk Dropper                              ║
# ║  Frees hotbar slots by dropping low-value blocks      ║
# ║  (cobblestone/dirt/gravel) once inventory looks full. ║
# ╚══════════════════════════════════════════════════════╝

import time

from memory.inventory import JUNK_ITEMS


class JunkDropper:
    """Drops junk items (cobblestone/dirt/gravel) from the hotbar when the
    tracked inventory looks full.

    Requires a slot-aware InventoryReader (behaviors/inventory_reader.py) to
    know which hotbar slot actually holds the junk item — without real slot
    data there's no safe way to pick a hotbar key without risking dropping
    something useful, so this is a no-op whenever the reader is
    disabled/unavailable or hasn't found the item in the hotbar.
    """

    COOLDOWN_SEC = 20.0

    def __init__(self, executor, inventory_reader=None):
        self.executor    = executor
        self.inv_reader  = inventory_reader
        self._last_drop  = 0.0

    def maybe_run(self, inventory) -> bool:
        """Call once per tick (cheap no-op when nothing to do). Returns True
        if a drop was attempted this call."""
        if not inventory.should_drop_junk():
            return False
        if self.inv_reader is None:
            return False
        if time.time() - self._last_drop < self.COOLDOWN_SEC:
            return False

        slots = self.inv_reader.read_with_slots(force=False)
        dropped = False
        for item in inventory.junk_to_drop():
            info = slots.get(item)
            slot = info.get('slot', -1) if info else -1
            if slot < 27:   # only hotbar slots (27-35) are safe to select by number key
                continue
            hotbar_key = str(slot - 27 + 1)   # slot 27-35 -> key '1'-'9'
            print(f'[INV] inventory full — dropping junk: {item} (hotbar slot {hotbar_key})')
            self.executor.execute({'key': hotbar_key, 'click': None, 'gamepad': None, 'source': 'inventory'})
            time.sleep(0.15)
            self.executor.execute({'key': 'q', 'click': None, 'gamepad': None, 'source': 'inventory'})
            time.sleep(0.15)
            dropped = True

        self._last_drop = time.time()
        return dropped
