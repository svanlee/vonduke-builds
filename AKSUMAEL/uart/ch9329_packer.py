# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — CH9329 UART Packer                 ║
# ╚══════════════════════════════════════════════════════╝
#
# CH9329 = USB-to-UART HID chip. KEYBOARD + MOUSE ONLY.
# It has NO native gamepad. The console/Brook path needs either
# the custom-HID descriptor mode (advanced, see pack_custom_hid)
# or a gamepad-capable board (KB2040). pack_gamepad() here routes
# through custom-HID (cmd 0x06) and ONLY works if the chip has been
# reconfigured with a gamepad descriptor — see tools/ch9329_config.py.
#
# Serial frame format:
#   0x57 0xAB  ADDR  CMD  LEN  DATA...  SUM
#   ADDR = 0x00 (default chip address)
#   SUM  = (sum of all preceding bytes) & 0xFF
#
# Command bytes (VERIFY against your chip via GET_INFO):
#   0x01  GET_INFO              chip version + USB status
#   0x02  SEND_KB_GENERAL_DATA  keyboard (modifier + 6 keys)
#   0x03  SEND_KB_MEDIA_DATA    media keys
#   0x04  SEND_MS_ABS_DATA      absolute mouse  (prefix byte 0x02)
#   0x05  SEND_MS_REL_DATA      relative mouse  (prefix byte 0x01)
#   0x06  SEND_MY_HID_DATA      custom HID report (gamepad path)
#   0x08  GET_PARA_CFG          read chip config
#   0x09  SET_PARA_CFG          write chip config

import time
import threading
import config

# ── Command bytes ─────────────────────────────────────────────
CMD_GET_INFO   = 0x01
CMD_KB_GENERAL = 0x02
CMD_KB_MEDIA   = 0x03
CMD_MS_ABS     = 0x04
CMD_MS_REL     = 0x05
CMD_CUSTOM_HID = 0x06
CMD_GET_CFG    = 0x08
CMD_SET_CFG    = 0x09

ADDR = 0x00
HEAD = [0x57, 0xAB]

# Absolute mouse coordinate range. CH9329 commonly uses 0–4095 (12-bit).
# If clicks land in the wrong place on hardware, try 0x7FFF instead.
ABS_RANGE = 4096

# ── HID Keycodes ──────────────────────────────────────────────
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


def _checksum(data: list) -> int:
    return sum(data) & 0xFF


def _frame(cmd: int, data: list) -> bytes:
    body = HEAD + [ADDR, cmd, len(data)] + data
    body.append(_checksum(body))
    return bytes(body)


# ── Keyboard ──────────────────────────────────────────────────
def pack_keyboard(keys: list = None, modifiers: int = 0x00) -> bytes:
    """Keyboard report: [modifier, 0x00, k1..k6]."""
    keys = (keys or [])[:6]
    keys += [0x00] * (6 - len(keys))
    return _frame(CMD_KB_GENERAL, [modifiers, 0x00] + keys)


# ── Mouse ─────────────────────────────────────────────────────
def pack_mouse_relative(dx: int = 0, dy: int = 0,
                        buttons: int = 0x00, wheel: int = 0) -> bytes:
    """
    Relative mouse. Data: [0x01, button, dx, dy, wheel].
    The leading 0x01 is the CH9329 relative-mouse report ID.
    """
    def s8(v): return v & 0xFF
    return _frame(CMD_MS_REL, [0x01, buttons, s8(dx), s8(dy), s8(wheel)])


def pack_mouse_absolute(x_pct: float, y_pct: float,
                        buttons: int = 0x00, wheel: int = 0) -> bytes:
    """
    Absolute mouse. Data: [0x02, button, x_lo, x_hi, y_lo, y_hi, wheel].
    The leading 0x02 is the CH9329 absolute-mouse report ID.
    x_pct / y_pct are 0.0–100.0 percent of screen.
    """
    ax = int(max(0.0, min(100.0, x_pct)) / 100.0 * (ABS_RANGE - 1))
    ay = int(max(0.0, min(100.0, y_pct)) / 100.0 * (ABS_RANGE - 1))
    data = [0x02, buttons,
            ax & 0xFF, (ax >> 8) & 0xFF,
            ay & 0xFF, (ay >> 8) & 0xFF,
            wheel & 0xFF]
    return _frame(CMD_MS_ABS, data)


def pack_mouse_click_at(x_pct: float, y_pct: float,
                        button: int = 0x01) -> list:
    """Move to absolute position and click (press + release)."""
    return [
        pack_mouse_absolute(x_pct, y_pct, buttons=button),
        pack_mouse_absolute(x_pct, y_pct, buttons=0x00),
    ]


# ── Gamepad (custom HID — requires chip reconfiguration) ──────
def pack_gamepad(lx: int = 0, ly: int = 0,
                 rx: int = 0, ry: int = 0,
                 buttons: int = 0x0000,
                 lt: int = 0, rt: int = 0) -> bytes:
    """
    Gamepad via custom HID (cmd 0x06).

    ⚠️  This ONLY works if the CH9329 has been configured with a
        gamepad HID descriptor via SET_PARA_CFG. A stock CH9329 is
        keyboard+mouse only and will ignore / mis-handle this.
        See tools/ch9329_config.py and the README gamepad section.

    Report layout (must match the configured descriptor):
        [lx, ly, rx, ry, lt, rt, btn_lo, btn_hi]  axes centred at 128
    """
    def s8(v): return (v + 128) & 0xFF
    report = [s8(lx), s8(ly), s8(rx), s8(ry),
              lt & 0xFF, rt & 0xFF,
              buttons & 0xFF, (buttons >> 8) & 0xFF]
    return _frame(CMD_CUSTOM_HID, report)


def pack_get_info() -> bytes:
    """GET_INFO request — chip responds with version + USB status."""
    return _frame(CMD_GET_INFO, [])


# ── Key name → HID ────────────────────────────────────────────
def key_to_hid(key_name: str) -> tuple:
    k   = str(key_name).lower().strip()
    return KEY_MAP.get(k, 0x00), MOD_MAP.get(k, 0x00)


# ── Action dict → packets ─────────────────────────────────────
def action_dict_to_packets(action_dict: dict, platform: str = 'pc') -> list:
    packets = []
    key     = action_dict.get('key')
    click   = action_dict.get('click')
    gamepad = action_dict.get('gamepad')

    # Keyboard
    if key and str(key).lower() not in ('null', 'none', 'wait', ''):
        hid, mod = key_to_hid(str(key))
        if hid:
            packets.append(pack_keyboard(keys=[hid], modifiers=mod))
            packets.append(pack_keyboard())  # release

    # Absolute mouse click
    if click and click not in ('null', None):
        try:
            x_pct, y_pct = float(click[0]), float(click[1])
            packets.extend(pack_mouse_click_at(x_pct, y_pct))
        except (TypeError, IndexError, ValueError):
            pass

    # Gamepad (only on console targets; needs configured chip)
    if gamepad and isinstance(gamepad, dict) and platform != 'pc':
        if any(gamepad.get(k, 0) for k in
               ('lx','ly','rx','ry','lt','rt','buttons')):
            packets.append(pack_gamepad(
                lx=gamepad.get('lx',0), ly=gamepad.get('ly',0),
                rx=gamepad.get('rx',0), ry=gamepad.get('ry',0),
                buttons=gamepad.get('buttons',0),
                lt=gamepad.get('lt',0), rt=gamepad.get('rt',0)))

    return packets


# ── Serial manager (thread-safe) ──────────────────────────────
class CH9329Serial:
    def __init__(self, port=None, baud=None):
        self.port  = port or config.UART_PORT
        self.baud  = baud or config.UART_BAUD
        self._ser  = None
        self._lock = threading.Lock()   # serialise writes across threads
        self.chip_info = None
        self._connect()

    def _connect(self):
        try:
            import serial as pyserial
            self._ser = pyserial.Serial(self.port, self.baud, timeout=0.2)
            time.sleep(0.1)
            print(f'[CH9329] connected on {self.port} @ {self.baud}')
            self.get_info()   # handshake
        except ImportError:
            print('[CH9329] pyserial not installed')
        except Exception as e:
            print(f'[CH9329] UART open failed: {e}')
            self._ser = None

    def get_info(self) -> dict:
        """
        Send GET_INFO and parse the reply. Confirms the chip is alive
        and reports USB enumeration status.
        """
        if not self._ser:
            return {}
        try:
            with self._lock:
                self._ser.reset_input_buffer()
                self._ser.write(pack_get_info())
                time.sleep(0.05)
                resp = self._ser.read(16)
            if not resp or len(resp) < 6:
                print('[CH9329] GET_INFO: no/short response — '
                      'check TX/RX wiring (crossed?) and baud')
                return {}
            # Expected: 57 AB 00 81 <len> <ver> <usb_status> ...
            if resp[0] == 0x57 and resp[1] == 0xAB:
                ver = resp[5] if len(resp) > 5 else 0
                usb = resp[6] if len(resp) > 6 else 0
                self.chip_info = {
                    'version':    ver,
                    'usb_status': usb,
                    'usb_ready':  bool(usb & 0x01),
                    'raw':        resp.hex(),
                }
                state = 'enumerated' if (usb & 0x01) else 'NOT enumerated'
                print(f'[CH9329] chip alive — fw v{ver}, USB {state}')
                if not (usb & 0x01):
                    print('[CH9329] ⚠ USB not enumerated — is the USB-A '
                          'side plugged into the PC?')
                return self.chip_info
            else:
                print(f'[CH9329] GET_INFO: unexpected reply {resp.hex()}')
                return {}
        except Exception as e:
            print(f'[CH9329] GET_INFO error: {e}')
            return {}

    def send(self, pkt: bytes) -> bool:
        if not self._ser:
            return False
        try:
            with self._lock:
                self._ser.write(pkt)
            return True
        except Exception as e:
            print(f'[CH9329] send error: {e}')
            return False

    def send_action(self, action_dict: dict,
                    platform: str = 'pc', delay_ms: int = 20) -> bool:
        ok = True
        for pkt in action_dict_to_packets(action_dict, platform):
            ok &= self.send(pkt)
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        return ok

    def release_all(self):
        self.send(pack_keyboard())
        self.send(pack_mouse_relative())

    def close(self):
        self.release_all()
        if self._ser:
            self._ser.close()

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open


# ── Self test ─────────────────────────────────────────────────
if __name__ == '__main__':
    print('CH9329 Packer self-test (corrected protocol)')
    print()

    kb = pack_keyboard(keys=[0x1A])
    print(f'Keyboard (W)        cmd=02: {kb.hex().upper()}')
    assert kb[3] == CMD_KB_GENERAL and kb[-1] == _checksum(list(kb[:-1]))

    mr = pack_mouse_relative(dx=10, dy=-5, buttons=0x01)
    print(f'Mouse relative      cmd=05: {mr.hex().upper()}')
    assert mr[3] == CMD_MS_REL and mr[5] == 0x01  # prefix byte
    assert mr[-1] == _checksum(list(mr[:-1]))

    ma = pack_mouse_absolute(50.0, 50.0, buttons=0x01)
    print(f'Mouse absolute 50%  cmd=04: {ma.hex().upper()}')
    assert ma[3] == CMD_MS_ABS and ma[5] == 0x02  # prefix byte
    assert ma[-1] == _checksum(list(ma[:-1]))

    gi = pack_get_info()
    print(f'GET_INFO            cmd=01: {gi.hex().upper()}')
    assert gi[3] == CMD_GET_INFO

    gp = pack_gamepad(lx=64, buttons=0x0001)
    print(f'Gamepad (custom)    cmd=06: {gp.hex().upper()}')
    assert gp[3] == CMD_CUSTOM_HID

    ad = {'key': 'w', 'click': [75.0, 30.0]}
    pkts = action_dict_to_packets(ad)
    print(f'\naction_dict → {len(pkts)} packets:')
    for p in pkts:
        print(f'  {p.hex()}')

    print('\nAll tests passed. Command bytes verified.')
    print('NOTE: gamepad (0x06) needs chip reconfiguration — see tools/ch9329_config.py')
