# AKSUMAEL KB2040 — code.py
#
# Two independent input channels drive the same HID outputs:
#
#   1. UART (FTDI USB-TTL, pins D0/D1) — binary packet protocol
#      Used by AKSUMAEL planner (kb2040_packer.py) for AI bot control.
#      Packet format: [0xAA][0xBB][TYPE][LEN][DATA...][SUM]
#
#   2. USB CDC data endpoint — ASCII line protocol
#      Used by pad_bridge.py for Xbox controller passthrough.
#      Commands: D<KEY>  U<KEY>  M<dx>,<dy>  P<L|R|M>  X<L|R|M>  S<n>  R
#
# Both channels share kbd/mouse. When the human is in control (pad_bridge
# active) the AKSUMAEL planner sends nothing — the Start button toggles
# HUMAN/AI mode in human_assist.py, so there's no logical conflict.
# A 250 ms CDC watchdog releases all CDC-held keys if the bridge goes quiet.

import board
import busio
import microcontroller
import time
import usb_hid
import usb_cdc
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode   import Keycode
from adafruit_hid.mouse     import Mouse

# ── HID devices ───────────────────────────────────────────────
kbd   = Keyboard(usb_hid.devices)
mouse = Mouse(usb_hid.devices)

gamepad_dev = None
for _dev in usb_hid.devices:
    if _dev.usage_page == 0x01 and _dev.usage == 0x05:
        gamepad_dev = _dev
        break

mouse_abs = None
for _dev in usb_hid.devices:
    if _dev.usage_page == 0x01 and _dev.usage == 0x01:
        mouse_abs = _dev
        break

# ── UART (FTDI adapter) ────────────────────────────────────────
uart = busio.UART(board.D0, board.D1, baudrate=115200, timeout=0)

# ── USB CDC data endpoint (pad_bridge) ────────────────────────
cdc = usb_cdc.data   # None if boot.py didn't enable it

# ── UART binary parser state ───────────────────────────────────
PKT_TYPE = {0x01, 0x02, 0x03, 0x04, 0x05, 0xFF}
LEN_MAP  = {0x01: 8, 0x02: 4, 0x03: 5, 0x04: 7, 0x05: 0, 0xFF: 0}
uart_buf = bytearray()
ABS_MAX  = 32767


def checksum(t, l, d):
    return (t + l + sum(d)) & 0xFF

def signed(b):
    return b - 128

# ── UART packet handlers ───────────────────────────────────────

def handle_keyboard(data):
    mod  = data[0]
    keys = [k for k in data[2:8] if k != 0]
    try:
        kbd.release_all()
        if mod & 0x01: kbd.press(Keycode.LEFT_CONTROL)
        if mod & 0x02: kbd.press(Keycode.LEFT_SHIFT)
        if mod & 0x04: kbd.press(Keycode.LEFT_ALT)
        if mod & 0x08: kbd.press(Keycode.LEFT_GUI)
        if mod & 0x10: kbd.press(Keycode.RIGHT_CONTROL)
        if mod & 0x20: kbd.press(Keycode.RIGHT_SHIFT)
        if mod & 0x40: kbd.press(Keycode.RIGHT_ALT)
        if mod & 0x80: kbd.press(Keycode.RIGHT_GUI)
        for k in keys:
            if k: kbd.press(k)
    except Exception:
        kbd.release_all()


def handle_mouse_rel(data):
    buttons = data[0]
    dx = signed(data[1]); dy = signed(data[2]); wheel = signed(data[3])
    try:
        if dx or dy: mouse.move(dx, dy, wheel)
        if buttons & 0x01: mouse.press(Mouse.LEFT_BUTTON)
        else: mouse.release(Mouse.LEFT_BUTTON)
        if buttons & 0x02: mouse.press(Mouse.RIGHT_BUTTON)
        else: mouse.release(Mouse.RIGHT_BUTTON)
        if buttons & 0x04: mouse.press(Mouse.MIDDLE_BUTTON)
        else: mouse.release(Mouse.MIDDLE_BUTTON)
    except Exception:
        pass


def _send_abs_report(buttons, x, y):
    if mouse_abs is None: return
    x = max(0, min(ABS_MAX, int(x))); y = max(0, min(ABS_MAX, int(y)))
    r = bytearray(5)
    r[0] = buttons & 0x03
    r[1] = x & 0xFF;  r[2] = (x >> 8) & 0xFF
    r[3] = y & 0xFF;  r[4] = (y >> 8) & 0xFF
    try: mouse_abs.send_report(r)
    except Exception: pass


def handle_mouse_abs(data):
    buttons = data[0]
    x = (data[1] << 8) | data[2]
    y = (data[3] << 8) | data[4]
    _send_abs_report(buttons, x, y)


def gamepad_send(buttons, x, y, z, rx_val):
    if gamepad_dev is None: return
    r = bytearray(7)
    r[0] = buttons & 0xFF
    r[1] = (buttons >> 8) & 0xFF
    r[2] = (buttons >> 16) & 0x01
    r[3] = x & 0xFF; r[4] = y & 0xFF
    r[5] = z & 0xFF; r[6] = rx_val & 0xFF
    try: gamepad_dev.send_report(r)
    except Exception: pass


def handle_gamepad(data):
    lx = signed(data[0]); ly = signed(data[1])
    rx = signed(data[2]); ry = signed(data[3])
    buttons = data[4] | (data[5] << 8) | (data[6] << 16)
    gamepad_send(buttons, lx, ly, rx, ry)


def uart_release_all():
    try:
        kbd.release_all()
        mouse.release(Mouse.LEFT_BUTTON)
        mouse.release(Mouse.RIGHT_BUTTON)
        mouse.release(Mouse.MIDDLE_BUTTON)
        gamepad_send(0, 0, 0, 0, 0)
    except Exception:
        pass


# ── CDC ASCII protocol (pad_bridge) ───────────────────────────

KEYMAP = {
    "W": Keycode.W, "A": Keycode.A, "S": Keycode.S, "D": Keycode.D,
    "Q": Keycode.Q, "E": Keycode.E, "T": Keycode.T,
    "SPACE": Keycode.SPACE, "LSHIFT": Keycode.LEFT_SHIFT,
    "LCTRL": Keycode.LEFT_CONTROL, "TAB": Keycode.TAB,
    "ESCAPE": Keycode.ESCAPE,
    "F1": Keycode.F1, "F3": Keycode.F3, "F5": Keycode.F5,
    "1": Keycode.ONE, "2": Keycode.TWO, "3": Keycode.THREE,
    "4": Keycode.FOUR, "5": Keycode.FIVE, "6": Keycode.SIX,
    "7": Keycode.SEVEN, "8": Keycode.EIGHT, "9": Keycode.NINE,
}
BTN = {"L": Mouse.LEFT_BUTTON, "R": Mouse.RIGHT_BUTTON, "M": Mouse.MIDDLE_BUTTON}

cdc_held_keys = set()
cdc_held_btns = set()
cdc_buf = b""
WATCHDOG_S = 0.25
cdc_last_rx = time.monotonic()


def cdc_release_all():
    global cdc_held_keys, cdc_held_btns
    try:
        for k in list(cdc_held_keys):
            kc = KEYMAP.get(k)
            if kc: kbd.release(kc)
        for b in list(cdc_held_btns):
            mouse.release(b)
    except Exception:
        pass
    cdc_held_keys.clear()
    cdc_held_btns.clear()


def cdc_handle(line):
    global cdc_held_keys, cdc_held_btns
    if not line:
        return   # empty = keepalive, feeds watchdog (last_rx updated in loop)
    op  = line[0]
    arg = line[1:]
    if op == "D":
        kc = KEYMAP.get(arg)
        if kc and arg not in cdc_held_keys:
            try: kbd.press(kc)
            except Exception: pass
            cdc_held_keys.add(arg)
    elif op == "U":
        kc = KEYMAP.get(arg)
        if kc:
            try: kbd.release(kc)
            except Exception: pass
            cdc_held_keys.discard(arg)
    elif op == "M":
        try:
            dx, dy = arg.split(",")
            dx, dy = int(dx), int(dy)
            while dx or dy:
                sx = max(-127, min(127, dx))
                sy = max(-127, min(127, dy))
                mouse.move(x=sx, y=sy)
                dx -= sx; dy -= sy
        except Exception:
            pass
    elif op == "P":
        b = BTN.get(arg)
        if b and b not in cdc_held_btns:
            try: mouse.press(b)
            except Exception: pass
            cdc_held_btns.add(b)
    elif op == "X":
        b = BTN.get(arg)
        if b:
            try: mouse.release(b)
            except Exception: pass
            cdc_held_btns.discard(b)
    elif op == "S":
        try: mouse.move(wheel=max(-127, min(127, int(arg))))
        except Exception: pass
    elif op == "R":
        cdc_release_all()


# ── Main loop ──────────────────────────────────────────────────
print("AKSUMAEL KB2040 ready (UART binary + CDC ASCII)")

while True:
    now = time.monotonic()

    # ── UART binary packets ────────────────────────────────────
    chunk = uart.read(64)
    if chunk:
        uart_buf.extend(chunk)

    while len(uart_buf) >= 2:
        idx = -1
        for i in range(len(uart_buf) - 1):
            if uart_buf[i] == 0xAA and uart_buf[i+1] == 0xBB:
                idx = i; break
        if idx < 0:
            uart_buf = uart_buf[-1:]; break
        if idx > 0:
            uart_buf = uart_buf[idx:]
        if len(uart_buf) < 4: break

        pkt_type = uart_buf[2]
        pkt_len  = uart_buf[3]
        if pkt_type not in PKT_TYPE:
            uart_buf = uart_buf[1:]; continue

        total = 2 + 1 + 1 + pkt_len + 1
        if len(uart_buf) < total: break

        data    = uart_buf[4:4 + pkt_len]
        got_sum = uart_buf[4 + pkt_len]
        if got_sum != checksum(pkt_type, pkt_len, data):
            uart_buf = uart_buf[1:]; continue

        if   pkt_type == 0x01 and len(data) == 8: handle_keyboard(data)
        elif pkt_type == 0x02 and len(data) == 4: handle_mouse_rel(data)
        elif pkt_type == 0x03 and len(data) == 5: handle_mouse_abs(data)
        elif pkt_type == 0x04 and len(data) == 7: handle_gamepad(data)
        elif pkt_type == 0x05:
            uart_release_all()
            microcontroller.reset()
        elif pkt_type == 0xFF:
            uart_release_all()

        uart_buf = uart_buf[total:]

    # ── USB CDC ASCII commands (pad_bridge) ────────────────────
    if cdc and cdc.in_waiting:
        global cdc_buf
        cdc_buf += cdc.read(cdc.in_waiting)
        cdc_last_rx = now
        while b"\n" in cdc_buf:
            line, cdc_buf = cdc_buf.split(b"\n", 1)
            try:
                cdc_handle(line.strip().decode())
            except Exception:
                pass
    elif (cdc_held_keys or cdc_held_btns) and now - cdc_last_rx > WATCHDOG_S:
        cdc_release_all()   # bridge died mid-hold — don't walk into lava

    time.sleep(0.001)
