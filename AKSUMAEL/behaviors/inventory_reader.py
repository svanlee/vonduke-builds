# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inventory Reader                   ║
# ║  Opens inventory, asks Claude to read it, returns     ║
# ║  a structured {item: count} dict for crafting logic.  ║
# ╚══════════════════════════════════════════════════════╝

import base64
import json
import time
import urllib.request
import urllib.error

import cv2

import config


# Cache TTL — don't re-open inventory more often than this
_CACHE_TTL_SEC = 15.0


def _frame_to_b64(frame) -> str:
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode('utf-8')


_INVENTORY_PROMPT = """This is a Minecraft Java Edition inventory screen.
List every item stack you can identify in the main inventory grid (3 rows × 9 cols)
and the hotbar (bottom row of 9 slots). Include the crafting 2x2 area only if
items are already placed there.

Respond with ONLY a JSON object mapping item names to counts, e.g.:
{"cobblestone": 23, "stick": 8, "oak_log": 4, "oak_planks": 0, "stone_pickaxe": 1}

Use Minecraft snake_case item IDs (oak_log, oak_planks, cobblestone, stick, etc.).
If a slot is empty, skip it. If a count is unclear, estimate conservatively.
Output ONLY the JSON object, nothing else."""


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
        """Return {item: count} from inventory.  Uses cache unless force=True."""
        now = time.time()
        if not force and now - self._cache_ts < _CACHE_TTL_SEC:
            return dict(self._cache)

        if self._reading:
            return dict(self._cache)   # avoid re-entry during crafting

        self._reading = True
        try:
            result = self._do_read()
        finally:
            self._reading = False

        self._cache    = result
        self._cache_ts = time.time()
        return dict(result)

    def has(self, item: str, min_count: int = 1) -> bool:
        """Check if inventory (cached) has at least min_count of item."""
        return self._cache.get(item, 0) >= min_count

    def invalidate(self):
        """Force next read() to re-query."""
        self._cache_ts = 0.0

    # ── Internals ────────────────────────────────────────────────

    def _do_read(self) -> dict:
        print('[INV] opening inventory')
        self._tap('e', 600)          # open inventory

        # Give the UI a moment to render
        time.sleep(0.4)

        frame = self.capture()
        if frame is None:
            print('[INV] no frame — closing')
            self._tap('e', 200)
            return {}

        items = self._ask_claude(frame)
        print(f'[INV] read: {items}')

        self._tap('e', 200)          # close inventory
        return items

    def _ask_claude(self, frame) -> dict:
        b64 = _frame_to_b64(frame)
        payload = json.dumps({
            "model": config.CLAUDE_MODEL,
            "max_tokens": 512,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": _INVENTORY_PROMPT}
                ]
            }]
        }).encode('utf-8')

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': config.ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            }
        )

        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                text_block = next(
                    (b for b in data.get('content', []) if b.get('type') == 'text'),
                    None
                )
                if text_block is None:
                    raise ValueError('no text block in Claude response')
                text = text_block['text'].strip()
                if text.startswith('```'):
                    text = '\n'.join(text.split('\n')[1:-1])
                items = json.loads(text)
                # Sanitise: ensure all values are ints, filter junk keys
                return {
                    k.lower().replace(' ', '_'): max(0, int(v))
                    for k, v in items.items()
                    if isinstance(k, str) and isinstance(v, (int, float))
                }
            except urllib.error.HTTPError as e:
                print(f'[INV] Claude HTTP {e.code} on attempt {attempt+1}')
                if e.code not in (429, 500, 502, 503, 529):
                    break
            except Exception as e:
                print(f'[INV] error on attempt {attempt+1}: {e}')

            if attempt < 2:
                time.sleep(2 ** attempt)

        return {}

    def _tap(self, key: str, wait_ms: int):
        self.executor.execute({
            'key': key, 'click': None, 'gamepad': None, 'source': 'inventory',
        })
        time.sleep(wait_ms / 1000.0)
