# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inventory Reader                   ║
# ║  Opens inventory, asks the local LLM to read it,      ║
# ║  returns a structured {item: count} dict for crafting.║
# ╚══════════════════════════════════════════════════════╝

import json
import re
import time

import config
from core.llm_router import route_llm_call, call_anthropic, frame_to_b64


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


_INVENTORY_PROMPT = """This is a Minecraft Java Edition screenshot showing the inventory screen.

First check: do you see a dark inventory panel with item slots? If not (just game world visible), return exactly: {"inventory_closed": true}

If inventory IS open, list every non-empty slot as a JSON object. Use this format:
{"item_name": {"count": N, "slot": S}, ...}

Rules:
- item_name: Minecraft snake_case (oak_log, iron_pickaxe, cobblestone, torch, etc.)
- count: the number shown in the corner of the slot (integer; use 1 if no number)
- slot: position 0-35 (main grid 0-26 top-left, hotbar 27-35)
- If the same item is in multiple slots, sum counts and use the lowest slot number
- Ignore armour slots and the 2x2 crafting grid

Return ONLY valid JSON, no markdown, no explanation.
Example: {"cobblestone": {"count": 23, "slot": 5}, "oak_log": {"count": 4, "slot": 0}, "iron_pickaxe": {"count": 1, "slot": 27}}"""


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

    def read_open_screen(self) -> dict:
        """Read the inventory that is *already open* on screen.

        Unlike read(force=True), this does NOT press E — it just captures
        the current frame and asks the LLM. Used by crafting code that has
        already opened the inventory and needs slot positions. Bypasses the
        INVENTORY_READER_ENABLED gate since the menu is already visible.
        Returns {item: {'count': N, 'slot': S}}.
        """
        if self._reading:
            return dict(self._cache)
        self._reading = True
        try:
            frame = self.capture()
            if frame is None:
                return {}
            items, was_open = self._ask_llm(frame)
            print(f'[INV] open-screen read: {items}')
            if not items.get('parse_error') and was_open:
                self._cache = items
                self._cache_ts = time.time()
            return dict(self._cache)
        finally:
            self._reading = False

    def _read_raw(self, force: bool = False) -> dict:
        """Return raw {item: {count, slot}} dict, refreshing cache if needed."""
        if not config.INVENTORY_READER_ENABLED:
            # See config.py's INVENTORY_READER_ENABLED comment — the local
            # model's replies don't parse as inventory JSON right now, so
            # every attempt was just burning up to ~60s per EXPLORE cycle
            # for nothing. Skip opening the menu entirely until that's fixed.
            return dict(self._cache)
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

        # A parse/LLM failure returns {'items': [], 'parse_error': True} —
        # keep the last known-good cache instead of clobbering it with that
        # placeholder, so a transient bad read doesn't erase real inventory
        # data callers already had. Still bump _cache_ts so the TTL/rate
        # limit applies and we don't hammer the LLM every tick.
        if not result.get('parse_error'):
            self._cache = result
        self._cache_ts = time.time()
        return dict(self._cache)

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
        press a close key.
        Routes to Anthropic (claude-haiku) for reliable JSON parsing —
        the local Qwen model returns bounding-box detection output instead."""
        raw = call_anthropic(
            _INVENTORY_PROMPT, max_tokens=600, images=[frame_to_b64(frame)],
            timeout=30,
            system='You are a Minecraft inventory assistant. Always respond with valid JSON only. Never output bounding boxes or labels.')
        if raw is None:
            # Anthropic failed — fall back to local as last resort
            print('[INV] Anthropic call failed — trying local fallback')
            raw, _provider = route_llm_call(
                _INVENTORY_PROMPT, max_tokens=600, images=[frame_to_b64(frame)],
                timeout=30, local_retries=1,
                system='You are a Minecraft inventory assistant. Always respond with valid JSON only. Never output bounding boxes or labels.')
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
