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
#   Left stick  -> native gamepad X/Y axes (Minecraft's own controller
#                  support reads these directly — see rp2040/code.py's
#                  handle_gamepad() / rp2040/boot.py's
#                  GAMEPAD_REPORT_DESCRIPTOR)
#   Right stick -> native gamepad Z/Rz axes (look)
#   A/B/X/Y/LB/RB -> forwarded as native gamepad buttons 1-6 every tick
#                  (held state, not taps — 2026-07-22: previously A/X/Y
#                  were converted to space/mouse-hold/right-click, which
#                  only worked because Minecraft was being driven as a
#                  keyboard+mouse app; now that the KB2040 is a real USB
#                  HID gamepad, Minecraft's controller bindings own this)
#   D-pad up/dn -> hotbar scroll (relative, wraps through slots 1-9) —
#                  still keyboard taps; the gamepad HID descriptor has no
#                  hat-switch usage, so there's no native axis for this
#   D-pad l/r   -> hotbar slot select (same wraparound, opposite step)
#   LT          -> crouch/sneak (left shift, true hold — pressed once on
#                  LT down, released once on LT up via 'key_hold'; see
#                  uart/kb2040_packer.py. Retapping shift every poll tick
#                  is what used to trip Windows Sticky Keys, 2026-07-19).
#                  Still keyboard — the gamepad descriptor declares only
#                  4 axes (X/Y/Z/Rz), no trigger axes, so LT/RT have no
#                  native HID representation on this device.
#   RT          -> sprint (left ctrl, true hold, same mechanism as LT)
#   Start       -> toggle HUMAN / AI mode (never sends a keyboard key or
#                  reaches the gamepad report)
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

POLL_HZ            = 60    # was 20 — 2026-07-19 latency pass. evdev event
                            # capture itself is interrupt-driven (near-zero
                            # added delay, see _event_loop's blocking
                            # read_loop()); this is the dispatch-loop cadence
                            # that turns cached stick/button state into HID
                            # actions, so it's the real ceiling on responsiveness.
POLL_INTERVAL_SEC  = 1.0 / POLL_HZ

# Delay between the press and release packet of a discrete tap (jump,
# hotbar, use/place) — see action_dict_to_packets/KB2040Serial.send_action,
# which sleeps delay_ms after *every* packet it sends. Actions below that
# don't pass their own delay_ms fall back to config.KEY_HOLD_MS (500ms,
# tuned for the AI's 250ms FSM tick, see config.py) — that fallback used to
# silently apply here too, blocking this 20/60Hz dispatch loop for up to a
# full second per jump/hotbar/use-place tap and 500ms per look update
# (single packet, no release to pair with — the 500ms sleep served no
# purpose at all) or crouch/sprint/mine hold edge. HID_TAP_MS is just long
# enough for a game running at 60fps to see the key go down and back up as
# two distinct frames; hold/look actions pass delay_ms=0 outright since
# there's nothing after them to sequence against.
HID_TAP_MS         = 15

# Raw stick axes are normalised to -127..+127 (see _norm_axis, matching
# input/controller_router.py's EvdevController). ~8000/32767 of full
# deflection, converted to this module's -127..127 scale, to filter out
# analog drift/noise near center regardless of the controller's actual
# raw axis range (2026-07-19).
STICK_DEADZONE     = 31
MOVE_DEADZONE      = 40     # coarser deadzone for movement so light drift
                             # on the right stick doesn't walk the player
                             # (already stricter than STICK_DEADZONE, so no
                             # change needed here for the same drift-noise ask)
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
        self._evdev_missing = False   # true once we know evdev itself isn't installed
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
        self._hotbar_idx   = 0

        # Context supplied by core/runtime.py each main-loop tick so
        # imitation rows carry real screen/FSM state regardless of which
        # 20Hz poll tick an action happened to land on.
        self._latest_objects   = []
        self._latest_pos       = (None, None, None)
        self._latest_fsm_state = None

        self._find_device()

    # ── Device discovery ────────────────────────────────────────
    # Probed once at startup and then re-probed on a timer (see
    # _supervisor_loop) so plugging a controller in after AKSUMAEL is
    # already running doesn't require a restart to pick it up.
    PROBE_INTERVAL_SEC = 5

    def _find_device(self, quiet: bool = False) -> bool:
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
                    return True
            if not quiet:
                print(f'[HumanAssist] no Xbox controller found on /dev/input — '
                      f'will keep checking every {self.PROBE_INTERVAL_SEC}s')
            return False
        except ImportError:
            print('[HumanAssist] python-evdev not installed — human-assist disabled')
            self._evdev_missing = True
            return False
        except Exception as e:
            if not quiet:
                print(f'[HumanAssist] controller detection error: {e}')
            return False

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
        if self._evdev_missing:
            return
        self._running = True
        self._thread = threading.Thread(target=self._supervisor_loop, daemon=True,
                                        name='human_assist_supervisor')
        self._thread.start()
        print(f'[HumanAssist] supervisor started — '
              f'{"controller already connected" if self._available else "probing for a controller"}')

    def _supervisor_loop(self):
        """Owns the connect/disconnect lifecycle: launches the event+dispatch
        threads once a controller is found, waits for them to end (which
        only happens on physical disconnect — see _event_loop/_dispatch_loop),
        then goes back to probing on a timer so a controller plugged in
        after AKSUMAEL started still gets picked up without a restart."""
        while self._running:
            if not self._available:
                self._find_device(quiet=True)
            if not self._available:
                time.sleep(self.PROBE_INTERVAL_SEC)
                continue

            self._event_thread = threading.Thread(target=self._event_loop, daemon=True,
                                                   name='human_assist_evdev')
            self._event_thread.start()
            print(f'[HumanAssist] controller connected — dispatch loop starting ({POLL_HZ}Hz)')
            self._dispatch_loop()   # blocks until _running=False or the device drops
            if self._running:
                print('[HumanAssist] controller disconnected — will keep probing '
                      f'every {self.PROBE_INTERVAL_SEC}s')
                if self.human_mode:
                    # Nobody's holding the controller anymore — don't leave
                    # the FSM paused indefinitely or a key/click stuck down.
                    self.human_mode = False
                    self.executor.release_all()
                    # release_all() just cleared shift/ctrl on the HID side —
                    # forget any pending hold state so a still-held LT/RT on
                    # reconnect re-sends the down edge instead of assuming
                    # it's still asserted (2026-07-19).
                    self._prev_lt_held = False
                    self._prev_rt_held = False
                    print('[HumanAssist] Switched to AI mode (controller lost mid-session)')

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
                elif event.code == ecodes.ABS_HAT0X:
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
        # Shared by both d-pad axes: ABS_HAT0Y (-1=up/+1=down) and
        # ABS_HAT0X (-1=left/+1=right), 0 = released. Only fires on the
        # press edge (0 -> nonzero) so a held d-pad doesn't spam the
        # hotbar every event. up/left step back a slot, down/right step
        # forward — same wraparound cycle for both scroll and select.
        if value == -1:
            self._hotbar_idx = (self._hotbar_idx - 1) % 9
            self._queue_hotbar = True
        elif value == 1:
            self._hotbar_idx = (self._hotbar_idx + 1) % 9
            self._queue_hotbar = True

    # ── 20Hz dispatch: map state -> HID actions + record episode ──
    def _dispatch_loop(self):
        self._queue_hotbar = False
        while self._running and self._available:
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
        self._prev_buttons = buttons

        # Start always toggles mode, in either mode, so a human stuck in
        # AI mode can always take back control. This must be a pure
        # internal state flip — it never emits a keyboard/mouse packet
        # itself, and it returns immediately so this tick's stick/trigger
        # state (which may be stale relative to the mode that just
        # changed) never reaches action dispatch below (2026-07-19,
        # urgent fix: toggling Start while LT was physically held used to
        # phantom-fire a SHIFT press — see _prev_lt_held note below).
        if rising & 0x0080:
            self.human_mode = not self.human_mode
            if self.human_mode:
                print('[HumanAssist] Switched to HUMAN mode')
                # Resync the trigger edge-detect baseline to whatever's
                # physically held right now. Without this, a trigger
                # already held at the instant mode is entered reads as a
                # brand-new press-edge next dispatch tick — against a
                # stale _prev_*_held left over from before (False at
                # startup, or force-reset False by the AI-mode branch
                # below) — and fires a phantom SHIFT/CTRL key_hold
                # 'down'. Mash Start while resting a finger on LT and
                # that repeats on every toggle: a burst of real SHIFT
                # presses in quick succession, which is exactly what
                # trips Windows Sticky Keys. This was the actual source
                # of the flood, not a retap loop.
                self._prev_lt_held = lt > TRIGGER_THRESHOLD
                self._prev_rt_held = rt > TRIGGER_THRESHOLD
            else:
                print('[HumanAssist] Switched to AI mode')
                # Don't leave a key/mouse button or gamepad button
                # physically down from the instant control was handed
                # back to the FSM.
                self.executor.release_all()
                self._prev_lt_held = False
                self._prev_rt_held = False
            return

        if not self.human_mode:
            return

        action_parts = []

        # Sticks + buttons -> one native gamepad HID report per tick
        # (see rp2040/code.py's handle_gamepad() / rp2040/boot.py's
        # GAMEPAD_REPORT_DESCRIPTOR: 4 signed axes + 16 buttons). Sent as
        # live held state, not discrete taps — Minecraft's own controller
        # bindings (Options > Controls > Controller) decide what A/X/Y and
        # the sticks do, the same way they would for a real Xbox pad.
        # bit7 (Start, 0x0080) is masked out: it's AKSUMAEL's own
        # HUMAN/AI toggle above and must never reach the game.
        gp_lx = lx if abs(lx) > MOVE_DEADZONE else 0
        gp_ly = ly if abs(ly) > MOVE_DEADZONE else 0
        gp_rx = rx if abs(rx) > STICK_DEADZONE else 0
        gp_ry = ry if abs(ry) > STICK_DEADZONE else 0
        gp_buttons = buttons & ~0x0080
        self.executor.execute({
            'gamepad': {'lx': gp_lx, 'ly': gp_ly, 'rx': gp_rx, 'ry': gp_ry,
                       'buttons': gp_buttons},
            'delay_ms': 0, 'source': 'human',
        })
        if gp_lx or gp_ly or gp_rx or gp_ry or gp_buttons:
            action_parts.append(
                f'gp(lx={gp_lx},ly={gp_ly},rx={gp_rx},ry={gp_ry},btn={gp_buttons:04x})')

        # D-pad -> hotbar scroll (edge-queued in _handle_dpad)
        if hotbar_pending:
            slot = HOTBAR_SLOTS[self._hotbar_idx]
            self.executor.execute({'key': slot, 'delay_ms': HID_TAP_MS, 'source': 'human'})
            action_parts.append(f'hotbar_{slot}')

        # LT -> crouch/sneak, true hold: press shift once on the LT-down
        # edge, release it once on the LT-up edge (key_hold in
        # uart/kb2040_packer.py). Retapping shift every poll tick used to
        # send a full press+release pair at 20Hz for as long as LT was
        # held — a rapid-fire SHIFT loop that trips Windows Sticky Keys
        # (2026-07-19).
        lt_held = lt > TRIGGER_THRESHOLD
        if lt_held and not self._prev_lt_held:
            self.executor.execute({'key_hold': 'down', 'key': 'lshift', 'delay_ms': 0, 'source': 'human'})
            action_parts.append('crouch_start')
        elif not lt_held and self._prev_lt_held:
            self.executor.execute({'key_hold': 'up', 'key': 'lshift', 'delay_ms': 0, 'source': 'human'})
            action_parts.append('crouch_stop')
        elif lt_held:
            action_parts.append('crouch')
        self._prev_lt_held = lt_held

        # RT -> sprint, same true-hold mechanism as LT.
        rt_held = rt > TRIGGER_THRESHOLD
        if rt_held and not self._prev_rt_held:
            self.executor.execute({'key_hold': 'down', 'key': 'lctrl', 'delay_ms': 0, 'source': 'human'})
            action_parts.append('sprint_start')
        elif not rt_held and self._prev_rt_held:
            self.executor.execute({'key_hold': 'up', 'key': 'lctrl', 'delay_ms': 0, 'source': 'human'})
            action_parts.append('sprint_stop')
        elif rt_held:
            action_parts.append('sprint')
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
