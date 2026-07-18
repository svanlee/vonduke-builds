# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — KB2040 UART Packer                 ║
# ║  Host-side packet builder for the KB2040 HID firmware ║
# ╚══════════════════════════════════════════════════════╝
#
# Packet format (matches rp2040/code.py parser):
#   [0xAA] [0xBB] [TYPE] [LEN] [DATA...] [SUM]
#   SUM = (TYPE + LEN + sum(DATA)) & 0xFF
#
# Types:
#   0x01  Keyboard  [modifier, 0x00, k1..k6]        8 bytes
#   0x02  Mouse rel [buttons, dx+128, dy+128, whl+128]  4 bytes  -> relative MOUSE device (camera look)
#   0x03  Mouse abs [buttons, x_hi, x_lo, y_hi, y_lo]   5 bytes  -> absolute pointer device (desktop clicks)
#   0x04  Gamepad   [lx+128, ly+128, rx+128, ry+128,
#                   btn_lo, btn_hi, lt, rt]           8 bytes
#   0x05  Reset                                       0 bytes  -> microcontroller.reset() (forces USB HID re-enumeration)
#   0xFF  Release all                                 0 bytes

import time
import threading
import config

TYPE_KB      = 0x01
TYPE_MOUSE_R = 0x02
TYPE_MOUSE_A = 0x03
TYPE_GAMEPAD = 0x04
TYPE_RESET   = 0x05
TYPE_RELEASE = 0xFF

HEADER = bytes([0xAA, 0xBB])

# ── HID Keycodes (same as ch9329_packer) ─────────────────────
KEY_MAP = {
    'a':0x04,'b':0x05,'c':0x06,'d':0x07,'e':0x08,'f':0x09,
    'g':0x0A,'h':0x0B,'i':0x0C,'j':0x0D,'k':0x0E,'l':0x0F,
    'm':0x10,'n':0x11,'o':0x12,'p':0x13,'q':0x14,'r':0x15,
    's':0x16,'t':0x17,'u':0x18,'v':0x19,'w':0x1A,'x':0x1B,
    'y':0x1C,'z':0x1D,
    '1':0x1E,'2':0x1F,'3':0x20,'4':0x21,'5':0x22,
    '6':0x23,'7':0x24,'8':0x25,'9':0x26,'0':0x27,
    'enter':0x28,'return':0x28,'escape':0x29,'esc':0x29,
    'backspace':0x2A,'tab':0x2B,'space':0x2C,' ':0x2C,
    'f1':0x3A,'f2':0x3B,'f3':0x3C,'f4':0x3D,'f5':0x3E,
    'f6':0x3F,'f7':0x40,'f8':0x41,'f9':0x42,'f10':0x43,
    'f11':0x44,'f12':0x45,
    'up':0x52,'down':0x51,'left':0x50,'right':0x4F,
    'delete':0x4C,'home':0x4A,'end':0x4D,
    'pageup':0x4B,'pagedown':0x4E,
    '-':0x2D,'=':0x2E,'[':0x2F,']':0x30,
    ';':0x33,"'":0x34,'`':0x35,'\\':0x31,',':0x36,
    '.':0x37,'/':0x38,
}
MOD_MAP = {
    'lctrl':0x01,'lshift':0x02,'lalt':0x04,'lmeta':0x08,
    'rctrl':0x10,'rshift':0x20,'ralt':0x40,'rmeta':0x80,
    'ctrl':0x01,'shift':0x02,'alt':0x04,
}


def _frame(pkt_type: int, data: list) -> bytes:
    body = list(HEADER) + [pkt_type, len(data)] + list(data)
    cksum = (pkt_type + len(data) + sum(data)) & 0xFF
    return bytes(body + [cksum])


def u8(v: int) -> int:
    """Clamp and convert to unsigned byte."""
    return max(0, min(255, v)) & 0xFF


def s8_to_u8(v: int) -> int:
    """Signed -128..127 → offset-128 unsigned byte."""
    return max(0, min(255, v + 128))


# ── Packet builders ───────────────────────────────────────────
def pack_keyboard(keys: list = None, modifiers: int = 0x00) -> bytes:
    keys = (keys or [])[:6]
    keys += [0x00] * (6 - len(keys))
    return _frame(TYPE_KB, [modifiers, 0x00] + keys)


def pack_mouse_relative(dx: int = 0, dy: int = 0,
                        buttons: int = 0, wheel: int = 0) -> bytes:
    return _frame(TYPE_MOUSE_R, [
        u8(buttons),
        s8_to_u8(dx),
        s8_to_u8(dy),
        s8_to_u8(wheel),
    ])


def pack_mouse_move(dx: int = 0, dy: int = 0) -> bytes:
    """Relative mouse movement packet (no buttons/wheel) — camera look."""
    return pack_mouse_relative(dx=dx, dy=dy)


def pack_mouse_absolute(x_pct: float, y_pct: float,
                        buttons: int = 0) -> bytes:
    """x_pct, y_pct: 0.0-100.0 percent of screen, scaled here to the
    0-32767 HID logical range the firmware's absolute pointer device
    expects (see rp2040/boot.py's ABS_POINTER_REPORT_DESCRIPTOR /
    rp2040/code.py's handle_mouse_abs()) — the firmware forwards these
    x/y values as-is, so this is the only place the scaling happens."""
    v = 32767
    x = int(max(0.0, min(100.0, x_pct)) / 100.0 * v)
    y = int(max(0.0, min(100.0, y_pct)) / 100.0 * v)
    return _frame(TYPE_MOUSE_A, [
        u8(buttons),
        (x >> 8) & 0xFF, x & 0xFF,
        (y >> 8) & 0xFF, y & 0xFF,
    ])


def pack_mouse_click_at(x_pct: float, y_pct: float,
                        button: int = 0x01) -> list:
    return [
        pack_mouse_absolute(x_pct, y_pct, buttons=button),
        pack_mouse_absolute(x_pct, y_pct, buttons=0),
    ]


def pack_gamepad(lx: int = 0, ly: int = 0,
                 rx: int = 0, ry: int = 0,
                 buttons: int = 0,
                 lt: int = 0, rt: int = 0,
                 guide: bool = False) -> bytes:
    # No dedicated wire byte for guide — TYPE_GAMEPAD is a fixed 8 bytes
    # (rp2040/code.py checks len(data) == 8 and silently drops anything
    # else), so guide rides in bit 15 of the existing 16-bit buttons field
    # (Button 16 in boot.py's GAMEPAD_REPORT_DESCRIPTOR) instead of a 9th
    # byte that the firmware would never dispatch.
    if guide:
        buttons |= 0x8000
    return _frame(TYPE_GAMEPAD, [
        s8_to_u8(lx), s8_to_u8(ly),
        s8_to_u8(rx), s8_to_u8(ry),
        buttons & 0xFF, (buttons >> 8) & 0xFF,
        u8(lt), u8(rt),
    ])


def pack_release_all() -> bytes:
    return _frame(TYPE_RELEASE, [])


def pack_reset() -> bytes:
    return _frame(TYPE_RESET, [])


def key_to_hid(key_name: str) -> tuple:
    k = str(key_name).lower().strip()
    if '+' in k:
        # Combo keys, e.g. "ctrl+w" for sprint — modifier(s) + one regular key
        hid, mod = 0x00, 0x00
        for part in (p.strip() for p in k.split('+')):
            if part in MOD_MAP:
                mod |= MOD_MAP[part]
            elif part in KEY_MAP:
                hid = KEY_MAP[part]
        return hid, mod
    return KEY_MAP.get(k, 0x00), MOD_MAP.get(k, 0x00)


def action_dict_to_packets(action_dict: dict,
                            platform: str = 'pc',
                            held_buttons: int = 0) -> list:
    packets = []
    key     = action_dict.get('key')
    click   = action_dict.get('click')
    gamepad = action_dict.get('gamepad')
    look    = action_dict.get('look')

    # Keyboard
    if key and str(key).lower() not in ('null', 'none', 'wait', ''):
        hid, mod = key_to_hid(str(key))
        if hid or mod:   # mod-only (e.g. plain "ctrl") is still a valid press
            packets.append(pack_keyboard(keys=[hid] if hid else [], modifiers=mod))
            packets.append(pack_keyboard())   # release

    # Absolute click
    if click and click not in ('null', None):
        try:
            button = 0x02 if action_dict.get('button', 'left') == 'right' else 0x01
            packets.extend(pack_mouse_click_at(float(click[0]),
                                               float(click[1]),
                                               button=button))
        except (TypeError, IndexError, ValueError):
            pass

    # Relative mouse button press (goes through TYPE_MOUSE_R — captured by game)
    # Use 'mouse_button': 'left' or 'right' for in-game clicks (mining, attacking)
    # instead of 'click': [...] which uses TYPE_MOUSE_A (not game-captured).
    mouse_btn = action_dict.get('mouse_button')
    if mouse_btn in ('left', 'right'):
        _btn = 1 if mouse_btn == 'left' else 2
        packets.append(pack_mouse_relative(dx=0, dy=0, buttons=_btn))
        packets.append(pack_mouse_relative(dx=0, dy=0, buttons=0))

    # Held mouse button — press-only or release-only, no auto-release.
    # 'mouse_button' above always sends press+release as one instant click,
    # which resets Minecraft's block-break progress every time it fires.
    # Use 'mouse_hold': 'down' once to start a continuous hold and 'up'
    # once to end it — the button stays physically down on everything
    # in between (2026-07-17).
    mouse_hold = action_dict.get('mouse_hold')
    if mouse_hold in ('down', 'up'):
        _hold_btn = 1 if action_dict.get('mouse_button_name', 'left') == 'left' else 2
        packets.append(pack_mouse_relative(
            dx=0, dy=0, buttons=_hold_btn if mouse_hold == 'down' else 0))

    # Mouse look (relative camera pan) — must carry forward any currently
    # held mouse button (held_buttons, tracked by KB2040Serial) instead of
    # hardcoding buttons=0. Every TYPE_MOUSE_R packet is a full state
    # report on the firmware side (rp2040/code.py::handle_mouse_rel), not
    # a delta — a look packet sent mid-hold with buttons=0 would silently
    # release the held click (2026-07-17).
    if look and isinstance(look, dict):
        dx = int(look.get('dx', 0))
        dy = int(look.get('dy', 0))
        if dx != 0 or dy != 0:
            packets.append(pack_mouse_relative(dx=dx, dy=dy, buttons=held_buttons))

    # Gamepad (all platforms — KB2040 supports it natively)
    if gamepad and isinstance(gamepad, dict):
        if any(gamepad.get(k, 0) for k in
               ('lx','ly','rx','ry','lt','rt','buttons','guide')):
            packets.append(pack_gamepad(
                lx=gamepad.get('lx',0), ly=gamepad.get('ly',0),
                rx=gamepad.get('rx',0), ry=gamepad.get('ry',0),
                buttons=gamepad.get('buttons',0),
                lt=gamepad.get('lt',0), rt=gamepad.get('rt',0),
                guide=gamepad.get('guide', False),
            ))

    return packets


# ── KB2040Serial (thread-safe) ────────────────────────────────
class KB2040Serial:
    RECONNECT_INTERVAL = 30  # seconds between reconnect attempts

    def __init__(self, port=None, baud=None):
        self.port  = port or config.UART_PORT
        self.baud  = baud or config.UART_BAUD
        self._ser  = None
        self._lock = threading.Lock()
        self._last_reconnect_attempt = 0.0
        # Currently-held relative-mouse buttons bitmask (bit 0=left,
        # bit 1=right) — see action_dict_to_packets' held_buttons param.
        # Every TYPE_MOUSE_R packet is a full state report, so any 'look'
        # packet sent while a 'mouse_hold' is active must echo this back
        # or the firmware will release the button (2026-07-17).
        self._held_mouse_buttons = 0
        self._connect()

    def _connect(self, quiet=False):
        try:
            import serial as pyserial
            self._ser = pyserial.Serial(self.port, self.baud, timeout=0.1)
            time.sleep(0.15)
            # Send release-all to clear any stale HID state
            self._ser.write(pack_release_all())
            self._held_mouse_buttons = 0
            if not quiet:
                print(f'[KB2040] connected on {self.port} @ {self.baud}')
        except ImportError:
            if not quiet:
                print('[KB2040] pyserial not installed')
            self._ser = None
        except Exception as e:
            if not quiet:
                print(f'[KB2040] UART open failed: {e}')
            # pyserial.Serial() may have already opened the fd before the
            # write() above raised (e.g. device yanked mid-connect) — close
            # it explicitly instead of relying on GC to release the handle.
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None
        self._last_reconnect_attempt = time.time()

    def try_reconnect(self) -> bool:
        """Rate-limited reconnect attempt (at most once per RECONNECT_INTERVAL).
        Returns True if connected (already, or freshly reconnected)."""
        if self.is_connected:
            return True
        if time.time() - self._last_reconnect_attempt < self.RECONNECT_INTERVAL:
            return False
        self._connect(quiet=True)
        if self.is_connected:
            print(f'[KB2040] reconnected on {self.port}')
            return True
        return False

    def send(self, pkt: bytes) -> bool:
        if not self._ser:
            self.try_reconnect()
        if not self._ser:
            return False
        try:
            with self._lock:
                self._ser.write(pkt)
            return True
        except Exception as e:
            print(f'[KB2040] send error: {e}')
            if isinstance(e, OSError):
                # Cable likely unplugged — drop the handle and let the
                # next send()/try_reconnect() re-open it once it's back.
                self._ser = None
                self.try_reconnect()
            return False

    def send_action(self, action_dict: dict,
                    platform: str = 'pc',
                    delay_ms: int = 20) -> bool:
        mouse_hold = action_dict.get('mouse_hold')
        if mouse_hold in ('down', 'up'):
            _btn = 1 if action_dict.get('mouse_button_name', 'left') == 'left' else 2
            if mouse_hold == 'down':
                self._held_mouse_buttons |= _btn
            else:
                self._held_mouse_buttons &= ~_btn

        ok = True
        for pkt in action_dict_to_packets(action_dict, platform,
                                          held_buttons=self._held_mouse_buttons):
            ok &= self.send(pkt)
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        return ok

    def release_all(self):
        self._held_mouse_buttons = 0
        self.send(pack_release_all())

    def reset_device(self):
        """Force the KB2040 to microcontroller.reset(), which re-enumerates
        its USB HID devices on the target PC — recovery path for when the
        relative-mouse channel goes dead without any USB disconnect visible
        on this host's side (2026-07-17). Requires rp2040/code.py to have
        been flashed with the 0x05 packet handler — older firmware silently
        ignores this packet (unrecognized type), so it's a no-op until then.
        The reset drops /dev/ttyUSB0 briefly; existing try_reconnect() polling
        picks the link back up once the board finishes rebooting.
        Sends release-all first — 0x05 is a no-op on old firmware, so
        without this a held button would otherwise stay physically down
        with no way to clear it."""
        self._held_mouse_buttons = 0
        self.send(pack_release_all())
        self.send(pack_reset())

    def close(self):
        self.release_all()
        if self._ser:
            self._ser.close()

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open


# ── Self test ─────────────────────────────────────────────────
if __name__ == '__main__':
    print('KB2040 Packer self-test')
    print()

    kb = pack_keyboard(keys=[0x1A])
    print(f'Keyboard  (W)        type=01: {kb.hex().upper()}')
    assert kb[2] == TYPE_KB

    mr = pack_mouse_relative(dx=10, dy=-5)
    print(f'Mouse rel            type=02: {mr.hex().upper()}')
    assert mr[2] == TYPE_MOUSE_R
    assert mr[5] == 10 + 128   # dx offset
    assert mr[6] == -5 + 128   # dy offset

    ma = pack_mouse_absolute(50.0, 50.0, buttons=1)
    print(f'Mouse abs 50%        type=03: {ma.hex().upper()}')
    assert ma[2] == TYPE_MOUSE_A

    gp = pack_gamepad(lx=64, buttons=0x0001)
    print(f'Gamepad              type=04: {gp.hex().upper()}')
    assert gp[2] == TYPE_GAMEPAD

    rel = pack_release_all()
    print(f'Release all          type=FF: {rel.hex().upper()}')
    assert rel[2] == TYPE_RELEASE

    # Full action dict round-trip
    ad = {'key': 'w', 'click': [75.0, 30.0],
          'gamepad': {'lx': 50, 'ly': 0, 'buttons': 1}}
    pkts = action_dict_to_packets(ad)
    print(f'\nAction dict → {len(pkts)} packets:')
    for p in pkts:
        print(f'  type={p[2]:02X} len={p[3]} → {p.hex()}')

    print('\nAll tests passed.')
