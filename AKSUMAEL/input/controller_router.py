# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Controller Router                  ║
# ║  Priority: evdev → I2C joystick → aksumael-only       ║
# ╚══════════════════════════════════════════════════════╝

import threading
import time
import config


# ── Blend modes ───────────────────────────────────────────────
BLEND_MODES = ('aksumael_only', 'human_only', 'assist', 'blend')


class ControllerState:
    """Unified input state shared across all input sources."""
    def __init__(self):
        self.lx = 0        # left stick X  (-127 to +127)
        self.ly = 0        # left stick Y
        self.rx = 0        # right stick X
        self.ry = 0        # right stick Y
        self.lt = 0        # left trigger  (0-255)
        self.rt = 0        # right trigger
        self.buttons = 0   # 16-bit bitmask
        self.keys = []     # keyboard keys held
        self.click = None      # [x_pct, y_pct] absolute click, or None
        self.button = None     # 'left'/'right' for click, or None (defaults to left)
        self.look = None       # {'dx':.., 'dy':..} relative mouse-look, or None
        self.delay_ms = None   # ms to hold key/click, or None (executor default)
        self.source = 'none'   # which source last wrote this
        self.ts = 0.0          # timestamp of last update

    def as_dict(self) -> dict:
        return {
            'lx': self.lx, 'ly': self.ly,
            'rx': self.rx, 'ry': self.ry,
            'lt': self.lt, 'rt': self.rt,
            'buttons': self.buttons,
            'keys': list(self.keys),
            'source': self.source,
        }

    def reset(self):
        self.lx = self.ly = self.rx = self.ry = 0
        self.lt = self.rt = 0
        self.buttons = 0
        self.keys = []
        self.click = None
        self.button = None
        self.look = None
        self.delay_ms = None


# ── Evdev reader (real controller plugged into the laptop) ─────
class EvdevController:
    """
    Reads a real USB/Bluetooth controller via Linux evdev.
    Runs in a background thread, updates ControllerState.
    """
    def __init__(self, state: ControllerState):
        self.state = state
        self.device = None
        self._thread = None
        self._running = False
        self._available = False
        self._axis_range = {}
        self._find_device()

    def _find_device(self):
        try:
            from evdev import list_devices, InputDevice, categorize, ecodes
            devices = [InputDevice(p) for p in list_devices()]
            # Prefer devices with absolute axes (gamepad/joystick)
            for dev in devices:
                caps = dev.capabilities()
                if ecodes.EV_ABS in caps:
                    self.device = dev
                    self._available = True
                    self._read_axis_ranges()
                    print(f'[EVDEV] found controller: {dev.name}')
                    return
            print('[EVDEV] no gamepad found')
        except ImportError:
            print('[EVDEV] evdev not installed — skipping real controller')
        except Exception as e:
            print(f'[EVDEV] detection error: {e}')

    def _read_axis_ranges(self):
        """
        Read the real min/max for each axis from the device.
        Controllers vary wildly (0-255, -32768..32767, 0-65535),
        so we must query absinfo rather than assume a fixed range.
        """
        from evdev import ecodes
        self._axis_range = {}   # code → (min, max)
        try:
            caps = self.device.capabilities()
            for code, absinfo in caps.get(ecodes.EV_ABS, []):
                # absinfo is an AbsInfo namedtuple (value, min, max, ...)
                self._axis_range[code] = (absinfo.min, absinfo.max)
            print(f'[EVDEV] calibrated {len(self._axis_range)} axes')
        except Exception as e:
            print(f'[EVDEV] axis range read failed: {e} — using defaults')
            self._axis_range = {}

    def start(self):
        if not self._available:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop,
                                        daemon=True, name='evdev')
        self._thread.start()

    def _read_loop(self):
        from evdev import categorize, ecodes
        try:
            for event in self.device.read_loop():
                if not self._running:
                    break
                self._handle_event(event)
        except Exception as e:
            print(f'[EVDEV] read error: {e}')
            self._available = False

    def _handle_event(self, event):
        from evdev import ecodes
        s = self.state
        if event.type == ecodes.EV_ABS:
            # Sticks: normalise using the axis's real range
            if event.code == ecodes.ABS_X:
                s.lx = self._norm_axis(event.code, event.value)
            elif event.code == ecodes.ABS_Y:
                s.ly = self._norm_axis(event.code, event.value)
            elif event.code == ecodes.ABS_RX:
                s.rx = self._norm_axis(event.code, event.value)
            elif event.code == ecodes.ABS_RY:
                s.ry = self._norm_axis(event.code, event.value)
            # Triggers: scale to 0-255
            elif event.code == ecodes.ABS_Z:
                s.lt = self._norm_trigger(event.code, event.value)
            elif event.code == ecodes.ABS_RZ:
                s.rt = self._norm_trigger(event.code, event.value)
        elif event.type == ecodes.EV_KEY:
            btn_map = {
                ecodes.BTN_A:      0x0001,
                ecodes.BTN_B:      0x0002,
                ecodes.BTN_X:      0x0004,
                ecodes.BTN_Y:      0x0008,
                ecodes.BTN_TL:     0x0010,
                ecodes.BTN_TR:     0x0020,
                ecodes.BTN_SELECT: 0x0040,
                ecodes.BTN_START:  0x0080,
            }
            mask = btn_map.get(event.code, 0)
            if mask:
                if event.value:   # pressed
                    s.buttons |= mask
                else:             # released
                    s.buttons &= ~mask
        s.source = 'evdev'
        s.ts = time.time()

    def _norm_axis(self, code: int, raw: int) -> int:
        """Normalise a stick axis to -127..+127 using its real range."""
        lo, hi = self._axis_range.get(code, (0, 65535))
        if hi == lo:
            return 0
        mid = (hi + lo) / 2.0
        half = (hi - lo) / 2.0
        return max(-127, min(127, int((raw - mid) / half * 127)))

    def _norm_trigger(self, code: int, raw: int) -> int:
        """Scale a trigger axis to 0..255 using its real range."""
        lo, hi = self._axis_range.get(code, (0, 255))
        if hi == lo:
            return 0
        return max(0, min(255, int((raw - lo) / (hi - lo) * 255)))

    def stop(self):
        self._running = False

    @property
    def is_active(self) -> bool:
        return self._available and self._running


# ── I2C Joystick (mini module fallback) ───────────────────────
class I2CJoystick:
    """
    Reads the mini I2C joystick module at address 0x5A.
    Used as fallback when no real controller is connected.

    Register map (typical for this module):
        0x00 = X axis (0-255, centre ~128)
        0x01 = Y axis (0-255, centre ~128)
        0x02 = Button bitmask (A=bit0, B=bit1, C=bit2, D=bit3)
    """
    JOYSTICK_BUTTONS = {
        0: ('A', 0x0001),   # A → gamepad A / good reward
        1: ('B', 0x0002),   # B → gamepad B / bad reward
        2: ('C', 0x0004),   # C → pause/resume
        3: ('D', 0x0008),   # D → cycle blend mode
    }

    def __init__(self, state: ControllerState,
                 addr: int = None, bus: int = None):
        self.state = state
        self.addr = addr or config.I2C_JOY_ADDR
        self.bus_num = bus or config.I2C_BUS
        self.dead = config.I2C_DEADZONE
        self._bus = None
        self._available = False
        self._thread = None
        self._running = False
        self._connect()

    def _connect(self):
        try:
            import smbus2
            self._bus = smbus2.SMBus(self.bus_num)
            # Probe the address
            self._bus.read_byte(self.addr)
            self._available = True
            print(f'[I2C_JOY] found at 0x{self.addr:02X} on bus {self.bus_num}')
        except ImportError:
            print('[I2C_JOY] smbus2 not installed — skipping I2C joystick')
        except Exception as e:
            print(f'[I2C_JOY] not found (0x{self.addr:02X}): {e}')

    def start(self):
        if not self._available:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop,
                                        daemon=True, name='i2c_joy')
        self._thread.start()

    def _poll_loop(self):
        while self._running:
            try:
                self._read()
            except Exception as e:
                print(f'[I2C_JOY] poll error: {e}')
                time.sleep(1)
            time.sleep(0.02)   # ~50 Hz

    def _read(self):
        x_raw = self._bus.read_byte_data(self.addr, 0x00)
        y_raw = self._bus.read_byte_data(self.addr, 0x01)
        btns  = self._bus.read_byte_data(self.addr, 0x02)

        lx = self._deadzone(x_raw - 128)
        ly = self._deadzone(y_raw - 128)

        btn_mask = 0
        for bit, (name, mask) in self.JOYSTICK_BUTTONS.items():
            if btns & (1 << bit):
                btn_mask |= mask

        s = self.state
        s.lx = lx
        s.ly = ly
        s.buttons = btn_mask
        s.source = 'i2c_joy'
        s.ts = time.time()

    def _deadzone(self, v: int) -> int:
        return v if abs(v) > self.dead else 0

    def stop(self):
        self._running = False

    @property
    def is_active(self) -> bool:
        return self._available and self._running


# ── Controller Router ─────────────────────────────────────────
class ControllerRouter:
    """
    Manages input priority chain and blend mode.

    Priority (highest → lowest):
        1. evdev   — real controller plugged into the laptop
        2. i2c_joy — mini I2C joystick (if no real controller)
        3. aksumael  — pure AI decisions (always present)

    Blend modes:
        aksumael_only  — only AI input reaches the output
        human_only   — only physical controller (evdev/i2c) reaches output
        assist       — human drives, AI adds/corrects
        blend        — weighted mix of human + AI
    """
    STALENESS_SEC = 0.5   # controller input older than this = stale

    def __init__(self):
        self.human_state  = ControllerState()
        self.aksumael_state = ControllerState()
        self.blend_mode   = config.BLEND_MODE

        # Build input chain based on config
        self.evdev = None
        self.i2c   = None

        if config.ENABLE_EVDEV:
            self.evdev = EvdevController(self.human_state)

        # Only use I2C joystick if no real controller found
        if config.ENABLE_I2C_JOY:
            if self.evdev is None or not self.evdev.is_active:
                self.i2c = I2CJoystick(self.human_state)

        self._report_sources()

    def _report_sources(self):
        evdev_ok = self.evdev and self.evdev.is_active
        i2c_ok   = self.i2c and self.i2c.is_active
        if evdev_ok:
            src = 'real controller (evdev)'
        elif i2c_ok:
            src = 'I2C joystick (fallback)'
        else:
            src = 'aksumael-only (no physical controller)'
        print(f'[ROUTER] human input source: {src}')
        print(f'[ROUTER] blend mode: {self.blend_mode}')

    def start(self):
        if self.evdev:
            self.evdev.start()
        if self.i2c:
            self.i2c.start()

    def stop(self):
        if self.evdev:
            self.evdev.stop()
        if self.i2c:
            self.i2c.stop()

    def update_aksumael(self, action_dict: dict):
        """
        Called each tick with the AI's desired action.
        Converts action_dict keys/gamepad to aksumael_state.
        """
        s = self.aksumael_state
        s.reset()

        key = action_dict.get('key')
        if key and key not in ('null', 'none', None, 'wait'):
            s.keys = [str(key).lower()]

        # click/look/delay_ms/button — see 2026-07-15 fix: this method used
        # to only carry key+gamepad through to resolve()/_state_to_action(),
        # silently dropping click/look/delay_ms for every action that (like
        # core/fsm.py's MINE state) is dispatched solely through
        # update_aksumael()->resolve()->executor.execute() with no earlier
        # direct executor.execute() call of its own. That meant MINE's
        # left-click and its per-tick aim-correction 'look' were computed
        # correctly but never actually reached the KB2040 — looked exactly
        # like a lost-focus/uncaptured-cursor problem (aim never converged,
        # clicks never landed) but was really just this router discarding
        # the data before it got anywhere near hardware.
        s.click    = action_dict.get('click')
        s.button   = action_dict.get('button')
        s.look     = action_dict.get('look')
        s.delay_ms = action_dict.get('delay_ms')

        gp = action_dict.get('gamepad') or {}
        s.lx = gp.get('lx', 0)
        s.ly = gp.get('ly', 0)
        s.rx = gp.get('rx', 0)
        s.ry = gp.get('ry', 0)
        s.lt = gp.get('lt', 0)
        s.rt = gp.get('rt', 0)
        s.buttons = gp.get('buttons', 0)
        s.source = 'aksumael'
        s.ts = time.time()

    def _human_is_fresh(self) -> bool:
        return (time.time() - self.human_state.ts) < self.STALENESS_SEC

    def resolve(self) -> dict:
        """
        Apply blend mode and return the final merged action dict
        ready for the CH9329 packer.
        """
        h = self.human_state
        a = self.aksumael_state
        fresh = self._human_is_fresh()

        if self.blend_mode == 'aksumael_only':
            return self._state_to_action(a)

        elif self.blend_mode == 'human_only':
            return self._state_to_action(h) if fresh else self._idle()

        elif self.blend_mode == 'assist':
            # Human drives; AI fills in when human is idle
            if fresh and (h.lx or h.ly or h.buttons or h.keys):
                return self._state_to_action(h)
            return self._state_to_action(a)

        elif self.blend_mode == 'blend':
            # Weighted average of axes; OR of buttons; human keys win
            if fresh:
                merged = ControllerState()
                merged.lx = int((h.lx + a.lx) / 2)
                merged.ly = int((h.ly + a.ly) / 2)
                merged.rx = int((h.rx + a.rx) / 2)
                merged.ry = int((h.ry + a.ry) / 2)
                merged.lt = max(h.lt, a.lt)
                merged.rt = max(h.rt, a.rt)
                merged.buttons = h.buttons | a.buttons
                merged.keys = list(set(h.keys + a.keys))
                # Human sources never populate click/look/delay_ms today
                # (only lx/ly/rx/ry/buttons/keys — see EvdevController /
                # I2CJoystick below), so this is effectively "always
                # aksumael's", but prefer human's if a future source ever
                # sets one, same precedence as keys above.
                merged.click    = h.click    if h.click    is not None else a.click
                merged.button   = h.button   if h.button   is not None else a.button
                merged.look     = h.look     if h.look     is not None else a.look
                merged.delay_ms = h.delay_ms if h.delay_ms is not None else a.delay_ms
                merged.source = 'blend'
                return self._state_to_action(merged)
            return self._state_to_action(a)

        return self._idle()

    @staticmethod
    def _state_to_action(s: ControllerState) -> dict:
        key = s.keys[0] if s.keys else None
        ad = {
            'key': key,
            'click': s.click,
            'look': s.look,
            'gamepad': {
                'lx': s.lx, 'ly': s.ly,
                'rx': s.rx, 'ry': s.ry,
                'lt': s.lt, 'rt': s.rt,
                'buttons': s.buttons,
            },
            'source': s.source,
        }
        if s.button is not None:
            ad['button'] = s.button
        # Only set delay_ms when the state actually specified one — an
        # explicit None here would override executor._execute_hid()'s
        # ad.get('delay_ms', config.KEY_HOLD_MS) default (a present key
        # with value None is not the same as an absent key to .get()).
        if s.delay_ms is not None:
            ad['delay_ms'] = s.delay_ms
        return ad

    @staticmethod
    def _idle() -> dict:
        return {'key': None, 'click': None,
                'gamepad': {'lx':0,'ly':0,'rx':0,'ry':0,
                            'lt':0,'rt':0,'buttons':0},
                'source': 'idle'}

    def cycle_blend_mode(self):
        idx = BLEND_MODES.index(self.blend_mode)
        self.blend_mode = BLEND_MODES[(idx + 1) % len(BLEND_MODES)]
        print(f'[ROUTER] blend mode → {self.blend_mode}')
        return self.blend_mode

    def set_blend_mode(self, mode: str):
        if mode in BLEND_MODES:
            self.blend_mode = mode
            print(f'[ROUTER] blend mode set to {mode}')


if __name__ == '__main__':
    print('Controller Router self-test (no hardware needed)')
    router = ControllerRouter()
    router.start()

    # Simulate an AI action
    router.update_aksumael({'key': 'w', 'confidence': 0.8})
    result = router.resolve()
    print('Resolved action:', result)

    # Test blend mode cycling
    for _ in range(4):
        mode = router.cycle_blend_mode()
        print(f'  mode: {mode}')

    router.stop()
    print('Done.')
