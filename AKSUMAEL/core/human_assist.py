# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Human-Assist Mode                         ║
# ║  Xbox controller passthrough + imitation-learning log ║
# ╚══════════════════════════════════════════════════════╝
#
# Plug an Xbox controller into Victus and press Start: AKSUMAEL's FSM
# pauses (core/runtime.py checks .human_mode each tick) and controller
# input is forwarded straight to the game through the same KB2040 HID
# path AKSUMAEL's own actions use (actions/executor.py). Every tick
# spent in human mode is logged to data/aurora.db's human_episodes
# table (screen state + action + FSM context) for later behavioral
# cloning / skill generation.
#
# Button map (Xbox pad, evdev ecodes — see input/controller_router.py
# for the same BTN_* constants used elsewhere in this codebase):
#   Left stick  -> mouse look (yaw/pitch deltas)
#   Right stick -> WASD movement (left stick is the only "look" input
#                  the spec calls for, but a play session needs a way
#                  to actually walk — this is a deliberate addition,
#                  easy to rip out if unwanted)
#   A           -> jump (space)
#   B           -> sprint toggle (left ctrl, assumes "Toggle Sprint" is
#                  on in the game's controls options)
#   X           -> attack/mine (left mouse, real press-and-hold so
#                  block-break progress doesn't reset every tick)
#   Y           -> use/place (right mouse, single tap)
#   D-pad up/dn -> hotbar scroll (number keys 1-9)
#   LT          -> crouch (left shift, retapped each poll tick — see
#                  _dispatch note on why there's no true "hold")
#   RT          -> interact (right mouse, alternate to Y)
#   Start       -> toggle HUMAN / AI mode
import json
import os
import sqlite3
import threading
import time

import config

DB_PATH = os.path.join('data', 'aurora.db')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS human_episodes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    screen_state_json TEXT,
    action_taken      TEXT,
    fsm_context       TEXT
);
"""

POLL_HZ            = 20
POLL_INTERVAL_SEC  = 1.0 / POLL_HZ
POLL_INTERVAL_MS   = int(POLL_INTERVAL_SEC * 1000)

# Raw stick axes are normalised to -127..+127 (see _norm_axis, matching
# input/controller_router.py's EvdevController).
STICK_DEADZONE     = 12
LOOK_SENSITIVITY   = 0.15   # mouse-look dx/dy per raw stick unit
MOVE_DEADZONE      = 40     # coarser deadzone for movement so light drift
                             # on the right stick doesn't walk the player
TRIGGER_THRESHOLD  = 40     # 0-255 trigger value that counts as "pressed"

HOTBAR_SLOTS = [str(n) for n in range(1, 10)]


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA busy_timeout = 2000')
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


class HumanAssist:
    """Background 20Hz Xbox-controller reader. Owns HUMAN_MODE and
    forwards input straight to actions/executor.py when active; records
    an imitation-learning episode to aurora.db every poll tick spent in
    human mode."""

    def __init__(self, executor):
        self.executor    = executor
        self.human_mode  = False

        self._device     = None
        self._available  = False
        self._axis_range = {}
        self._thread     = None
        self._running    = False
        self._conn       = None

        # Live stick/button state, updated by the blocking evdev
        # read_loop() thread as events arrive (mirrors
        # input/controller_router.py's EvdevController pattern).
        self._lx = self._ly = 0
        self._rx = self._ry = 0
        self._lt = self._rt = 0
        self._buttons = 0
        self._state_lock = threading.Lock()
        self._event_thread = None

        # Edge-detect bookkeeping (dispatch thread only).
        self._prev_buttons = 0
        self._prev_lt_held = False
        self._prev_rt_held = False
        self._mining       = False
        self._sprint_on    = False
        self._hotbar_idx   = 0

        # Context supplied by core/runtime.py each main-loop tick so
        # imitation rows carry real screen/FSM state regardless of which
        # 20Hz poll tick an action happened to land on.
        self._latest_objects   = []
        self._latest_pos       = (None, None, None)
        self._latest_fsm_state = None

        self._find_device()

    # ── Device discovery ────────────────────────────────────────
    def _find_device(self):
        try:
            from evdev import list_devices, InputDevice, ecodes
            for path in list_devices():
                dev = InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_ABS in caps and ecodes.EV_KEY in caps:
                    self._device = dev
                    self._available = True
                    self._read_axis_ranges()
                    print(f'[HumanAssist] found controller: {dev.name} ({path})')
                    return
            print('[HumanAssist] no Xbox controller found on /dev/input — human-assist disabled')
        except ImportError:
            print('[HumanAssist] python-evdev not installed — human-assist disabled')
        except Exception as e:
            print(f'[HumanAssist] controller detection error: {e}')

    def _read_axis_ranges(self):
        from evdev import ecodes
        try:
            for code, absinfo in self._device.capabilities().get(ecodes.EV_ABS, []):
                self._axis_range[code] = (absinfo.min, absinfo.max)
            print(f'[HumanAssist] calibrated {len(self._axis_range)} axes')
        except Exception as e:
            print(f'[HumanAssist] axis range read failed: {e} — using defaults')

    def _norm_axis(self, code, raw) -> int:
        lo, hi = self._axis_range.get(code, (0, 65535))
        if hi == lo:
            return 0
        mid, half = (hi + lo) / 2.0, (hi - lo) / 2.0
        return max(-127, min(127, int((raw - mid) / half * 127)))

    def _norm_trigger(self, code, raw) -> int:
        lo, hi = self._axis_range.get(code, (0, 255))
        if hi == lo:
            return 0
        return max(0, min(255, int((raw - lo) / (hi - lo) * 255)))

    # ── Lifecycle ────────────────────────────────────────────────
    def start(self):
        if not self._available:
            return
        self._running = True
        self._event_thread = threading.Thread(target=self._event_loop, daemon=True,
                                               name='human_assist_evdev')
        self._event_thread.start()
        self._thread = threading.Thread(target=self._dispatch_loop, daemon=True,
                                        name='human_assist_dispatch')
        self._thread.start()
        print('[HumanAssist] controller threads started (20Hz dispatch)')

    def stop(self):
        self._running = False
        if self.executor:
            self.executor.release_all()
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass

    @property
    def is_available(self) -> bool:
        return self._available

    # ── Context feed from core/runtime.py's main loop ───────────
    def update_context(self, objects: list, world_mem, fsm_state):
        self._latest_objects = [
            {'label': o.get('label') if isinstance(o, dict) else getattr(o, 'label', None),
             'conf':  o.get('conf')  if isinstance(o, dict) else getattr(o, 'conf', None)}
            for o in (objects or [])
        ]
        if world_mem is not None:
            self._latest_pos = (getattr(world_mem, 'pos_x', None),
                                getattr(world_mem, 'y_level', None),
                                getattr(world_mem, 'pos_z', None))
        self._latest_fsm_state = str(fsm_state) if fsm_state is not None else None

    # ── Raw event reader (blocking generator, own thread) ────────
    def _event_loop(self):
        from evdev import ecodes
        try:
            for event in self._device.read_loop():
                if not self._running:
                    break
                self._handle_event(event, ecodes)
        except Exception as e:
            print(f'[HumanAssist] event read error: {e}')
            self._available = False

    def _handle_event(self, event, ecodes):
        with self._state_lock:
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_X:
                    self._lx = self._norm_axis(event.code, event.value)
                elif event.code == ecodes.ABS_Y:
                    self._ly = self._norm_axis(event.code, event.value)
                elif event.code == ecodes.ABS_RX:
                    self._rx = self._norm_axis(event.code, event.value)
                elif event.code == ecodes.ABS_RY:
                    self._ry = self._norm_axis(event.code, event.value)
                elif event.code == ecodes.ABS_Z:
                    self._lt = self._norm_trigger(event.code, event.value)
                elif event.code == ecodes.ABS_RZ:
                    self._rt = self._norm_trigger(event.code, event.value)
                elif event.code == ecodes.ABS_HAT0Y:
                    self._handle_dpad(event.value)
            elif event.type == ecodes.EV_KEY:
                btn_map = {
                    ecodes.BTN_A:     0x0001,
                    ecodes.BTN_B:     0x0002,
                    ecodes.BTN_X:     0x0004,
                    ecodes.BTN_Y:     0x0008,
                    ecodes.BTN_TL:    0x0010,
                    ecodes.BTN_TR:    0x0020,
                    ecodes.BTN_START: 0x0080,
                }
                mask = btn_map.get(event.code, 0)
                if mask:
                    if event.value:
                        self._buttons |= mask
                    else:
                        self._buttons &= ~mask

    def _handle_dpad(self, value):
        # ABS_HAT0Y: -1 = up, +1 = down, 0 = released. Only fire on the
        # press edge (0 -> nonzero) so a held d-pad doesn't spam the
        # hotbar every event.
        if value == -1:
            self._hotbar_idx = (self._hotbar_idx - 1) % 9
            self._queue_hotbar = True
        elif value == 1:
            self._hotbar_idx = (self._hotbar_idx + 1) % 9
            self._queue_hotbar = True

    # ── 20Hz dispatch: map state -> HID actions + record episode ──
    def _dispatch_loop(self):
        self._queue_hotbar = False
        while self._running:
            t0 = time.time()
            try:
                self._dispatch_once()
            except Exception as e:
                print(f'[HumanAssist] dispatch error: {e}')
            elapsed = time.time() - t0
            if elapsed < POLL_INTERVAL_SEC:
                time.sleep(POLL_INTERVAL_SEC - elapsed)

    def _dispatch_once(self):
        with self._state_lock:
            lx, ly, rx, ry = self._lx, self._ly, self._rx, self._ry
            lt, rt, buttons = self._lt, self._rt, self._buttons
            hotbar_pending = self._queue_hotbar
            self._queue_hotbar = False

        rising = buttons & ~self._prev_buttons   # newly-pressed this tick
        falling = self._prev_buttons & ~buttons  # newly-released this tick
        self._prev_buttons = buttons

        # Start always toggles mode, in either mode, so a human stuck in
        # AI mode can always take back control.
        if rising & 0x0080:
            self.human_mode = not self.human_mode
            if self.human_mode:
                print('[HumanAssist] Switched to HUMAN mode')
            else:
                print('[HumanAssist] Switched to AI mode')
                # Don't leave a key/mouse button physically down from the
                # instant control was handed back to the FSM.
                if self._mining:
                    self.executor.execute({'mouse_hold': 'up', 'mouse_button_name': 'left',
                                           'source': 'human'})
                    self._mining = False
                self.executor.release_all()

        if not self.human_mode:
            return

        action_parts = []

        # Left stick -> mouse look
        if abs(lx) > STICK_DEADZONE or abs(ly) > STICK_DEADZONE:
            dx = int(lx * LOOK_SENSITIVITY)
            dy = int(ly * LOOK_SENSITIVITY)
            if dx or dy:
                self.executor.execute({'look': {'dx': dx, 'dy': dy}, 'source': 'human'})
                action_parts.append(f'look({dx},{dy})')

        # Right stick -> WASD movement. Not a real hardware hold (the
        # KB2040 keyboard packet is press-then-release) — instead each
        # dispatch tick sends one key press held for ~one poll interval
        # via delay_ms, back-to-back, which reads as continuous movement
        # in-game (same trick core/runtime.py uses for tree_fallback).
        move_key = None
        if ry < -MOVE_DEADZONE:
            move_key = 'w'
        elif ry > MOVE_DEADZONE:
            move_key = 's'
        elif rx < -MOVE_DEADZONE:
            move_key = 'a'
        elif rx > MOVE_DEADZONE:
            move_key = 'd'
        if move_key:
            self.executor.execute({'key': move_key, 'delay_ms': POLL_INTERVAL_MS,
                                   'source': 'human'})
            action_parts.append(f'move_{move_key}')

        # A -> jump
        if rising & 0x0001:
            self.executor.execute({'key': 'space', 'source': 'human'})
            action_parts.append('jump')

        # B -> sprint toggle
        if rising & 0x0002:
            self._sprint_on = not self._sprint_on
            self.executor.execute({'key': 'lctrl', 'source': 'human'})
            action_parts.append('sprint_on' if self._sprint_on else 'sprint_off')

        # X -> attack/mine, real press-and-hold via mouse_hold (matches
        # core/runtime.py's mining code — a plain tap resets block-break
        # progress every time it fires).
        if rising & 0x0004:
            self.executor.execute({'mouse_hold': 'down', 'mouse_button_name': 'left',
                                   'source': 'human'})
            self._mining = True
            action_parts.append('attack_start')
        if falling & 0x0004:
            self.executor.execute({'mouse_hold': 'up', 'mouse_button_name': 'left',
                                   'source': 'human'})
            self._mining = False
            action_parts.append('attack_stop')

        # Y -> use/place (single tap right click)
        if rising & 0x0008:
            self.executor.execute({'mouse_button': 'right', 'source': 'human'})
            action_parts.append('use_place')

        # D-pad -> hotbar scroll (edge-queued in _handle_dpad)
        if hotbar_pending:
            slot = HOTBAR_SLOTS[self._hotbar_idx]
            self.executor.execute({'key': slot, 'source': 'human'})
            action_parts.append(f'hotbar_{slot}')

        # LT -> crouch. No true keyboard hold exists on this HID path, so
        # retap each poll tick for ~one interval, same approach as
        # movement above.
        lt_held = lt > TRIGGER_THRESHOLD
        if lt_held:
            self.executor.execute({'key': 'lshift', 'delay_ms': POLL_INTERVAL_MS,
                                   'source': 'human'})
            action_parts.append('crouch')
        self._prev_lt_held = lt_held

        # RT -> interact (alternate right click, tap on rising edge only)
        rt_held = rt > TRIGGER_THRESHOLD
        if rt_held and not self._prev_rt_held:
            self.executor.execute({'mouse_button': 'right', 'source': 'human'})
            action_parts.append('interact')
        self._prev_rt_held = rt_held

        action_taken = '+'.join(action_parts) if action_parts else 'idle'
        self._record_episode(action_taken)

    # ── Imitation-learning recording ─────────────────────────────
    def _record_episode(self, action_taken: str):
        try:
            if self._conn is None:
                self._conn = _connect()
            px, py, pz = self._latest_pos
            screen_state = {
                'objects': self._latest_objects,
                'pos': {'x': px, 'y': py, 'z': pz},
            }
            self._conn.execute(
                'INSERT INTO human_episodes '
                '(timestamp, screen_state_json, action_taken, fsm_context) '
                'VALUES (?, ?, ?, ?)',
                (time.strftime('%Y-%m-%dT%H:%M:%S'),
                 json.dumps(screen_state), action_taken, self._latest_fsm_state))
            self._conn.commit()
        except Exception as e:
            print(f'[HumanAssist] episode record error: {e}')
            self._conn = None
