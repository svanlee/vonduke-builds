# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Action Executor                    ║
# ║  kb2040 | ch9329 | bridge | print                     ║
# ╚══════════════════════════════════════════════════════╝
#
# A `click: [x_pct, y_pct]` in an action dict becomes a TYPE_MOUSE_A
# packet (uart.kb2040_packer.pack_mouse_click_at) that the KB2040 lands
# on its dedicated absolute-pointer HID device — see
# rp2040/code.py::handle_mouse_abs() — not the relative MOUSE device used
# for camera-look (`look: {dx, dy}` / TYPE_MOUSE_R).
#
# config.KB2040_MODE = "bridge" overrides ACTION_OUTPUT = "kb2040": the
# board is then flashed with uart/kb2040_bridge.py and speaks JSON, not
# HID packets, so sending it a 0xAA 0xBB frame would be noise. In that
# mode game actions are logged (there is no game) and .bridge holds a
# BridgeClient for GPIO/UART/I2C/SPI work.

import config


# xdotool keysym map — converts AKSUMAEL action key names to X11 keysym names.
# Single letters and digits pass through unchanged (xdotool accepts lowercase).
_XDOTOOL_KEYMAP = {
    'space': 'space', 'lshift': 'shift', 'rshift': 'shift',
    'lctrl': 'ctrl',  'rctrl': 'ctrl',
    'lalt': 'alt',    'ralt': 'alt',
    'tab': 'Tab', 'escape': 'Escape', 'esc': 'Escape',
    'enter': 'Return', 'return': 'Return', 'backspace': 'BackSpace',
    'delete': 'Delete', 'home': 'Home', 'end': 'End',
    'pageup': 'Prior', 'pagedown': 'Next',
    'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
    'f1': 'F1', 'f2': 'F2', 'f3': 'F3', 'f4': 'F4',
    'f5': 'F5', 'f6': 'F6', 'f7': 'F7', 'f8': 'F8',
    'f9': 'F9', 'f10': 'F10', 'f11': 'F11', 'f12': 'F12',
}


class ActionExecutor:
    def __init__(self):
        self.mode     = config.ACTION_OUTPUT.lower()
        self.platform = config.PLATFORM_TARGET.lower()
        self._hid     = None   # KB2040Serial or CH9329Serial
        self.bridge   = None   # BridgeClient, when KB2040_MODE == 'bridge'
        self._xdotool = False  # True when xdotool is available as fallback

        # The KB2040 can only be one thing at a time. When it's flashed as
        # a hardware bridge, an ACTION_OUTPUT of 'kb2040' is stale config,
        # not an instruction — honour the firmware, don't fight it.
        if (self.mode == 'kb2040' and
                getattr(config, 'KB2040_MODE', 'hid').lower() == 'bridge'):
            self.mode = 'bridge'

        # Remember what the user actually asked for, so we know what to
        # reconnect back to if we fall back to 'print' at startup.
        self._intended_mode = self.mode
        # Cached Minecraft X11 window ID for xdotool targeting.
        self._minecraft_win_id: str | None = None
        # Track inventory/menu state via 'e' key presses instead of relying
        # on YOLO detection (inventory class had 0 training examples — 2026-07-31).
        # 'e' toggles menu open/closed; 'escape' always closes.
        self._menu_open: bool = False

        if self.mode == 'kb2040':
            self._init_kb2040()
        elif self.mode == 'ch9329':
            self._init_ch9329()
        elif self.mode == 'bridge':
            self._init_bridge()
        # 'print' needs no init

        # xdotool fallback: used whenever no HID device is present so the
        # bot can send keystrokes to Minecraft via X11 even without the KB2040.
        self._xdotool = self._init_xdotool()

        print(f'[ACTION] mode:{self.mode}  platform:{self.platform}')

    def _init_xdotool(self) -> bool:
        """Check xdotool availability. Used as HID fallback when KB2040 absent."""
        import shutil, os
        if shutil.which('xdotool') is None:
            return False
        self._xdotool_display = os.environ.get('DISPLAY', ':0')
        print(f'[ACTION] xdotool available — keystroke fallback active '
              f'(DISPLAY={self._xdotool_display})')
        return True

    def _init_bridge(self):
        """KB2040 as a general hardware bridge — no HID, no packets.

        A missing bridge is not fatal: self.bridge stays None and game
        actions keep getting logged, exactly as in print mode. Nothing in
        the FSM depends on the bridge, so a bench rig that isn't plugged
        in yet shouldn't stop AKSUMAEL from booting.
        """
        try:
            from uart.bridge_client import BridgeClient
            self.bridge = BridgeClient(port=getattr(config, 'BRIDGE_PORT', None),
                                       baud=getattr(config, 'BRIDGE_BAUD', 115200))
            if self.bridge.is_connected:
                print('[ACTION] KB2040 hardware bridge online — '
                      'game actions are log-only')
            else:
                print('[ACTION] KB2040 bridge not found — game actions '
                      'log-only, bridge calls will retry on demand')
        except Exception as e:
            print(f'[ACTION] bridge init failed: {e}')
            self.bridge = None

    def _init_kb2040(self):
        try:
            from uart.kb2040_packer import KB2040Serial
            self._hid = KB2040Serial()
            if not self._hid.is_connected:
                print('[ACTION] KB2040 not connected — falling back to print')
                self.mode = 'print'
        except Exception as e:
            print(f'[ACTION] KB2040 init failed: {e} — falling back to print')
            self.mode = 'print'

    def _init_ch9329(self):
        try:
            from uart.ch9329_packer import CH9329Serial
            self._hid = CH9329Serial()
            if not self._hid.is_connected:
                print('[ACTION] CH9329 not connected — falling back to print')
                self.mode = 'print'
        except Exception as e:
            print(f'[ACTION] CH9329 init failed: {e} — falling back to print')
            self.mode = 'print'

    @property
    def menu_open(self) -> bool:
        """True when the bot has opened a menu screen (inventory, chest, etc.).
        Tracked via key presses rather than YOLO so it works without training data."""
        return self._menu_open

    def execute(self, action_dict: dict):
        if not action_dict:
            return
        # Key-press menu tracking: 'e' toggles inventory; 'escape'/'esc' always closes.
        _key = (action_dict.get('key') or '').lower()
        if _key == 'e':
            self._menu_open = not self._menu_open
        elif _key in ('escape', 'esc'):
            self._menu_open = False
        if self.mode == 'print':
            self._check_reconnect()
        # 'bridge' logs like 'print' — there is no HID device to send to,
        # and silently dropping the action would make a stale KB2040_MODE
        # look like a dead FSM.
        if self.mode in ('print', 'bridge'):
            self._print_action(action_dict)
            # xdotool fallback: send real keystrokes to the X11 window when
            # no HID device is present. Camera-look (relative mouse) is skipped
            # because xdotool cannot send relative motion; key + mouse_button
            # actions work normally. This is what makes the bot functional
            # without the KB2040 plugged in.
            if self._xdotool:
                self._xdotool_action(action_dict)
        else:
            self._execute_hid(action_dict)

    def _check_reconnect(self):
        """If we fell back to print because the HID device wasn't there at
        startup, periodically retry the connection (rate-limited inside
        the HID driver itself) and resume HID output once it's back."""
        if self._hid is None or self._intended_mode not in ('kb2040', 'ch9329'):
            return
        if hasattr(self._hid, 'try_reconnect') and self._hid.try_reconnect():
            self.mode = self._intended_mode
            print(f'[ACTION] {self._intended_mode} reconnected — resuming HID output')

    def _get_minecraft_win_id(self) -> str | None:
        """Return Minecraft's X11 window ID, cached after first lookup.

        Returns None if the window is not found — callers must skip the
        keystroke entirely rather than falling back to active-window injection.
        """
        import subprocess, os
        if self._minecraft_win_id is not None:
            return self._minecraft_win_id
        env = {**os.environ, 'DISPLAY': getattr(self, '_xdotool_display', ':0')}
        for search_args in (
            ['xdotool', 'search', '--name', 'Minecraft'],
            ['xdotool', 'search', '--class', 'minecraft'],
        ):
            try:
                r = subprocess.run(search_args, env=env, capture_output=True,
                                   text=True, timeout=3)
                wids = r.stdout.strip().split()
                if wids:
                    self._minecraft_win_id = wids[0]
                    print(f'[ACTION] Minecraft window ID locked: {self._minecraft_win_id}')
                    return self._minecraft_win_id
            except Exception:
                pass
        print('[ACTION] WARNING: Minecraft window not found — xdotool keystroke skipped')
        return None

    def _xdotool_action(self, ad: dict):
        """Inject keystrokes / mouse clicks via xdotool when no HID device present.

        Covers key taps and mouse button clicks. Mouse-look (relative dx/dy)
        requires hardware and is intentionally skipped — xdotool mousemove is
        absolute and would pull the camera off target.

        All xdotool calls use --window targeting so keystrokes land in
        Minecraft and never leak into the active window.
        """
        import subprocess, os
        env = {**os.environ, 'DISPLAY': getattr(self, '_xdotool_display', ':0')}

        win_id = self._get_minecraft_win_id()
        if win_id is None:
            return  # Safety: skip rather than inject into wrong window

        key = (ad.get('key') or '').lower().strip()
        if key and key not in ('null', 'none', 'wait', ''):
            xkey = _XDOTOOL_KEYMAP.get(key, key)
            try:
                subprocess.run(
                    ['xdotool', 'key', '--window', win_id, '--clearmodifiers', xkey],
                    env=env, timeout=1, capture_output=True,
                )
            except Exception as e:
                print(f'[ACTION] xdotool key error: {e}')

        mouse_btn = ad.get('mouse_button')
        if mouse_btn in ('left', 'right'):
            btn_num = '1' if mouse_btn == 'left' else '3'
            try:
                subprocess.run(
                    ['xdotool', 'click', '--window', win_id, btn_num],
                    env=env, timeout=1, capture_output=True,
                )
            except Exception as e:
                print(f'[ACTION] xdotool click error: {e}')

    def _print_action(self, ad: dict):
        key   = ad.get('key')
        click = ad.get('click')
        gp    = ad.get('gamepad') or {}
        src   = ad.get('source', '?')
        parts = [f'src:{src}']
        if key:   parts.append(f'key:{key}')
        if click: parts.append(f'click:{click}')
        if gp and any(gp.values()):
            parts.append(
                f'gp:lx={gp.get("lx",0)} ly={gp.get("ly",0)} '
                f'btn={gp.get("buttons",0):04x}'
            )
        print(f'[ACTION] → {" | ".join(parts)}')

    def _execute_hid(self, ad: dict):
        if self._hid:
            delay = ad.get('delay_ms', config.KEY_HOLD_MS)
            self._hid.send_action(ad, platform=self.platform, delay_ms=delay)

    def release_all(self):
        if self._hid:
            self._hid.release_all()
        elif self.bridge:
            # Bridge equivalent of "let go of everything": deinit every
            # claimed pin/bus so a half-configured output isn't left
            # driving whatever is wired to it.
            self.bridge.release_all()

    def reset_device(self):
        """Forward to KB2040Serial.reset_device() (no-op on ch9329/print
        modes, or on unflashed KB2040 firmware — see that method). In
        bridge mode this reboots the board the same way, via its JSON
        'reset' command."""
        if self._hid and hasattr(self._hid, 'reset_device'):
            self._hid.reset_device()
        elif self.bridge:
            self.bridge.reset_device()

    def close(self):
        self.release_all()
        if self._hid:
            self._hid.close()
        if self.bridge:
            self.bridge.close()
