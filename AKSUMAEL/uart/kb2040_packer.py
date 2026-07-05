# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — KB2040 UART Packer                 ║
# ║  Pi-side packet builder for the KB2040 HID firmware  ║
# ╚══════════════════════════════════════════════════════╝
#
# Packet format (matches rp2040/code.py parser):
#   [0xAA] [0xBB] [TYPE] [LEN] [DATA...] [SUM]
#   SUM = (TYPE + LEN + sum(DATA)) & 0xFF
#
# Types:
#   0x01  Keyboard  [modifier, 0x00, k1..k6]        8 bytes
#   0x02  Mouse rel [buttons, dx+128, dy+128, whl+128]  4 bytes
#   0x03  Mouse abs [buttons, x_hi, x_lo, y_hi, y_lo]   5 bytes
#   0x04  Gamepad   [lx+128, ly+128, rx+128, ry+128,
#                   btn_lo, btn_hi, lt, rt]           8 bytes
#   0xFF  Release all                                 0 bytes

import time
import threading
import config

TYPE_KB      = 0x01
TYPE_MOUSE_R = 0x02
TYPE_MOUSE_A = 0x03
TYPE_GAMEPAD = 0x04
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


def pack_mouse_absolute(x_pct: float, y_pct: float,
                        buttons: int = 0) -> bytes:
    """x_pct, y_pct: 0.0–100.0 percent of screen."""
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
    return _frame(TYPE_GAMEPAD, [
        s8_to_u8(lx), s8_to_u8(ly),
        s8_to_u8(rx), s8_to_u8(ry),
        buttons & 0xFF, (buttons >> 8) & 0xFF,
        u8(lt), u8(rt),
        1 if guide else 0,
    ])


def pack_release_all() -> bytes:
    return _frame(TYPE_RELEASE, [])


def key_to_hid(key_name: str) -> tuple:
    k = str(key_name).lower().strip()
    return KEY_MAP.get(k, 0x00), MOD_MAP.get(k, 0x00)


def action_dict_to_packets(action_dict: dict,
                            platform: str = 'pc') -> list:
    packets = []
    key     = action_dict.get('key')
    click   = action_dict.get('click')
    gamepad = action_dict.get('gamepad')

    # Keyboard
    if key and str(key).lower() not in ('null', 'none', 'wait', ''):
        hid, mod = key_to_hid(str(key))
        if hid:
            packets.append(pack_keyboard(keys=[hid], modifiers=mod))
            packets.append(pack_keyboard())   # release

    # Absolute click
    if click and click not in ('null', None):
        try:
            packets.extend(pack_mouse_click_at(float(click[0]),
                                               float(click[1])))
        except (TypeError, IndexError, ValueError):
            pass

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
    def __init__(self, port=None, baud=None):
        self.port  = port or config.UART_PORT
        self.baud  = baud or config.UART_BAUD
        self._ser  = None
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        try:
            import serial as pyserial
            self._ser = pyserial.Serial(self.port, self.baud, timeout=0.1)
            time.sleep(0.15)
            # Send release-all to clear any stale HID state
            self._ser.write(pack_release_all())
            print(f'[KB2040] connected on {self.port} @ {self.baud}')
        except ImportError:
            print('[KB2040] pyserial not installed')
        except Exception as e:
            print(f'[KB2040] UART open failed: {e}')
            self._ser = None

    def send(self, pkt: bytes) -> bool:
        if not self._ser:
            return False
        try:
            with self._lock:
                self._ser.write(pkt)
            return True
        except Exception as e:
            print(f'[KB2040] send error: {e}')
            return False

    def send_action(self, action_dict: dict,
                    platform: str = 'pc',
                    delay_ms: int = 20) -> bool:
        ok = True
        for pkt in action_dict_to_packets(action_dict, platform):
            ok &= self.send(pkt)
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        return ok

    def release_all(self):
        self.send(pack_release_all())

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
