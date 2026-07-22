"""
AKSUMAEL — Windows-side KB2040 -> virtual Xbox 360 controller bridge
=====================================================================

WHAT THIS IS
    Reads the KB2040's generic HID gamepad (as enumerated by rp2040/boot.py's
    GAMEPAD_REPORT_DESCRIPTOR: 16 buttons + 4 signed axes X/Y/Z/Rz) via
    pygame, and mirrors it 1:1 onto a ViGEmBus virtual Xbox 360 controller
    via vgamepad, so any Windows game/app that only understands XInput can
    see the KB2040 as a real Xbox controller.

HOW TO RUN (on the Windows machine)
    1. Confirm ViGEmBus driver is installed (already done per setup notes).
    2. Install Python deps:
           pip install pygame vgamepad
       (vgamepad itself is already installed per setup notes; pygame likely
       is not — install it too.)
    3. Plug in the KB2040.
    4. From this file's directory, run:
           python win_xinput_bridge.py
    5. Leave the window open — it runs until Ctrl+C. It prints a line when
       the KB2040 connects/disconnects, and auto-reconnects if unplugged.

CONFIG YOU MAY WANT TO TWEAK (below)
    NAME_HINTS          - substrings to prefer when picking among multiple
                           matching HID gamepads (KB2040 usually enumerates
                           with an Adafruit/CircuitPython product string).
    AXIS_DEADZONE        - stick deadzone (0.0-1.0).
    INVERT_LEFT_Y/RIGHT_Y - flip stick Y if forward/back feels backwards.
    BUTTON_MAP           - which pygame button index maps to which Xbox
                           button (see note below).

KNOWN HARDWARE LIMITATIONS (confirmed from rp2040/boot.py + rp2040/code.py)
  - No analog triggers on the wire: GAMEPAD_REPORT_DESCRIPTOR only declares
    16 buttons + 4 axes (X, Y, Z, Rz) — there are no trigger axes. The
    lt/rt bytes in the UART gamepad packet (uart/kb2040_packer.py
    pack_gamepad) are consumed by rp2040/code.py's handle_gamepad() but
    never forwarded to the actual USB HID report (Gamepad.move_joysticks
    only takes x/y/z/r_z). So LT/RT cannot be read from this HID device
    today; this bridge always reports them as 0. To fix at the source,
    boot.py's descriptor would need two more analog axes.
  - No D-pad/hat in the descriptor either. rp2040/code.py packs all 16
    button bits from a flat `buttons` field (bit 15 is reserved for the
    guide button per its comment: "guide rides in bit 15 of the existing
    16-bit buttons field"). There is no established convention in this
    repo for which of the remaining bits are meant to be a D-pad, since
    the host-side apps that build the `gamepad` action dict just forward
    an arbitrary buttons bitmask. BUTTON_MAP below reserves bits 10-13 as
    a D-pad by convention (adjust freely to match whatever your
    controlling code actually sends) and bit 15 as Guide, matching
    boot.py/code.py's fixed guide-bit convention. Bit 14 is left
    unmapped/spare.

AXIS MAPPING (confirmed from rp2040/boot.py's report descriptor order)
    Descriptor declares Usage X, Y, Z, Rz in that order with Report Count 4,
    so pygame will expose them as axis 0=X, 1=Y, 2=Z, 3=Rz:
        axis 0 (X)  -> left stick X
        axis 1 (Y)  -> left stick Y
        axis 2 (Z)  -> right stick X
        axis 3 (Rz) -> right stick Y
"""

import sys
import time

try:
    import pygame
except ImportError:
    sys.exit("pygame is not installed. Run: pip install pygame")

try:
    import vgamepad as vg
    from vgamepad import XUSB_BUTTON
except ImportError:
    sys.exit("vgamepad is not installed. Run: pip install vgamepad")


# ── Config ────────────────────────────────────────────────────────
EXPECTED_AXES = 4
EXPECTED_BUTTONS = 16
NAME_HINTS = ("kb2040", "adafruit")

AXIS_DEADZONE = 0.03
LOOP_HZ = 125
RESCAN_INTERVAL = 1.0  # seconds between reconnect scans while disconnected

INVERT_LEFT_Y = True
INVERT_RIGHT_Y = True

# pygame button index -> vgamepad XUSB_BUTTON. See "KNOWN HARDWARE
# LIMITATIONS" above for why bit 15 is Guide and bits 10-13 are a D-pad
# convention rather than a hardware fact.
BUTTON_MAP = {
    0: "XUSB_GAMEPAD_A",
    1: "XUSB_GAMEPAD_B",
    2: "XUSB_GAMEPAD_X",
    3: "XUSB_GAMEPAD_Y",
    4: "XUSB_GAMEPAD_LEFT_SHOULDER",
    5: "XUSB_GAMEPAD_RIGHT_SHOULDER",
    6: "XUSB_GAMEPAD_BACK",
    7: "XUSB_GAMEPAD_START",
    8: "XUSB_GAMEPAD_LEFT_THUMB",
    9: "XUSB_GAMEPAD_RIGHT_THUMB",
    10: "XUSB_GAMEPAD_DPAD_UP",
    11: "XUSB_GAMEPAD_DPAD_DOWN",
    12: "XUSB_GAMEPAD_DPAD_LEFT",
    13: "XUSB_GAMEPAD_DPAD_RIGHT",
    # 14 intentionally unmapped/spare
    15: "XUSB_GAMEPAD_GUIDE",
}
# Resolve to actual enum members now, dropping any name this vgamepad
# version doesn't define (e.g. older builds may lack GUIDE).
BUTTON_MAP = {
    idx: getattr(XUSB_BUTTON, name)
    for idx, name in BUTTON_MAP.items()
    if hasattr(XUSB_BUTTON, name)
}


def find_kb2040():
    """Scan connected joysticks for one matching the KB2040's exact HID
    shape (4 axes, 16 buttons per rp2040/boot.py). If more than one
    matches, prefer a name containing NAME_HINTS."""
    candidates = []
    for i in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(i)
        js.init()
        if js.get_numaxes() == EXPECTED_AXES and js.get_numbuttons() == EXPECTED_BUTTONS:
            candidates.append(js)
    if not candidates:
        return None
    for js in candidates:
        name = js.get_name().lower()
        if any(hint in name for hint in NAME_HINTS):
            return js
    return candidates[0]


def apply_deadzone(v, dz=AXIS_DEADZONE):
    return 0.0 if abs(v) < dz else v


def scale_axis(v):
    """pygame axis (-1.0..1.0) -> vgamepad stick range (-32768..32767)."""
    v = max(-1.0, min(1.0, v))
    return int(v * 32768) if v < 0 else int(v * 32767)


def main():
    pygame.init()
    pygame.joystick.init()

    gamepad = vg.VX360Gamepad()
    joystick = None
    last_scan = 0.0
    frame_dt = 1.0 / LOOP_HZ

    print("[bridge] waiting for KB2040 gamepad...")

    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEREMOVED:
                if joystick is not None and event.instance_id == joystick.get_instance_id():
                    print("[bridge] KB2040 disconnected")
                    joystick = None
                    gamepad.reset()
                    gamepad.update()

        if joystick is None:
            now = time.monotonic()
            if now - last_scan >= RESCAN_INTERVAL:
                last_scan = now
                pygame.joystick.quit()
                pygame.joystick.init()
                joystick = find_kb2040()
                if joystick is not None:
                    print(f"[bridge] KB2040 connected: {joystick.get_name()}")
            time.sleep(frame_dt)
            continue

        try:
            lx = scale_axis(apply_deadzone(joystick.get_axis(0)))
            ly_raw = apply_deadzone(joystick.get_axis(1))
            ly = scale_axis(-ly_raw if INVERT_LEFT_Y else ly_raw)
            rx = scale_axis(apply_deadzone(joystick.get_axis(2)))
            ry_raw = apply_deadzone(joystick.get_axis(3))
            ry = scale_axis(-ry_raw if INVERT_RIGHT_Y else ry_raw)

            gamepad.left_joystick(x_value=lx, y_value=ly)
            gamepad.right_joystick(x_value=rx, y_value=ry)

            for i in range(min(joystick.get_numbuttons(), EXPECTED_BUTTONS)):
                xbtn = BUTTON_MAP.get(i)
                if xbtn is None:
                    continue
                if joystick.get_button(i):
                    gamepad.press_button(button=xbtn)
                else:
                    gamepad.release_button(button=xbtn)

            # No analog trigger axes on the wire — see KNOWN HARDWARE
            # LIMITATIONS at the top of this file.
            gamepad.left_trigger(value=0)
            gamepad.right_trigger(value=0)

            gamepad.update()
        except pygame.error as e:
            print(f"[bridge] lost KB2040: {e}")
            joystick = None
            gamepad.reset()
            gamepad.update()

        time.sleep(frame_dt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[bridge] stopped")
