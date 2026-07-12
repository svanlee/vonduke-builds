# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Chest Manager                       ║
# ║  Opens chests, asks Claude to read contents, and       ║
# ║  shift-clicks items between chest and player inventory.║
# ╚══════════════════════════════════════════════════════╝

import base64
import json
import time
import urllib.request
import urllib.error

import cv2

import config


# Cache TTL — don't re-read chest contents more often than this
_CACHE_TTL_SEC = 30.0

# ── Chest grid slot → screen % (chest UI, single chest) ────────
# Slots 0-26: 3×9 chest grid (top of screen).
# Slots 27-62: player inventory (3×9 main + hotbar) below the chest —
# same column pitch/origin as the regular inventory screen.
_CHEST_X0   = 31.5    # left edge of chest column 0 (%)
_CHEST_DX   = 4.5     # column pitch (%)
_CHEST_Y0   = 28.0    # top edge of chest row 0 (%)
_CHEST_DY   = 8.0     # row pitch (%)
_CHEST_COLS = 9

_PLAYER_X0        = 31.5   # same x layout as chest grid / regular inventory
_PLAYER_DX        = 4.5
_PLAYER_Y0        = 59.5   # top edge of player main grid row 0 (%)
_PLAYER_DY        = 8.0
_PLAYER_HOTBAR_Y  = 84.5
_PLAYER_SLOT_BASE = 27     # chest-screen slot where player inventory starts


def _frame_to_b64(frame) -> str:
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode('utf-8')


def _chest_slot_pct(slot: int) -> tuple[float, float]:
    """Convert a chest-grid slot index (0-26 single / 0-53 double) to screen %."""
    if slot < 0:
        return (50.0, 90.0)
    row, col = divmod(slot, _CHEST_COLS)
    return _CHEST_X0 + col * _CHEST_DX, _CHEST_Y0 + row * _CHEST_DY


def _player_slot_pct(inv_slot: int) -> tuple[float, float]:
    """Convert an InventoryReader-style player slot index (0-26 main, 27-35
    hotbar) to screen % within the chest UI (player grid sits below chest)."""
    if inv_slot < 0:
        return (50.0, 90.0)
    if inv_slot >= 27:   # hotbar
        col = inv_slot - 27
        return _PLAYER_X0 + col * _PLAYER_DX, _PLAYER_HOTBAR_Y
    row, col = divmod(inv_slot, 9)
    return _PLAYER_X0 + col * _PLAYER_DX, _PLAYER_Y0 + row * _PLAYER_DY


_CHEST_PROMPT = """This is a Minecraft chest inventory screen.
List all non-empty slots. Chest slots are 0-26 (top 3 rows, left-to-right).
Player inventory slots 27-62 (3 rows + hotbar below the chest).
Return ONLY valid JSON: {"cobblestone": {"count": 64, "slot": 3}, ...}
If no chest is open, return: {"chest_closed": true}"""


class ChestManager:
    """Open a chest, ask Claude to read its contents, and move items
    between chest and player inventory via shift-click."""

    def __init__(self):
        self._cache    = {}     # last read result: {item: {count, slot}}
        self._cache_ts = 0.0
        self._was_open = False  # True only if the last read confirmed a chest UI was open

    # ── Public API ───────────────────────────────────────────────

    def open(self, executor, capture_fn):
        """Right-click the chest, wait for the UI, and capture a frame."""
        print('[CHEST] opening chest')
        self._click(executor, 50.0, 50.0, button='right')
        time.sleep(0.7)
        return capture_fn()

    def read_contents(self, frame, force: bool = False) -> dict:
        """Ask Claude to read chest contents from `frame`. Cached for
        _CACHE_TTL_SEC unless force=True or frame is None."""
        now = time.time()
        if not force and now - self._cache_ts < _CACHE_TTL_SEC:
            return dict(self._cache)
        if frame is None:
            print('[CHEST] no frame — skipping read')
            self._was_open = False
            return dict(self._cache)

        items, was_open = self._ask_claude(frame)
        print(f'[CHEST] read: {items}')
        self._cache    = items
        self._cache_ts = time.time()
        self._was_open = was_open
        return dict(items)

    def store_item(self, executor, item: str, inv_slot: int):
        """Shift-click an item in the player's inventory to move it to the chest."""
        x, y = _player_slot_pct(inv_slot)
        print(f'[CHEST] storing {item} (inv slot {inv_slot})')
        self._shift_click(executor, x, y)

    def retrieve_item(self, executor, item: str, chest_slot: int):
        """Shift-click an item in the chest to move it to the player's inventory."""
        x, y = _chest_slot_pct(chest_slot)
        print(f'[CHEST] retrieving {item} (chest slot {chest_slot})')
        self._shift_click(executor, x, y)

    def close(self, executor):
        if not self._was_open:
            # The right-click never actually opened a chest UI (missed the
            # block, or the read failed) — Escape here would just pop the
            # pause menu instead of closing nothing.
            print('[CHEST] no confirmed-open chest — skipping close key')
            return
        print('[CHEST] closing')
        executor.execute({'key': 'escape', 'click': None, 'gamepad': None, 'source': 'chest'})
        time.sleep(0.2)

    def invalidate(self):
        """Force next read_contents() to re-query."""
        self._cache_ts = 0.0

    def has(self, item: str, min_count: int = 1) -> bool:
        return self._cache.get(item, {}).get('count', 0) >= min_count

    # ── Internals ────────────────────────────────────────────────

    def _ask_claude(self, frame) -> tuple[dict, bool]:
        """Returns (items, was_open) — was_open is False when the chest was
        confirmed closed (or the read failed), so close() knows not to
        press Escape."""
        b64 = _frame_to_b64(frame)
        payload = json.dumps({
            "model": config.CLAUDE_MODEL,
            "max_tokens": 512,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": _CHEST_PROMPT}
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
                    stop   = data.get('stop_reason', 'unknown')
                    ctypes = [b.get('type') for b in data.get('content', [])]
                    raise ValueError(
                        f'no text block in Claude response '
                        f'(stop_reason={stop}, content_types={ctypes})'
                    )
                text = text_block['text'].strip()
                if text.startswith('```'):
                    text = '\n'.join(text.split('\n')[1:-1])
                items = json.loads(text)
                if items.get('chest_closed'):
                    print('[CHEST] Claude says chest was not open')
                    return {}, False
                result = {}
                for k, v in items.items():
                    if not isinstance(k, str) or k == 'chest_closed':
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
            except urllib.error.HTTPError as e:
                print(f'[CHEST] Claude HTTP {e.code} on attempt {attempt+1}')
                if e.code not in (429, 500, 502, 503, 529):
                    break
            except Exception as e:
                print(f'[CHEST] error on attempt {attempt+1}: {e}')

            if attempt < 2:
                time.sleep(2 ** attempt)

        # Couldn't confirm state — don't claim it was open.
        return {}, False

    def _click(self, executor, x_pct: float, y_pct: float, button: str = 'left'):
        action = {
            'key': None, 'click': [x_pct, y_pct],
            'gamepad': None, 'source': 'chest',
        }
        if button == 'right':
            action['button'] = 'right'
        executor.execute(action)

    def _shift_click(self, executor, x_pct: float, y_pct: float):
        action = {
            'key': 'shift', 'click': [x_pct, y_pct],
            'gamepad': None, 'source': 'chest',
        }
        executor.execute(action)
        time.sleep(0.35)
