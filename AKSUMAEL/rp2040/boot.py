# AKSUMAEL KB2040 — boot.py
import usb_hid
import storage

GAMEPAD_REPORT_DESCRIPTOR = bytes((
    # IMPORTANT: axes must come FIRST in the report so the byte layout
    # matches the order the Adafruit HID Gamepad library packs its send_report:
    #   bytes 0-3: X, Y, Z, Rz (signed, -127..127)
    #   bytes 4-5: 16 button bits (little-endian)
    # The previous descriptor had buttons FIRST which swapped buttons and
    # axes in the HID report: Windows read the axis bytes as button bits and
    # the button bytes as the first two axes, causing left/right sticks to
    # appear crossed. Fixed 2026-07-22.
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x05,        # Usage (Gamepad)
    0xA1, 0x01,        # Collection (Application)
    # ── Axes first (4 bytes) ─────────────────────────────────
    0x05, 0x01,        #   Usage Page (Generic Desktop)
    0x15, 0x81,        #   Logical Minimum (-127)
    0x25, 0x7F,        #   Logical Maximum (127)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x04,        #   Report Count (4)
    0x09, 0x30,        #   Usage (X)   → left stick X
    0x09, 0x31,        #   Usage (Y)   → left stick Y
    0x09, 0x32,        #   Usage (Z)   → right stick X
    0x09, 0x35,        #   Usage (Rz)  → right stick Y
    0x81, 0x02,        #   Input (Data,Var,Abs)
    # ── Buttons second (2 bytes = 16 bits) ───────────────────
    0x05, 0x09,        #   Usage Page (Button)
    0x19, 0x01,        #   Usage Minimum (Button 1)
    0x29, 0x10,        #   Usage Maximum (Button 16)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x10,        #   Report Count (16)
    0x81, 0x02,        #   Input (Data,Var,Abs)
    0xC0,              # End Collection
))

gamepad = usb_hid.Device(
    report_descriptor=GAMEPAD_REPORT_DESCRIPTOR,
    usage_page=0x01,
    usage=0x05,
    report_ids=(0,),
    in_report_lengths=(6,),
    out_report_lengths=(0,),
)

# Absolute pointer — separate from usb_hid.Device.MOUSE (relative, used for
# camera-look / TYPE_MOUSE_R) so desktop-style "click at this coordinate"
# (TYPE_MOUSE_A) can land the cursor exactly instead of approximating it
# with relative deltas. Top-level usage is Pointer (0x01), not Mouse
# (0x02), specifically so it doesn't collide with adafruit_hid.Mouse's
# usage-page/usage lookup for the relative MOUSE device in code.py.
ABS_POINTER_REPORT_DESCRIPTOR = bytes((
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x01,        # Usage (Pointer)
    0xA1, 0x01,        # Collection (Application)
    0x05, 0x09,        #   Usage Page (Button)
    0x19, 0x01,        #   Usage Minimum (Button 1)
    0x29, 0x02,        #   Usage Maximum (Button 2)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x95, 0x02,        #   Report Count (2)
    0x75, 0x01,        #   Report Size (1)
    0x81, 0x02,        #   Input (Data,Var,Abs)
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x06,        #   Report Size (6)  -- pad to a full byte
    0x81, 0x03,        #   Input (Const,Var,Abs)
    0x05, 0x01,        #   Usage Page (Generic Desktop)
    0x09, 0x30,        #   Usage (X)
    0x09, 0x31,        #   Usage (Y)
    0x16, 0x00, 0x00,  #   Logical Minimum (0)
    0x26, 0xFF, 0x7F,  #   Logical Maximum (32767)
    0x75, 0x10,        #   Report Size (16)
    0x95, 0x02,        #   Report Count (2)
    0x81, 0x02,        #   Input (Data,Var,Abs)
    0xC0,              # End Collection
))

mouse_abs = usb_hid.Device(
    report_descriptor=ABS_POINTER_REPORT_DESCRIPTOR,
    usage_page=0x01,
    usage=0x01,
    report_ids=(0,),
    in_report_lengths=(5,),   # buttons(1) + x(2) + y(2), little-endian
    out_report_lengths=(0,),
)

usb_hid.enable(
    (usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE, gamepad, mouse_abs)
)

storage.disable_usb_drive()
