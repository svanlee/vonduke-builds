# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inventory Reader                   ║
# ║  Opens inventory, asks the local LLM to read it,      ║
# ║  returns a structured {item: count} dict for crafting.║
# ╚══════════════════════════════════════════════════════╝

import json
import re
import time

from core.llm_router import route_llm_call, frame_to_b64


# Cache TTL — don't re-open inventory more often than this
_CACHE_TTL_SEC = 15.0

_CODE_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$', re.IGNORECASE | re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    """Strip ```json ... ``` / ``` ... ``` markdown fences some LLM tiers
    wrap their JSON reply in, despite the prompt asking for raw JSON."""
    return _CODE_FENCE_RE.sub('', text).strip()


def _parse_json_response(raw) -> dict | list | None:
    """Parse an LLM JSON reply, tolerating markdown code fences and
    empty/None responses. Returns None (never raises) on any failure."""
    if not raw or not raw.strip():
        return None
    cleaned = _strip_code_fences(raw)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


_INVENTORY_PROMPT = """This is a screenshot from Minecraft Java Edition.

TASK: Read the player's inventory and return item slots as JSON.

The inventory screen (press E) has:
- A 3×9 MAIN GRID (27 slots numbered 0-26, row-major, top-left = 0)
- A HOTBAR row (9 slots numbered 27-35, left = 27)
- A small 2×2 crafting area + result in the top-right (ignore these for slot numbering)
- Armour slots on the left (ignore)

INSTRUCTIONS:
1. First check: is an inventory/crafting screen actually open?
   If you see only the game world with no UI panel, return: {"inventory_closed": true}
2. Number the slots left-to-right, top-to-bottom:
   Row 0: slots 0-8  (top row of main grid)
   Row 1: slots 9-17 (middle row)
   Row 2: slots 18-26 (bottom row)
   Hotbar: slots 27-35 (bottom strip)
3. For each non-empty slot, record:
   - "count": stack size (integer shown in slot corner; 1 if no number visible)
   - "slot": slot index (0-35)
4. If the same item appears in multiple slots, sum the counts but report the
   FIRST slot index (lowest number) where it appears.
5. Use Minecraft snake_case IDs. Common ones:
   oak_log, spruce_log, birch_log, dark_oak_log, jungle_log, acacia_log,
   oak_planks, spruce_planks, birch_planks, cobblestone, stone, dirt, gravel,
   stick, coal, charcoal, iron_ore, iron_ingot, gold_ore, gold_ingot,
   diamond, diamond_ore, redstone, lapis_lazuli, emerald,
   wooden_pickaxe, stone_pickaxe, iron_pickaxe, wooden_axe, stone_axe,
   wooden_sword, stone_sword, bread, apple, raw_beef, cooked_beef,
   torch, crafting_table, furnace, chest, bow, arrow, shield

Respond with ONLY a valid JSON object — no markdown fences, no commentary:
{"cobblestone": {"count": 23, "slot": 5}, "stick": {"count": 8, "slot": 14}, "oak_log": {"count": 4, "slot": 0}}"""


class InventoryReader:
    """Open the inventory, ask Claude to parse it, return {item: count}."""

    def __init__(self, executor, capture_fn):
        """
        Args:
            executor:   action executor (same interface as CraftingBehavior uses)
            capture_fn: callable() → OpenCV BGR frame of the current screen
        """
        self.executor   = executor
        self.capture    = capture_fn
        self._cache     = {}          # last read result
        self._cache_ts  = 0.0        # when it was read
        self._reading   = False       # re-entrancy guard

    # ── Public API ───────────────────────────────────────────────

    def read(self, force: bool = False) -> dict:
        """Return {item: count} — simple form for crafting decision logic."""
        raw = self._read_raw(force=force)
        return {k: v['count'] for k, v in raw.items()
                if isinstance(v, dict) and 'count' in v}

    def read_with_slots(self, force: bool = False) -> dict:
        """Return {item: {'count': N, 'slot': S}} — full form for pick-and-place."""
        raw = self._read_raw(force=force)
        return {k: v for k, v in raw.items()
                if isinstance(v, dict) and 'slot' in v}

    def slot_of(self, item: str) -> int:
        """Return inventory slot index for item, or -1 if not found."""
        return self._cache.get(item, {}).get('slot', -1)

    def has(self, item: str, min_count: int = 1) -> bool:
        """Check if inventory (cached) has at least min_count of item."""
        return self._cache.get(item, {}).get('count', 0) >= min_count

    def invalidate(self):
        """Force next read() to re-query."""
        self._cache_ts = 0.0

    def _read_raw(self, force: bool = False) -> dict:
        """Return raw {item: {count, slot}} dict, refreshing cache if needed."""
        now = time.time()
        if not force and now - self._cache_ts < _CACHE_TTL_SEC:
            return dict(self._cache)
        if self._reading:
            return dict(self._cache)

        self._reading = True
        try:
            result = self._do_read()
        finally:
            self._reading = False

        self._cache    = result
        self._cache_ts = time.time()
        return dict(result)

    # ── Internals ────────────────────────────────────────────────

    def _do_read(self) -> dict:
        print('[INV] opening inventory')
        self._tap('e', 700)          # open inventory — longer wait for slow frames

        # Give the UI a moment to render fully. Was 0.5s but logs showed
        # ~50% of reads coming back "inventory was not open" — the UI
        # fade-in sometimes isn't done yet at that point.
        time.sleep(0.9)

        frame = self.capture()
        if frame is None:
            # State unknown — don't blind-fire Escape (it opens the pause
            # menu if 'e' never actually opened the inventory). 'e' is a
            # safe no-op-or-toggle either way.
            print('[INV] no frame — closing with e (state unknown)')
            self._tap('e', 200)
            return {}

        items, was_open = self._ask_llm(frame)
        print(f'[INV] read: {items}')

        if was_open:
            # Press Escape to close (safer than E which could toggle a different menu)
            self._tap('escape', 300)
        else:
            print('[INV] inventory was not open — skipping close key')
        return items

    def _ask_llm(self, frame) -> tuple[dict, bool]:
        """Returns (items, was_open) — was_open is False when the inventory
        was confirmed closed (or the read failed), so callers know not to
        press a close key."""
        # Generous budget — the model 'thinks' before answering, which can
        # burn several hundred tokens before the actual JSON reply.
        raw, _provider = route_llm_call(
            _INVENTORY_PROMPT, max_tokens=1200, images=[frame_to_b64(frame)],
            timeout=45, local_retries=3)
        if raw is None:
            print('[INV] all LLM tiers failed')
            return {'items': [], 'parse_error': True}, False

        items = _parse_json_response(raw)
        if items is None:
            print(f'[INV] error parsing response: not valid JSON — raw={raw[:200]!r}')
            return {'items': [], 'parse_error': True}, False
        if not isinstance(items, dict):
            print(f'[INV] response parsed but was not a JSON object ({type(items).__name__})')
            return {'items': [], 'parse_error': True}, False

        # Inventory wasn't open — return empty rather than crash
        if items.get('inventory_closed'):
            print('[INV] LLM says inventory was not open')
            return {}, False
        # Support both old {item: count} and new {item: {count, slot}} formats
        result = {}
        for k, v in items.items():
            if not isinstance(k, str) or k == 'inventory_closed':
                continue
            key = k.lower().replace(' ', '_')
            if isinstance(v, dict):
                count = max(0, int(v.get('count', 1)))
                slot  = int(v.get('slot', -1))
            elif isinstance(v, (int, float)):
                count = max(0, int(v))
                slot  = -1
            else:
                continue
            result[key] = {'count': count, 'slot': slot}
        return result, True

    def _tap(self, key: str, wait_ms: int):
        self.executor.execute({
            'key': key, 'click': None, 'gamepad': None, 'source': 'inventory',
        })
        time.sleep(wait_ms / 1000.0)
