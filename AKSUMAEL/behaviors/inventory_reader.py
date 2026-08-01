# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inventory Reader                   ║
# ║  Opens inventory, crops each slot, asks local LLM     ║
# ║  to classify items; returns {item: count} for craft.  ║
# ╚══════════════════════════════════════════════════════╝

import json
import re
import time

import numpy as np

import config
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


# ── Inventory screen slot layout (vanilla Java 1920×1080 GUI scale 2) ──────
# Panel origin derived from CRAFT_GRID_2x2 calibration in crafting.py:
#   (0,0) = (51.0%, 38.0%) = (979px, 410px), which is gui(98,18) from panel.
#   → panel_x = 979 - 98*2 = 783,  panel_y = 410 - 18*2 = 374
_PANEL_X      = 783    # inventory panel left edge (pixels)
_PANEL_Y      = 374    # inventory panel top edge (pixels)
_GUI_SCALE    = 2      # Minecraft GUI scale multiplier
_SLOT_GUI     = 18     # slot pitch in gui units (= 36 pixels at scale 2)
_ICON_GUI     = 16     # icon size in gui units  (= 32 pixels at scale 2)
_ICON_PX      = _ICON_GUI * _GUI_SCALE   # 32px — crop half-width = 16
_CELL_PX      = 64     # upscale each crop to 64×64 for the composite

# Main inventory grid: 3 rows × 9 cols, starting at gui (8, 84) from panel
_MAIN_GX0, _MAIN_GY0 = 8, 84
# Hotbar: 1 row × 9 cols, starting at gui (8, 142) from panel
_HBAR_GY0 = 142

# Slot variance threshold — empty slots show nearly uniform gray background
_EMPTY_STD_THRESHOLD = 12.0

_SLOT_CLASSIFY_PROMPT = (
    "These are Minecraft inventory slot icon images arranged left-to-right, "
    "top-to-bottom in a {cols}-column grid (each cell is {cell}×{cell}px). "
    "The slot indices in order are: [{indices}]. "
    "Identify the Minecraft item in each cell using its snake_case ID "
    "(e.g. oak_log, cobblestone, iron_pickaxe, oak_planks, stick, torch). "
    "Reply ONLY as compact JSON: {{\"slot_index\": \"item_name\", ...}}. "
    "Omit slots that appear empty. No explanation, no markdown."
)

_SLOT_CLASSIFY_SYSTEM = (
    "You are a Minecraft item classifier. "
    "Given a grid of item sprite images, output a JSON dict mapping "
    "slot index strings to snake_case item names. JSON only."
)


def _slot_center_px(slot_idx: int) -> tuple[int, int]:
    """Return the pixel (cx, cy) of the centre of inventory slot 0-35."""
    if slot_idx >= 27:          # hotbar
        col = slot_idx - 27
        gx = _MAIN_GX0 + col * _SLOT_GUI + _SLOT_GUI // 2
        gy = _HBAR_GY0 + _SLOT_GUI // 2
    else:                       # main inventory
        row, col = divmod(slot_idx, 9)
        gx = _MAIN_GX0 + col * _SLOT_GUI + _SLOT_GUI // 2
        gy = _MAIN_GY0 + row * _SLOT_GUI + _SLOT_GUI // 2
    return _PANEL_X + gx * _GUI_SCALE, _PANEL_Y + gy * _GUI_SCALE


def _crop_slot(frame, slot_idx: int):
    """Extract a 32×32 crop centred on the slot icon, or None if out-of-bounds."""
    import cv2
    cx, cy = _slot_center_px(slot_idx)
    half = _ICON_PX // 2   # 16
    h, w = frame.shape[:2]
    if cy - half < 0 or cy + half > h or cx - half < 0 or cx + half > w:
        return None
    return frame[cy - half : cy + half, cx - half : cx + half].copy()


def _is_empty(crop) -> bool:
    """True when the slot crop is nearly uniform — i.e., empty air background."""
    if crop is None or crop.size == 0:
        return True
    return float(np.std(crop.astype(np.float32))) < _EMPTY_STD_THRESHOLD


def _build_composite(crops_64: list) -> 'np.ndarray':
    """Stack 64×64 BGR crops into a grid (4 columns max) for a single LLM call."""
    import cv2
    COLS = 4
    rows = (len(crops_64) + COLS - 1) // COLS
    composite = np.zeros((rows * _CELL_PX, COLS * _CELL_PX, 3), dtype=np.uint8)
    for i, crop in enumerate(crops_64):
        r, c = divmod(i, COLS)
        scaled = cv2.resize(crop, (_CELL_PX, _CELL_PX), interpolation=cv2.INTER_NEAREST)
        composite[r * _CELL_PX:(r + 1) * _CELL_PX,
                  c * _CELL_PX:(c + 1) * _CELL_PX] = scaled
    return composite


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
        """Classify inventory slots locally using per-slot crops.

        Extracts a 32×32 crop for each of the 36 inventory slots (0-26 main,
        27-35 hotbar) at calibrated pixel positions, skips slots whose pixel
        variance indicates an empty air background, composes the remaining crops
        into a single small grid image, and asks the local mesh-llm model to
        identify all items in one call.

        This avoids sending the full inventory GUI screenshot, which causes
        Qwen3.5-Vision to return GUI bounding-box output instead of item names.
        Returns (items_dict, was_open).
        """
        # ── 1. Collect non-empty slot crops ─────────────────────────────
        non_empty: list[tuple[int, 'np.ndarray']] = []
        for slot_idx in range(36):
            crop = _crop_slot(frame, slot_idx)
            if not _is_empty(crop):
                non_empty.append((slot_idx, crop))

        # If every slot looks empty, the inventory is probably not open —
        # when the game world is visible at those coordinates, variance is
        # high and slots appear "full". Low total non-empty count on a live
        # frame usually means the screen IS showing the inventory (most slots
        # really are empty). But ALL 36 looking empty is suspicious.
        if not non_empty:
            print('[INV] all 36 slot crops appear empty — inventory not open?')
            return {}, False

        print(f'[INV] {len(non_empty)} non-empty slots → local classification')

        # ── 2. Build composite image ──────────────────────────────────────
        crops_64 = [c for _, c in non_empty]
        composite = _build_composite(crops_64)

        COLS = 4
        slot_indices = ', '.join(str(s) for s, _ in non_empty)
        prompt = _SLOT_CLASSIFY_PROMPT.format(
            cols=COLS, cell=_CELL_PX, indices=slot_indices)

        # ── 3. Single local LLM call ──────────────────────────────────────
        raw, _ = route_llm_call(
            prompt, max_tokens=300, images=[frame_to_b64(composite)],
            timeout=config.LOCAL_LLM_TIMEOUT, local_retries=1,
            system=_SLOT_CLASSIFY_SYSTEM)

        if raw is None:
            print('[INV] local LLM call failed')
            return {'items': [], 'parse_error': True}, False

        # ── 4. Parse JSON response ────────────────────────────────────────
        parsed = _parse_json_response(raw)
        if not isinstance(parsed, dict):
            print(f'[INV] unexpected response ({type(parsed).__name__}) — raw={raw[:200]!r}')
            return {'items': [], 'parse_error': True}, False

        # Build {item_name: {count, slot}} — multiple slots of same item → sum
        result: dict[str, dict] = {}
        for slot_key, item_name in parsed.items():
            if not isinstance(item_name, str):
                continue
            item_name = item_name.lower().replace(' ', '_').strip('.')
            if not item_name or len(item_name) > 50:
                continue
            try:
                slot_idx = int(slot_key)
            except (ValueError, TypeError):
                continue
            if item_name in result:
                result[item_name]['count'] += 1
            else:
                result[item_name] = {'count': 1, 'slot': slot_idx}

        print(f'[INV] local classified: {list(result.keys())}')
        return result, bool(result)

    def _tap(self, key: str, wait_ms: int):
        self.executor.execute({
            'key': key, 'click': None, 'gamepad': None, 'source': 'inventory',
        })
        time.sleep(wait_ms / 1000.0)
