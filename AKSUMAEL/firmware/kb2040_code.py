# ╔══════════════════════════════════════════════════════════════╗
# ║  AKSUMAEL KB2040 Firmware  —  CircuitPython HID Bridge       ║
# ║                                                              ║
# ║  Receives UART packets from robocar-hub FTDI and emits       ║
# ║  USB HID keyboard + mouse events to the HP machine.         ║
# ║                                                              ║
# ║  UART: TX=GP0, RX=GP1, 115200 baud (UART0)                  ║
# ║  USB:  standard HID composite (keyboard + mouse)             ║
# ║                                                              ║
# ║  Packet format:                                              ║
# ║    [0xAA] [0xBB] [TYPE] [LEN] [DATA...] [CKSUM]             ║
# ║    CKSUM = (TYPE + LEN + sum(DATA)) & 0xFF                   ║
# ║                                                              ║
# ║  Types:                                                      ║
# ║    0x01  Keyboard  [mod, 0x00, k1..k6]          8 bytes     ║
# ║    0x02  Mouse rel [btn, dx+128, dy+128, whl+128] 4 bytes   ║
# ║    0x03  Mouse abs [btn, x_hi, x_lo, y_hi, y_lo]  5 bytes  ║
# ║    0xFF  Release all                              0 bytes    ║
# ║                                                              ║
# ║  Install: copy this file to CIRCUITPY as code.py            ║
# ╚══════════════════════════════════════════════════════════════╝

import usb_hid
import busio
import board
import supervisor
import time

# ── UART ──────────────────────────────────────────────────────
uart = busio.UART(board.TX, board.RX, baudrate=115200, timeout=0)

# ── HID devices ───────────────────────────────────────────────
keyboard = None
mouse    = None
gamepad  = None

for dev in usb_hid.devices:
    if dev.usage == 0x06 and dev.usage_page == 0x01:
        keyboard = dev   # Generic Desktop / Keyboard
    if dev.usage == 0x02 and dev.usage_page == 0x01:
        mouse = dev      # Generic Desktop / Mouse
    if dev.usage == 0x05 and dev.usage_page == 0x01:
        gamepad = dev    # Generic Desktop / Gamepad

# ── Packet parser state ───────────────────────────────────────
HEADER_A = 0xAA
HEADER_B = 0xBB
TYPE_KB      = 0x01
TYPE_MOUSE_R = 0x02
TYPE_MOUSE_A = 0x03
TYPE_GAMEPAD = 0x04
TYPE_RELEASE = 0xFF

STATE_WAIT_AA  = 0
STATE_WAIT_BB  = 1
STATE_TYPE     = 2
STATE_LEN      = 3
STATE_DATA     = 4
STATE_CKSUM    = 5

state    = STATE_WAIT_AA
pkt_type = 0
pkt_len  = 0
pkt_data = []
buf      = bytearray(256)


def send_keyboard(mod, keys):
    """Send a keyboard HID report. keys = list of up to 6 HID keycodes."""
    if keyboard is None:
        return
    report = bytearray(8)
    report[0] = mod & 0xFF
    report[1] = 0x00
    for i, k in enumerate(keys[:6]):
        report[2 + i] = k & 0xFF
    keyboard.send_report(report)


def send_mouse_relative(buttons, dx, dy, wheel=0):
    """Send a relative mouse movement HID report."""
    if mouse is None:
        return
    # Standard 4-byte relative mouse report: [buttons, dx, dy, wheel]
    # dx/dy are signed int8 (-127 to +127)
    dx    = max(-127, min(127, dx))
    dy    = max(-127, min(127, dy))
    wheel = max(-127, min(127, wheel))
    report = bytearray(4)
    report[0] = buttons & 0x07   # buttons: bit0=left, bit1=right, bit2=middle
    report[1] = dx    & 0xFF     # signed byte — Python bytearray wraps correctly
    report[2] = dy    & 0xFF
    report[3] = wheel & 0xFF
    mouse.send_report(report)


def release_all():
    """Release all keys and mouse buttons."""
    if keyboard is not None:
        keyboard.send_report(bytearray(8))
    if mouse is not None:
        mouse.send_report(bytearray(4))


def handle_packet(ptype, data):
    """Dispatch a fully-parsed packet to the appropriate HID call."""
    if ptype == TYPE_RELEASE:
        release_all()

    elif ptype == TYPE_KB:
        if len(data) >= 8:
            mod  = data[0]
            keys = [data[i] for i in range(2, 8) if data[i] != 0]
            send_keyboard(mod, keys)
            time.sleep(0.020)         # brief hold
            send_keyboard(0, [])      # release

    elif ptype == TYPE_MOUSE_R:
        if len(data) >= 4:
            buttons = data[0]
            dx      = data[1] - 128   # un-bias: [0..255] → [-128..127]
            dy      = data[2] - 128
            wheel   = data[3] - 128
            send_mouse_relative(buttons, dx, dy, wheel)
            if buttons:
                time.sleep(0.050)     # brief hold for clicks
                send_mouse_relative(0, 0, 0, 0)   # release buttons

    elif ptype == TYPE_MOUSE_A:
        # Absolute mouse — convert 16-bit big-endian x/y to report
        if len(data) >= 5 and mouse is not None:
            buttons = data[0]
            x = (data[1] << 8) | data[2]
            y = (data[3] << 8) | data[4]
            dx = (x - 32768) >> 8
            dy = (y - 32768) >> 8
            send_mouse_relative(buttons, dx, dy)

    elif ptype == TYPE_GAMEPAD:
        # Gamepad: [lx+128, ly+128, rx+128, ry+128, btn_lo, btn_hi, lt, rt, guide]
        if len(data) >= 8 and gamepad is not None:
            lx = data[0] - 128
            ly = data[1] - 128
            rx = data[2] - 128
            ry = data[3] - 128
            btn_lo = data[4]
            btn_hi = data[5]
            # lt    = data[6]  # analog triggers — deferred
            # rt    = data[7]
            guide  = data[8] if len(data) >= 9 else 0

            buttons = btn_lo | (btn_hi << 8) | (guide << 16)

            report = bytearray(9)
            # Axes: lx, ly, rx, ry as signed bytes at offsets 0-3
            report[0] = lx & 0xFF
            report[1] = ly & 0xFF
            report[2] = rx & 0xFF
            report[3] = ry & 0xFF
            # Buttons: 16-bit + guide in bytes 4-6
            for i in range(16):
                if buttons & (1 << i):
                    report[4 + i // 8] |= (1 << (i % 8))
            if guide:
                report[6] |= 0x01
            gamepad.send_report(report)


# ── Main loop ─────────────────────────────────────────────────
print("AKSUMAEL KB2040 firmware ready")

while True:
    data = uart.read(64)
    if not data:
        continue

    for byte in data:
        if state == STATE_WAIT_AA:
            if byte == HEADER_A:
                state = STATE_WAIT_BB

        elif state == STATE_WAIT_BB:
            if byte == HEADER_B:
                state = STATE_TYPE
            elif byte == HEADER_A:
                state = STATE_WAIT_BB   # stay: could be AA AA BB...
            else:
                state = STATE_WAIT_AA

        elif state == STATE_TYPE:
            pkt_type = byte
            state = STATE_LEN

        elif state == STATE_LEN:
            pkt_len  = byte
            pkt_data = []
            state = STATE_DATA if pkt_len > 0 else STATE_CKSUM

        elif state == STATE_DATA:
            pkt_data.append(byte)
            if len(pkt_data) >= pkt_len:
                state = STATE_CKSUM

        elif state == STATE_CKSUM:
            expected = (pkt_type + pkt_len + sum(pkt_data)) & 0xFF
            if byte == expected:
                handle_packet(pkt_type, pkt_data)
            # else: silently drop malformed packet
            state = STATE_WAIT_AA
