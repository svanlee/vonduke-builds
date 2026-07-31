#!/usr/bin/env python3
"""
pad_bridge.py — Xbox controller → KB2040 USB CDC bridge

Reads an Xbox controller via SDL2 GameController API (pygame._sdl2.controller)
and translates its inputs into ASCII commands sent to the KB2040's USB CDC
data endpoint. This gives Minecraft exactly the same keyboard+mouse input it
would see from a native Xbox controller, because the translation happens at
this bridge, not in the gamepad HID descriptor.

WHY SDL2 GameController instead of pygame.joystick:
  Under raw HID / hid-generic on Linux, Xbox BT controllers expose a combined
  trigger axis (LT positive, RT negative) on axis 2, which shifts right-stick
  X to axis 3 and Y to axis 4. Code reading raw axis indices gets the wrong
  axis for camera look every time a trigger is pressed. SDL2's GameController
  layer normalises to the Xbox layout so LT/RT are always separate, sticks are
  always signed ±1.0, and named accessors work regardless of driver or platform.
  If the controller GUID isn't in SDL's gamecontrollerdb.txt, set:
    SDL_GAMECONTROLLERCONFIG="<guid>,name,platform:Linux,..."
  in the environment before launching, or extend EXTRA_MAPPINGS below.

Usage:
    venv/bin/python3 tools/pad_bridge.py [/dev/ttyACM1]

The KB2040 usually appears as /dev/ttyACM0 (console) and /dev/ttyACM1 (data).
If no argument is given, the script auto-detects the KB2040 data port.

CDC ASCII protocol (one command per line, newline-terminated):
    D<KEY>        key down  (e.g. DW, DSPACE, DLSHIFT)
    U<KEY>        key up
    M<dx>,<dy>    mouse move (integers)
    P<L|R|M>      mouse button press
    X<L|R|M>      mouse button release
    S<n>          scroll wheel (positive = up)
    R             release all

Key tokens (KEYMAP must match rp2040/code.py KEYMAP exactly):
    W A S D Q E T SPACE LSHIFT LCTRL TAB ESCAPE
    F1 F3 F5
    1 2 3 4 5 6 7 8 9

Button mapping (standard Xbox / W3C gamepad layout):
    A             Jump          → SPACE
    B             Sprint        → LSHIFT (hold)
    X             Use/place     → right-click (PR)
    Y             Attack        → left-click (PL)
    LEFTSHOULDER  Drop item     → Q (tap)
    RIGHTSHOULDER Sneak         → LSHIFT (hold while held)
    LT (axis)     not used      (reserved for zoom/scope)
    RT (axis)     Attack/break  → left-click (hold while > 0.2)
    BACK          Inventory     → TAB (tap)
    START         Pause / exit bridge — sends R then quits
    LEFTSTICK     Sprint toggle → LCTRL
    RIGHTSTICK    Change persp  → F5 (tap)
    DPAD_UP       Hotbar 1      → 1
    DPAD_DOWN     Hotbar 2      → 2
    DPAD_LEFT     Hotbar 3      → 3
    DPAD_RIGHT    Hotbar 4      → 4

Left stick:  WASD movement (8-directional with diagonals, dead-zone 0.25)
Right stick: mouse look (scaled to sensitivity below)

Keepalive: empty newline sent every 100 ms so KB2040 watchdog doesn't fire.
"""

import os
import sys
import time
import glob
import serial
import pygame

# ── tuning ────────────────────────────────────────────────────────────────────
DEAD_ZONE         = 0.25      # stick dead-zone (normalised, 0-1)
LOOK_SENSITIVITY  = 25        # max mouse pixels per tick (@ full stick)
TICK_S            = 0.016     # ~60 Hz loop
KEEPALIVE_EVERY   = 0.1       # seconds between empty keepalive newlines
BAUD              = 115200
RT_THRESHOLD      = 0.2       # RT axis threshold for attack/break hold

# Additional SDL2 controller mappings — extend if your controller GUID is
# not in pygame's bundled gamecontrollerdb.txt.
EXTRA_MAPPINGS = [
    # Example: "030000005e040000130b000011050000,Xbox Series X,..."
]

# ── SDL2 GameController axis IDs (pygame constants) ──────────────────────────
# These are stable regardless of the underlying HID driver.
try:
    AXIS_LX = pygame.CONTROLLER_AXIS_LEFTX        # -32768..32767
    AXIS_LY = pygame.CONTROLLER_AXIS_LEFTY
    AXIS_RX = pygame.CONTROLLER_AXIS_RIGHTX
    AXIS_RY = pygame.CONTROLLER_AXIS_RIGHTY
    AXIS_LT = pygame.CONTROLLER_AXIS_TRIGGERLEFT  # 0..32767
    AXIS_RT = pygame.CONTROLLER_AXIS_TRIGGERRIGHT
    BTN_A    = pygame.CONTROLLER_BUTTON_A
    BTN_B    = pygame.CONTROLLER_BUTTON_B
    BTN_X    = pygame.CONTROLLER_BUTTON_X
    BTN_Y    = pygame.CONTROLLER_BUTTON_Y
    BTN_LB   = pygame.CONTROLLER_BUTTON_LEFTSHOULDER
    BTN_RB   = pygame.CONTROLLER_BUTTON_RIGHTSHOULDER
    BTN_BACK = pygame.CONTROLLER_BUTTON_BACK
    BTN_START= pygame.CONTROLLER_BUTTON_START
    BTN_LS   = pygame.CONTROLLER_BUTTON_LEFTSTICK
    BTN_RS   = pygame.CONTROLLER_BUTTON_RIGHTSTICK
    BTN_DU   = pygame.CONTROLLER_BUTTON_DPAD_UP
    BTN_DD   = pygame.CONTROLLER_BUTTON_DPAD_DOWN
    BTN_DL   = pygame.CONTROLLER_BUTTON_DPAD_LEFT
    BTN_DR   = pygame.CONTROLLER_BUTTON_DPAD_RIGHT
    HAS_CONTROLLER_API = True
except AttributeError:
    HAS_CONTROLLER_API = False
    print("[PAD_BRIDGE] WARNING: pygame.CONTROLLER_* not found — "
          "falling back to raw joystick mode (axis mapping may be wrong)")


# ── helpers ───────────────────────────────────────────────────────────────────

def dead(v: float, z: float = DEAD_ZONE) -> float:
    """Apply dead-zone and re-scale to ±1."""
    if abs(v) < z:
        return 0.0
    return (v - z * (1 if v > 0 else -1)) / (1 - z)

def sdl_axis(raw: int, max_val: int = 32767) -> float:
    """Convert SDL2 axis raw int (−32768..32767) to ±1.0."""
    return max(-1.0, min(1.0, raw / max_val))

def sdl_trigger(raw: int, max_val: int = 32767) -> float:
    """Convert SDL2 trigger raw int (0..32767) to 0..1."""
    return max(0.0, min(1.0, raw / max_val))

def find_kb2040_port() -> str:
    """Auto-detect KB2040 USB CDC data port (second ttyACM after console)."""
    ports = sorted(glob.glob("/dev/ttyACM*"))
    if len(ports) >= 2:
        return ports[1]   # [0]=console, [1]=data
    if len(ports) == 1:
        return ports[0]   # single port — hope it's the data one
    raise RuntimeError("No ttyACM devices found — is KB2040 connected?")


# ── SDL2 GameController main loop ─────────────────────────────────────────────

def run_sdl2_controller(ser, ctrl):
    """Main input loop using pygame._sdl2.controller.Controller."""
    from pygame._sdl2 import controller as sdl2_ctrl

    def send(cmd: str):
        ser.write((cmd + "\n").encode())

    def keepalive():
        ser.write(b"\n")

    held_keys  = set()
    held_btns  = set()
    last_ka    = time.monotonic()

    def key_down(tok):
        if tok not in held_keys:
            send(f"D{tok}")
            held_keys.add(tok)

    def key_up(tok):
        if tok in held_keys:
            send(f"U{tok}")
            held_keys.discard(tok)

    def btn_press(side):
        if side not in held_btns:
            send(f"P{side}")
            held_btns.add(side)

    def btn_release(side):
        if side in held_btns:
            send(f"X{side}")
            held_btns.discard(side)

    def release_all():
        send("R")
        held_keys.clear()
        held_btns.clear()

    def tap(tok):
        send(f"D{tok}")
        time.sleep(0.05)
        send(f"U{tok}")

    # Button → (hold_action, tap_action)
    # hold_action: (key_down_fn, key_up_fn) or (btn_press_fn, btn_release_fn) or None
    # tap_action:  callable or None
    BTN_HOLD_MAP = {
        BTN_A:  (lambda p: key_down("SPACE") if p else key_up("SPACE")),
        BTN_B:  (lambda p: key_down("LSHIFT") if p else key_up("LSHIFT")),
        BTN_X:  (lambda p: btn_press("R") if p else btn_release("R")),
        BTN_Y:  (lambda p: btn_press("L") if p else btn_release("L")),
        BTN_LB: (lambda p: tap("Q") if p else None),
        BTN_RB: (lambda p: key_down("LSHIFT") if p else key_up("LSHIFT")),
        BTN_LS: (lambda p: key_down("LCTRL") if p else key_up("LCTRL")),
        BTN_RS: (lambda p: tap("F5") if p else None),
        BTN_DU: (lambda p: tap("1") if p else None),
        BTN_DD: (lambda p: tap("2") if p else None),
        BTN_DL: (lambda p: tap("3") if p else None),
        BTN_DR: (lambda p: tap("4") if p else None),
        BTN_BACK: (lambda p: tap("TAB") if p else None),
    }

    last_btn_state = {}
    print("[PAD_BRIDGE] SDL2 GameController loop running — press Start to quit")

    try:
        while True:
            now = time.monotonic()
            for ev in pygame.event.get():
                if ev.type == pygame.CONTROLLERDEVICEREMOVED:
                    print("[PAD_BRIDGE] controller disconnected")
                    release_all()
                    return False   # signal caller to wait for reconnect

            # ── buttons ───────────────────────────────────────────────────────
            for btn_id, action in BTN_HOLD_MAP.items():
                pressed = bool(ctrl.get_button(btn_id))
                if pressed != last_btn_state.get(btn_id, False):
                    last_btn_state[btn_id] = pressed
                    result = action(pressed)

            # START → quit
            if ctrl.get_button(BTN_START):
                release_all()
                print("[PAD_BRIDGE] Start pressed — exiting")
                return True

            # ── RT trigger → attack/break (left-click hold) ───────────────────
            rt_val = sdl_trigger(ctrl.get_axis(AXIS_RT))
            if rt_val > RT_THRESHOLD:
                btn_press("L")
            else:
                btn_release("L")

            # ── Left stick → WASD ─────────────────────────────────────────────
            lx = dead(sdl_axis(ctrl.get_axis(AXIS_LX)))
            ly = dead(sdl_axis(ctrl.get_axis(AXIS_LY)))

            if ly < -0.3: key_down("W")
            else:         key_up("W")
            if ly > 0.3:  key_down("S")
            else:         key_up("S")
            if lx < -0.3: key_down("A")
            else:         key_up("A")
            if lx > 0.3:  key_down("D")
            else:         key_up("D")

            # ── Right stick → mouse look ───────────────────────────────────────
            rx = dead(sdl_axis(ctrl.get_axis(AXIS_RX)))
            ry = dead(sdl_axis(ctrl.get_axis(AXIS_RY)))

            dx = int(rx * LOOK_SENSITIVITY)
            dy = int(ry * LOOK_SENSITIVITY)
            if dx or dy:
                send(f"M{dx},{dy}")

            # ── keepalive ──────────────────────────────────────────────────────
            if now - last_ka >= KEEPALIVE_EVERY:
                keepalive()
                last_ka = now

            time.sleep(TICK_S)

    except KeyboardInterrupt:
        pass
    finally:
        release_all()

    return True


# ── Joystick fallback (raw HID — axis indices for Xbox BT on Linux) ───────────
# Combined-trigger-axis layout: axis 0=LSX, 1=LSY, 2=LT/RT combined,
# 3=RSX, 4=RSY. RT is at axis 2 biased by LT.
# This path is only used if pygame._sdl2 is unavailable.

def run_joystick_fallback(ser, joy):
    """Fallback: raw joystick with corrected Xbox BT axis layout."""
    print("[PAD_BRIDGE] Using raw joystick fallback — "
          "axis layout assumes Xbox BT on Linux (axes 3/4 = RS)")

    def send(cmd: str):
        ser.write((cmd + "\n").encode())

    def keepalive():
        ser.write(b"\n")

    held_keys = set(); held_btns = set(); last_btn = {}; last_ka = time.monotonic()

    def key_down(tok):
        if tok not in held_keys: send(f"D{tok}"); held_keys.add(tok)
    def key_up(tok):
        if tok in held_keys: send(f"U{tok}"); held_keys.discard(tok)
    def btn_press(s):
        if s not in held_btns: send(f"P{s}"); held_btns.add(s)
    def btn_release(s):
        if s in held_btns: send(f"X{s}"); held_btns.discard(s)
    def release_all():
        send("R"); held_keys.clear(); held_btns.clear()
    def tap(tok):
        send(f"D{tok}"); time.sleep(0.05); send(f"U{tok}")

    TAP_MAP = {4: lambda: tap("Q"), 8: lambda: tap("TAB"),
               11: lambda: tap("F5"), 12: lambda: tap("1"),
               13: lambda: tap("2"), 14: lambda: tap("3"), 15: lambda: tap("4")}

    try:
        while True:
            now = time.monotonic()
            pygame.event.pump()

            for bi in range(joy.get_numbuttons()):
                pressed = bool(joy.get_button(bi))
                if pressed == last_btn.get(bi, False): continue
                last_btn[bi] = pressed
                if bi == 9:
                    if pressed: release_all(); print("[PAD_BRIDGE] Start — exit"); return
                elif bi == 0:
                    key_down("SPACE") if pressed else key_up("SPACE")
                elif bi == 1:
                    key_down("LSHIFT") if pressed else key_up("LSHIFT")
                elif bi == 2:
                    btn_press("R") if pressed else btn_release("R")
                elif bi == 3:
                    btn_press("L") if pressed else btn_release("L")
                elif bi == 5:
                    key_down("LSHIFT") if pressed else key_up("LSHIFT")
                elif bi == 10:
                    key_down("LCTRL") if pressed else key_up("LCTRL")
                elif bi in TAP_MAP:
                    if pressed: TAP_MAP[bi]()

            # Combined trigger axis (axis 2): LT pushes negative, RT positive.
            # Isolate RT by clamping to 0..1.
            if joy.get_numaxes() > 2:
                combined = joy.get_axis(2)
                rt_val = max(0.0, combined)    # RT is positive side
            else:
                rt_val = 0.0
            if rt_val > RT_THRESHOLD: btn_press("L")
            else:                     btn_release("L")

            # Xbox BT on Linux: RS is at axes 3 and 4 (not 2 and 3)
            lx = dead(joy.get_axis(0)) if joy.get_numaxes() > 0 else 0.0
            ly = dead(joy.get_axis(1)) if joy.get_numaxes() > 1 else 0.0
            rx = dead(joy.get_axis(3)) if joy.get_numaxes() > 3 else 0.0
            ry = dead(joy.get_axis(4)) if joy.get_numaxes() > 4 else 0.0

            if ly < -0.3: key_down("W")
            else:         key_up("W")
            if ly > 0.3:  key_down("S")
            else:         key_up("S")
            if lx < -0.3: key_down("A")
            else:         key_up("A")
            if lx > 0.3:  key_down("D")
            else:         key_up("D")

            dx = int(rx * LOOK_SENSITIVITY)
            dy = int(ry * LOOK_SENSITIVITY)
            if dx or dy: send(f"M{dx},{dy}")

            if now - last_ka >= KEEPALIVE_EVERY: keepalive(); last_ka = now
            time.sleep(TICK_S)

    except KeyboardInterrupt:
        pass
    finally:
        release_all()


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_kb2040_port()
    print(f"[PAD_BRIDGE] opening {port} @ {BAUD}")
    ser = serial.Serial(port, BAUD, timeout=0)

    pygame.init()

    # ── Try SDL2 GameController first ─────────────────────────────────────────
    try:
        from pygame._sdl2 import controller as sdl2_ctrl
        sdl2_ctrl.init()

        for mapping in EXTRA_MAPPINGS:
            sdl2_ctrl.add_mapping(mapping)

        print("[PAD_BRIDGE] Waiting for SDL2 GameController...")
        while sdl2_ctrl.get_count() == 0:
            pygame.event.pump()
            time.sleep(0.5)

        ctrl = sdl2_ctrl.Controller(0)
        print(f"[PAD_BRIDGE] SDL2 GameController: {ctrl.name!r}")

        run_sdl2_controller(ser, ctrl)
        ctrl.quit()
        sdl2_ctrl.quit()

    except (ImportError, AttributeError) as e:
        print(f"[PAD_BRIDGE] SDL2 controller unavailable ({e}), using joystick fallback")

        pygame.joystick.init()
        while pygame.joystick.get_count() == 0:
            pygame.event.pump()
            time.sleep(0.5)

        joy = pygame.joystick.Joystick(0)
        joy.init()
        n_axes = joy.get_numaxes()
        print(f"[PAD_BRIDGE] Raw joystick: {joy.get_name()!r} ({n_axes} axes)")
        if n_axes < 5:
            print(f"[PAD_BRIDGE] WARNING: only {n_axes} axes detected — "
                  "right stick or triggers may be missing")
        run_joystick_fallback(ser, joy)

    finally:
        ser.close()
        pygame.quit()
        print("[PAD_BRIDGE] exited cleanly")


if __name__ == "__main__":
    main()
