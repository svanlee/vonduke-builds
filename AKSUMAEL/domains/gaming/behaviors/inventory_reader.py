# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inventory Reader                   ║
# ║  Opens the inventory, locates the slot grid, and      ║
# ║  identifies each icon by sprite template match.       ║
# ╚══════════════════════════════════════════════════════╝
#
# This used to composite every non-empty slot into one grid image and ask
# the local vision model to name them all in a single JSON reply. It
# averaged about one usable label per 23 slots. Two independent reasons,
# both now removed rather than tuned around:
#
#   • The slot coordinates were hardcoded for "1920×1080 at GUI scale 2",
#     a 36px pitch. The capture card actually delivers a ~25px pitch, so
#     the crops were sampling panel background and the world behind it —
#     which is what produced the "all 36 slot crops appear empty" reads in
#     data/live.log. vision/inventory_grid.py now fits the grid to the
#     frame instead of assuming it.
#
#   • Naming a Minecraft item is a lookup, not a perception problem. The
#     sprites are fixed pixel art, so vision/sprite_matcher.py compares
#     them against a stored library and gets an exact answer. The model is
#     left with the one job it is actually needed for: putting a name on a
#     sprite the library has never seen before, once, after which the
#     answer is cached forever.
#
# The library grows on its own. An unrecognised sprite is filed immediately
# under `unknown_<hash>`, so a slot is identified consistently from its
# first sighting even when nothing can name it; the name catches up later,
# from the model or from tools/inv_templates.py.

import re
import time

import config
from core.llm_router import route_llm_call, frame_to_b64
from vision.inventory_grid import locate_grid
from vision.sprite_matcher import (CANON_PX, PAD_GUI, SEARCH_PX,
                                   UNKNOWN_PREFIX, SpriteLibrary,
                                   is_empty_crop)


# Cache TTL — don't re-open inventory more often than this
_CACHE_TTL_SEC = 15.0

# Labels sometimes arrive as the repr of a numpy array of class names —
# "['iron_pickaxe' 'pickaxe']" — or of a Python list, "['iron_pickaxe']".
_REPR_LABEL_RE = re.compile(r"^[\[\(](.*)[\]\)]$", re.DOTALL)
_ITEM_ID_RE    = re.compile(r'^[a-z0-9_]+$')
# Keys a detection-style entry may carry the item name under.
_LABEL_KEYS = ('label', 'item', 'name', 'item_name', 'class')

# How large a 16×16 sprite is blown up to before it goes to the model. The
# canonical template is 48px, which is small enough that a vision model
# sees almost nothing; nearest-neighbour keeps the pixel art crisp.
_NAMING_VIEW_PX = 192

_NAME_PROMPT = (
    "This is a single Minecraft item icon from an inventory slot, "
    "enlarged. Name the item and nothing else. "
    "Reply with only its snake_case Minecraft ID — for example "
    "oak_log, cobblestone, iron_pickaxe, oak_planks, stick, torch. "
    "No sentence, no punctuation, no explanation."
)

_NAME_SYSTEM = (
    "You are a Minecraft item identifier. You reply with exactly one "
    "snake_case item ID and nothing else."
)


def _read_failed() -> dict:
    """The sentinel a read returns when it could not see the inventory.

    It matters that this is distinguishable from a genuinely empty
    inventory: _read_raw keeps the previous cache for a failed read and
    replaces it for an empty one. Returning a bare {} for both is how a
    single dark frame used to wipe everything the crafting logic knew.
    """
    return {'items': [], 'parse_error': True}


def _clean_label(value) -> str | None:
    """Normalise one model label into a bare snake_case Minecraft item id.

    Handles the shapes seen live: plain 'oak_log', namespaced
    'minecraft:iron_pickaxe', and Python/numpy reprs like
    "['iron_pickaxe' 'pickaxe']" (first name wins). Returns None when the
    value can't be reduced to a plausible item id."""
    if isinstance(value, dict):
        value = next((value[k] for k in _LABEL_KEYS if k in value), None)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    # Quotes come back on maybe a quarter of replies — '"minecraft:iron_pickaxe"'
    # is a correct answer that used to be thrown away for having them.
    text = str(value).strip().strip('`"\'').strip()

    # Unwrap a repr of a list/array of names and keep the first one.
    m = _REPR_LABEL_RE.match(text)
    if m:
        names = re.findall(r"""['"]([^'"]+)['"]""", m.group(1)) or m.group(1).split()
        if not names:
            return None
        text = names[0].strip()

    # A one-word reply is the ask; anything longer is a sentence that
    # happens to contain the answer, and guessing which word is the item
    # is how 'the' and 'this' ended up in label positions before.
    text = text.split('\n')[0].strip()
    if len(text.split()) > 1:
        return None

    text = text.split(':')[-1]                    # drop 'minecraft:' namespace
    text = text.lower().replace(' ', '_').replace('-', '_').strip('.,_')
    if not text or len(text) > 50 or not _ITEM_ID_RE.match(text):
        return None
    return text


# One library shared by every reader instance — it is backed by files on
# disk, and two copies would each hold a stale half of the same index.
_LIBRARY: SpriteLibrary | None = None


def get_library() -> SpriteLibrary:
    """The process-wide sprite library, loaded on first use."""
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = SpriteLibrary(
            threshold=getattr(config, 'INVENTORY_MATCH_THRESHOLD', 0.92))
        print(f'[INV] sprite library: {len(_LIBRARY)} templates '
              f'({_LIBRARY.named_count} named)')
    return _LIBRARY


class InventoryReader:
    """Open the inventory, identify every slot, return {item: count}."""

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
        the current frame and classifies it. Used by crafting code that has
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
            items, was_open = self._classify_slots(frame)
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

        # A failed read returns {'items': [], 'parse_error': True} — keep the
        # last known-good cache instead of clobbering it with that
        # placeholder, so a transient bad read doesn't erase real inventory
        # data callers already had. Still bump _cache_ts so the TTL/rate
        # limit applies and we don't re-open the menu every tick.
        if not result.get('parse_error'):
            self._cache = result
        self._cache_ts = time.time()
        return dict(self._cache)

    # ── Internals ────────────────────────────────────────────────

    def _do_read(self) -> dict:
        # Reading the inventory means opening it and looking at it. With no
        # camera (vision-less mode — see core/capture.py) the look step can
        # never succeed, so opening it just costs two keypresses and ~1.6s of
        # the tick before bailing out below. Probe first and skip the whole
        # sequence instead.
        if self.capture() is None:
            return _read_failed()

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
            return _read_failed()

        items, was_open = self._classify_slots(frame)
        print(f'[INV] read: {items}')

        if was_open:
            # Press Escape to close (safer than E which could toggle a different menu)
            self._tap('escape', 300)
        else:
            # We pressed 'e' above, so leaving without a close key risks
            # stranding the GUI open. 'e' is the safe undo either way: it
            # closes the inventory if it did open, and is a no-op-or-toggle
            # if it didn't.
            print('[INV] inventory not detected — toggling e back')
            self._tap('e', 200)
        return items

    def _classify_slots(self, frame) -> tuple[dict, bool]:
        """Identify every slot on `frame`. Returns (items_dict, was_open).

        `was_open` comes from whether a GUI panel could be located, which is
        direct evidence about what is on screen. The old test — "did all 36
        crops look low-variance?" — answered "not open" for any sufficiently
        dark scene, because a night-time cave is as uniform as a bare slot.
        """
        grid = locate_grid(frame)
        if grid is None:
            print('[INV] no inventory GUI on screen')
            return _read_failed(), False

        lib = get_library()
        lib.reload_if_changed()      # pick up names edited on disk

        by_slot: dict[int, str] = {}
        new_keys: list[tuple[str, 'object']] = []
        filled = matched = 0

        for slot_idx in range(36):
            canon = grid.crop(frame, slot_idx, out_px=CANON_PX)
            if canon is None or is_empty_crop(canon):
                continue
            filled += 1
            search = grid.crop(frame, slot_idx, pad_gui=PAD_GUI,
                               out_px=SEARCH_PX)
            res = lib.match_or_add(search, canon, source='live')
            if res is None:
                continue
            key, label, _score, is_new = res
            by_slot[slot_idx] = label
            if is_new:
                new_keys.append((key, canon))
            else:
                matched += 1

        print(f'[INV] grid at pitch {grid.pitch:.1f}px — {filled} filled slots, '
              f'{matched} matched, {len(new_keys)} new sprite(s)')

        if new_keys:
            renamed = self._name_new_templates(lib, new_keys)
            # A sprite that just got a real name should report it in this
            # read, not only the next one.
            for slot_idx, label in list(by_slot.items()):
                if label in renamed:
                    by_slot[slot_idx] = renamed[label]

        lib.save()

        if not by_slot:
            # The panel was found, so the menu genuinely is open — it just
            # has nothing in it. That is a real, cacheable answer.
            return {}, True

        # Build {item_name: {count, slot}} — multiple slots of same item → sum.
        # NOTE: `count` is slots-holding-the-item, not the summed stack size;
        # reading the stack digits is a separate problem from naming sprites.
        result: dict[str, dict] = {}
        for slot_idx, item_name in sorted(by_slot.items()):
            if item_name in result:
                result[item_name]['count'] += 1
            else:
                result[item_name] = {'count': 1, 'slot': slot_idx}

        print(f'[INV] identified: {list(result.keys())}')
        return result, True

    def _name_new_templates(self, lib, new_keys) -> dict:
        """Ask the local model to name sprites the library just filed.

        Returns {placeholder_label: real_label} for the ones it managed to
        name. Everything about this is best-effort: a template that can't be
        named keeps its `unknown_<hash>` label and stays perfectly usable as
        an identity, and can be named later by tools/inv_templates.py.
        """
        renamed: dict[str, str] = {}
        if not getattr(config, 'INVENTORY_LLM_NAMING', True):
            return renamed
        if not getattr(config, 'LOCAL_LLM_ENABLED', True):
            return renamed

        # A first read against an empty library can turn up 20+ new sprites.
        # Naming them all inline would stall the tick for minutes, so take a
        # few per read and let the rest be named on subsequent opens.
        budget = int(getattr(config, 'INVENTORY_NAMING_PER_READ', 3))
        import cv2

        for key, canon in new_keys[:budget]:
            view = cv2.resize(canon, (_NAMING_VIEW_PX, _NAMING_VIEW_PX),
                              interpolation=cv2.INTER_NEAREST)
            raw, _ = route_llm_call(
                _NAME_PROMPT, max_tokens=24, images=[frame_to_b64(view)],
                timeout=config.LOCAL_LLM_TIMEOUT, local_retries=0,
                system=_NAME_SYSTEM)
            name = _clean_label(raw) if raw else None
            if not name:
                print(f'[INV] could not name new sprite {key} '
                      f'— raw={str(raw)[:60]!r}')
                continue

            # Two different sprites are two different items, so a name that
            # is already spoken for is the model repeating itself rather
            # than recognising something. Measured on a 25-sprite library it
            # answered `golden_carrot` three times and `iron_pickaxe` four,
            # so this rejects a large share of the wrong answers for free.
            owner = lib.label_taken_by(name, exclude=key)
            if owner:
                print(f'[INV] rejecting name {name!r} for {key} '
                      f'— already held by {owner}')
                continue

            placeholder = UNKNOWN_PREFIX + key
            if lib.set_label(key, name, source='llm'):
                renamed[placeholder] = name
                print(f'[INV] named new sprite {key} → {name}')
        return renamed

    def _tap(self, key: str, wait_ms: int):
        self.executor.execute({
            'key': key, 'click': None, 'gamepad': None, 'source': 'inventory',
        })
        time.sleep(wait_ms / 1000.0)
