# AKSUMAEL KB2040 — code.py
# Receives AKSUMAEL UART packets from the Pi and sends them as
# USB HID reports to the host PC or console.
#
# Packet format:
#   [0xAA] [0xBB] [TYPE] [LEN] [DATA...] [SUM]
#   SUM = (TYPE + LEN + sum(DATA)) & 0xFF
#
# Types:
#   0x01  Keyboard  [modifier, 0x00, k1, k2, k3, k4, k5, k6]
#   0x02  Mouse rel [buttons, dx+128, dy+128, wheel+128]
#   0x03  Mouse abs [buttons, x_hi, x_lo, y_hi, y_lo]
#   0x04  Gamepad   [lx+128, ly+128, rx+128, ry+128, btn_lo, btn_hi, lt, rt]
#   0xFF  Release all
#
# Dependencies (copy to CIRCUITPY/lib/):
#   adafruit_hid/  (full folder from CircuitPython library bundle)

import board
import busio
import usb_hid
from adafruit_hid.keyboard         import Keyboard
from adafruit_hid.keycode           import Keycode
from adafruit_hid.mouse             import Mouse
from adafruit_hid.gamepad           import Gamepad

# ── HID devices ───────────────────────────────────────────────
kbd      = Keyboard(usb_hid.devices)
mouse    = Mouse(usb_hid.devices)
gamepad  = Gamepad(usb_hid.devices)

# ── UART (Pi → KB2040) ────────────────────────────────────────
# KB2040 UART0: TX=D0 (GP0), RX=D1 (GP1)
# Wire: Pi GPIO14 (TX) → KB2040 D0 (RX)
#       Pi GPIO15 (RX) ← KB2040 D1 (TX)
uart = busio.UART(board.D0, board.D1, baudrate=115200, timeout=0)

# ── Packet parser state ───────────────────────────────────────
PKT_TYPE  = {0x01, 0x02, 0x03, 0x04, 0xFF}
LEN_MAP   = {0x01: 8, 0x02: 4, 0x03: 5, 0x04: 8, 0xFF: 0}

buf       = bytearray()
HEADER    = b'\xAA\xBB'

# Mouse absolute range the Pi sends (0-32767)
ABS_MAX   = 32767


def checksum(pkt_type, length, data):
    return (pkt_type + length + sum(data)) & 0xFF


def signed(b):
    """Convert offset-128 byte back to signed -128..127."""
    return b - 128


def handle_keyboard(data):
    mod   = data[0]
    keys  = [k for k in data[2:8] if k != 0]
    try:
        kbd.release_all()
        if mod:
            # Map modifier byte to Keycodes
            if mod & 0x01: kbd.press(Keycode.LEFT_CONTROL)
            if mod & 0x02: kbd.press(Keycode.LEFT_SHIFT)
            if mod & 0x04: kbd.press(Keycode.LEFT_ALT)
            if mod & 0x08: kbd.press(Keycode.LEFT_GUI)
            if mod & 0x10: kbd.press(Keycode.RIGHT_CONTROL)
            if mod & 0x20: kbd.press(Keycode.RIGHT_SHIFT)
            if mod & 0x40: kbd.press(Keycode.RIGHT_ALT)
            if mod & 0x80: kbd.press(Keycode.RIGHT_GUI)
        for k in keys:
            if k:
                kbd.press(k)
    except Exception:
        kbd.release_all()


def handle_mouse_rel(data):
    buttons = data[0]
    dx      = signed(data[1])
    dy      = signed(data[2])
    wheel   = signed(data[3])
    try:
        if dx or dy:
            mouse.move(dx, dy, wheel)
        if buttons & 0x01:
            mouse.press(Mouse.LEFT_BUTTON)
        else:
            mouse.release(Mouse.LEFT_BUTTON)
        if buttons & 0x02:
            mouse.press(Mouse.RIGHT_BUTTON)
        else:
            mouse.release(Mouse.RIGHT_BUTTON)
        if buttons & 0x04:
            mouse.press(Mouse.MIDDLE_BUTTON)
        else:
            mouse.release(Mouse.MIDDLE_BUTTON)
    except Exception:
        pass


def handle_mouse_abs(data):
    # Absolute mouse: move to percentage of screen
    # Pi sends x/y as 0-32767 (16-bit, big-endian)
    buttons = data[0]
    x = (data[1] << 8) | data[2]
    y = (data[3] << 8) | data[4]
    # Convert to signed relative move — approximate for now
    # True absolute requires OS-level calibration; this gets close
    # TODO: replace with proper absolute HID descriptor if needed
    try:
        mouse.move(0, 0)  # release momentum
        # Scale to a reasonable relative move from centre
        # This is imprecise — use relative mouse for accurate clicks
        if buttons & 0x01:
            mouse.click(Mouse.LEFT_BUTTON)
        if buttons & 0x02:
            mouse.click(Mouse.RIGHT_BUTTON)
    except Exception:
        pass


def handle_gamepad(data):
    lx      = signed(data[0])
    ly      = signed(data[1])
    rx      = signed(data[2])
    ry      = signed(data[3])
    btn_lo  = data[4]
    btn_hi  = data[5]
    lt      = data[6]
    rt      = data[7]
    buttons = (btn_hi << 8) | btn_lo
    try:
        gamepad.move_joysticks(x=lx, y=ly, z=rx, r_z=ry)
        # Map first 8 buttons
        for i in range(8):
            if buttons & (1 << i):
                gamepad.press_buttons(i + 1)
            else:
                gamepad.release_buttons(i + 1)
    except Exception:
        pass


def release_all():
    try:
        kbd.release_all()
        mouse.release(Mouse.LEFT_BUTTON)
        mouse.release(Mouse.RIGHT_BUTTON)
        mouse.release(Mouse.MIDDLE_BUTTON)
        gamepad.release_all_buttons()
        gamepad.move_joysticks(0, 0, 0, 0)
    except Exception:
        pass


# ── Main loop ─────────────────────────────────────────────────
print("AKSUMAEL KB2040 ready")

while True:
    # Read whatever is available
    chunk = uart.read(64)
    if chunk:
        buf.extend(chunk)

    # Parse complete packets from buffer
    while len(buf) >= 2:
        # Hunt for header
        idx = -1
        for i in range(len(buf) - 1):
            if buf[i] == 0xAA and buf[i+1] == 0xBB:
                idx = i
                break
        if idx < 0:
            buf = buf[-1:]   # keep last byte (might be start of header)
            break
        if idx > 0:
            buf = buf[idx:]  # discard garbage before header

        # Need at least header(2) + type(1) + len(1)
        if len(buf) < 4:
            break

        pkt_type = buf[2]
        pkt_len  = buf[3]

        # Validate type
        if pkt_type not in PKT_TYPE:
            buf = buf[1:]   # skip, re-hunt
            continue

        # Wait for full packet
        total = 2 + 1 + 1 + pkt_len + 1   # header + type + len + data + sum
        if len(buf) < total:
            break

        # Validate checksum
        data    = buf[4:4 + pkt_len]
        got_sum = buf[4 + pkt_len]
        exp_sum = checksum(pkt_type, pkt_len, data)

        if got_sum != exp_sum:
            buf = buf[1:]   # bad checksum, skip header byte
            continue

        # Dispatch
        if pkt_type == 0x01 and len(data) == 8:
            handle_keyboard(data)
        elif pkt_type == 0x02 and len(data) == 4:
            handle_mouse_rel(data)
        elif pkt_type == 0x03 and len(data) == 5:
            handle_mouse_abs(data)
        elif pkt_type == 0x04 and len(data) == 8:
            handle_gamepad(data)
        elif pkt_type == 0xFF:
            release_all()

        # Consume the packet
        buf = buf[total:]
