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


class ActionExecutor:
    def __init__(self):
        self.mode     = config.ACTION_OUTPUT.lower()
        self.platform = config.PLATFORM_TARGET.lower()
        self._hid     = None   # KB2040Serial or CH9329Serial
        self.bridge   = None   # BridgeClient, when KB2040_MODE == 'bridge'

        # The KB2040 can only be one thing at a time. When it's flashed as
        # a hardware bridge, an ACTION_OUTPUT of 'kb2040' is stale config,
        # not an instruction — honour the firmware, don't fight it.
        if (self.mode == 'kb2040' and
                getattr(config, 'KB2040_MODE', 'hid').lower() == 'bridge'):
            self.mode = 'bridge'

        # Remember what the user actually asked for, so we know what to
        # reconnect back to if we fall back to 'print' at startup.
        self._intended_mode = self.mode
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

        print(f'[ACTION] mode:{self.mode}  platform:{self.platform}')

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
