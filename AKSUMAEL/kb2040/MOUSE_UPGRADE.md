# KB2040 Mouse HID — current state & upgrade plan

**Status: implemented.** The upgrade path below has been applied to both
firmware copies (`rp2040/boot.py`+`rp2040/code.py` and
`firmware/kb2040_code.py`) — a dedicated absolute-pointer HID device now
handles `TYPE_MOUSE_A`, and no relative-delta approximation remains.
`rp2040/code.py` also exposes `mouse_abs_move(x, y, screen_w, screen_h)`
and `mouse_abs_click(x, y, button)` helpers for pixel-coordinate use.
The two firmware copies were left as separate files rather than
de-duplicated (item 4 below) — that refactor is still open.

The rest of this document is kept as a record of the original
investigation. See `rp2040/` and `firmware/` for the actual CircuitPython
source.

## Where the firmware actually lives

There are **two** copies of this firmware in the repo, and they are not
identical:

| File | Role | HID approach |
|---|---|---|
| `rp2040/boot.py` + `rp2040/code.py` | The one `rp2040/README.md` documents flashing — treat this as canonical/deployed | `adafruit_hid` library (`Keyboard`, `Mouse`, `Gamepad` helper classes) |
| `firmware/kb2040_code.py` | An alternate, self-contained implementation with no `adafruit_hid` dependency | Hand-rolled `usb_hid.Device.send_report()` calls |

Both parse the same `[0xAA][0xBB][TYPE][LEN][DATA...][CKSUM]` packet format
from `uart/kb2040_packer.py` (host side), and both already enable keyboard +
mouse + gamepad simultaneously:

```python
# rp2040/boot.py
usb_hid.enable((usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE, gamepad))
```

`gamepad` there is a custom 16-button/4-axis HID descriptor (`boot.py`
lines 6–31) — CircuitPython's built-in `usb_hid.Device` set doesn't ship a
stock gamepad, so this was already hand-written. **No keyboard+mouse+gamepad
upgrade is needed** — that work is done, on both copies.

## The actual gap: absolute-position clicking doesn't work

The env_profile architecture (`core/env_profile.py` /
`core/label_queue.py`) is aimed at eventually letting AKSUMAEL operate an
arbitrary desktop OS, not just Minecraft. Desktop UI interaction is
fundamentally "click at this coordinate" (a Start menu, an OK button, a
taskbar icon) — that's `TYPE_MOUSE_A` (0x03) in the packet protocol, built
by `uart.kb2040_packer.pack_mouse_absolute()` / `pack_mouse_click_at()`,
and it's already wired into `actions/executor.py` via any action dict with
a `click: [x_pct, y_pct]` field.

Reading both firmware implementations, `TYPE_MOUSE_A` doesn't actually
move the cursor to the target:

**`rp2040/code.py::handle_mouse_abs()`** (the deployed copy) ignores x/y
entirely for movement — it reads them off the packet, then just clicks
wherever the cursor already happens to be:

```python
def handle_mouse_abs(data):
    ...
    # Convert to signed relative move — approximate for now
    # True absolute requires OS-level calibration; this gets close
    # TODO: replace with proper absolute HID descriptor if needed
    try:
        mouse.move(0, 0)  # release momentum
        if buttons & 0x01:
            mouse.click(Mouse.LEFT_BUTTON)
        ...
```

That `TODO` is exactly this document's subject. In Minecraft this went
unnoticed because gameplay clicks (`click_left`/`click_right` on whatever's
under the crosshair, which is always screen-center) don't need absolute
positioning — `AimController` (`actions/aim_controller.py`) does the
targeting via relative mouse-look instead, and `action_dict_to_packets()`
only emits `TYPE_MOUSE_A` for a genuine `click: [x, y]` action, which
nothing in the current Minecraft-only runtime loop actually sends.

**`firmware/kb2040_code.py`**'s `TYPE_MOUSE_A` branch does attempt a move,
but by converting the absolute target into one relative jump computed from
an assumed screen center (`dx = (x - 32768) >> 8`) rather than tracking
where the cursor actually is — a USB relative mouse has no concept of
absolute position on the host, so this silently drifts after the very
first click and only happens to be right if the cursor started centered.

Neither copy can reliably click a specific desktop coordinate today. For
Minecraft this doesn't matter. For "plug into any OS and operate it," it's
a hard blocker — clicking a numbered menu item, a specific icon, or a
dialog button is the primary interaction primitive on a general desktop.

## Upgrade path (not yet implemented)

Replace the relative-move approximation with a genuine USB HID **absolute
pointer** report — the standard way USB tablets/touchscreens report
position, and something host OSes (Windows/Linux/macOS) already know how
to interpret without any relative-motion math on either end:

1. **`boot.py`**: add a second custom `usb_hid.Device` (alongside the
   existing gamepad one) with an absolute-pointer report descriptor —
   `Generic Desktop / Pointer`, `Logical Minimum 0` / `Logical Maximum
   32767` on X and Y, `Input (Data,Var,Abs)` instead of `Rel`. Keep the
   existing relative `usb_hid.Device.MOUSE` for camera-look
   (`TYPE_MOUSE_R`) — the two are complementary, not a replacement for
   each other:
   - `TYPE_MOUSE_R` (relative) → game camera look, drag-style gestures
   - new absolute pointer → desktop UI clicks (`TYPE_MOUSE_A`)
2. **`code.py`** (both copies): change `handle_mouse_abs()` (currently
   `handle_packet()`'s `TYPE_MOUSE_A` case in `firmware/kb2040_code.py`) to
   build and `send_report()` on the new absolute device directly — no
   relative-delta math, no center-of-screen assumption.
3. **Host side** (`uart/kb2040_packer.py`) needs no protocol change —
   `pack_mouse_absolute()` already sends the 0–32767 x/y pair `TYPE_MOUSE_A`
   expects; only the firmware's interpretation of that payload changes.
4. **De-duplicate the two firmware copies** while this is being touched
   anyway — right now a fix applied to one silently doesn't apply to the
   other, which is how `TYPE_MOUSE_A` ended up in two different broken
   states. Pick `rp2040/` (the one the README documents) as canonical and
   either delete `firmware/kb2040_code.py` or make it a thin wrapper that
   imports from `rp2040/code.py`.
5. Add a self-test to `rp2040/README.md`'s "Standalone test" section:
   send a `TYPE_MOUSE_A` packet for a known screen quadrant and confirm
   the cursor actually lands there, not just that a click fires.

None of the above is implemented in this change — this file exists so the
next session (or whoever flashes new firmware) has the actual failure
mode written down instead of rediscovering it by watching clicks land in
the wrong place.
