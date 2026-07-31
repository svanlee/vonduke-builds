# AKSUMAEL KB2040 — boot.py
# Enables USB CDC data endpoint (pad_bridge ASCII protocol) alongside
# existing HID devices. Hard-reset required after any change to this file.
import usb_hid
import usb_cdc
import storage

# Enable two CDC endpoints: console (REPL) + data (pad_bridge)
usb_cdc.enable(console=True, data=True)

GAMEPAD_REPORT_DESCRIPTOR = bytes((
    # Validated 2026-07-23 on gamepad-tester.net/Chrome — reports as
    # "standard" Xbox mapping.  7-byte report (report ID 4):
    #   bytes 0-2: 17 button bits (B1-B17) + 7 padding bits
    #   bytes 3-6: 4 signed axes (X, Y, Z, Rx)
    #
    # Button layout (0-indexed in W3C / 1-indexed in HID):
    #   B0=A  B1=B  B2=X  B3=Y  B4=LB  B5=RB  B6=LT  B7=RT
    #   B8=Back  B9=Start  B10=LS  B11=RS
    #   B12=DUp  B13=DDown  B14=DLeft  B15=DRight  B16=Guide
    #
    # Axis layout (matches W3C standard gamepad axes 0-3):
    #   Z (0x32) = right stick X   Rx (0x33) = right stick Y
    # NOTE: code.py bypasses adafruit_hid.Gamepad and calls
    # gamepad_dev.send_report() directly with a 7-byte buffer.
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x05,        # Usage (Gamepad)
    0xA1, 0x01,        # Collection (Application)
    # ── 17 Buttons (3 bytes: 17 bits data + 7 bits padding) ──
    0x05, 0x09,        #   Usage Page (Button)
    0x19, 0x01,        #   Usage Minimum (Button 1)
    0x29, 0x11,        #   Usage Maximum (Button 17)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x11,        #   Report Count (17)
    0x81, 0x02,        #   Input (Data, Var, Abs)
    0x75, 0x01,        #   Report Size (1)   — padding
    0x95, 0x07,        #   Report Count (7)
    0x81, 0x03,        #   Input (Const, Var, Abs)
    # ── 4 Axes (4 bytes, signed -127..127) ───────────────────
    0x05, 0x01,        #   Usage Page (Generic Desktop)
    0x15, 0x81,        #   Logical Minimum (-127)
    0x25, 0x7F,        #   Logical Maximum (127)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x04,        #   Report Count (4)
    0x09, 0x30,        #   Usage (X)   → left stick X
    0x09, 0x31,        #   Usage (Y)   → left stick Y
    0x09, 0x32,        #   Usage (Z)   → right stick X
    0x09, 0x33,        #   Usage (Rx)  → right stick Y
    0x81, 0x02,        #   Input (Data, Var, Abs)
    0xC0,              # End Collection
))

gamepad = usb_hid.Device(
    report_descriptor=GAMEPAD_REPORT_DESCRIPTOR,
    usage_page=0x01,
    usage=0x05,
    report_ids=(4,),
    in_report_lengths=(7,),
    out_report_lengths=(0,),
)

# Absolute pointer — separate from usb_hid.Device.MOUSE (relative, used for
# camera-look / TYPE_MOUSE_R) so desktop-style "click at this coordinate"
# (TYPE_MOUSE_A) can land the cursor exactly instead of approximating it
# with relative deltas.
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
    0x75, 0x06,        #   Report Size (6)
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
    in_report_lengths=(5,),
    out_report_lengths=(0,),
)

usb_hid.enable(
    (usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE, gamepad, mouse_abs)
)

storage.disable_usb_drive()
