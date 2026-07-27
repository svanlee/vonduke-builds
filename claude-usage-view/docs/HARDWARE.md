# Board reference — LAFVIN ESP32-S3 AIoT Starter Kit ("Axon rig")

The Axon rig for this project is the **LAFVIN ESP32-S3 AIoT Starter Kit**: an
ESP32-S3 module on the kit's "AI Chatbot IoT Shield," with a **2.0" SPI TFT
(ST7789, 240×320)**, audio codec + speaker, and a set of plug-in modules (RGB
LED, WS2812 8-LED strip, DHT11, SG90 servo, DC fan, 2-ch relay, rain/soil
sensors). This project uses only the ESP32-S3 + the ST7789 screen; the other
modules are unused by the usage monitor.

Upstream (`rafelton/claude-usage-view-esp32`) targets a Sunton CYD 4.0"
(ESP32-WROOM + ST7796 480×320). The firmware here is retargeted to the LAFVIN
board: different MCU (S3, native USB), different driver (ST7789), and a smaller
240×320 panel, so the on-screen layout was reflowed for landscape 320×240.

## Specs

| Item | Value |
|---|---|
| MCU | ESP32-S3 (dual-core 240 MHz), native USB-OTG |
| Display | 2.0" TFT, **ST7789**, 240×320, SPI, portrait-native |
| Touch | none on this panel (unlike the CYD's XPT2046) |
| USB | USB-C, **native USB CDC** (no CH340 serial bridge) |
| Buttons | RESET, BOOT (GPIO 0) |

## Pinout — CONFIRM before flashing

The display SPI pins are **board-specific** and this repo does not have an
authoritative LAFVIN pinout. TFT_eSPI is configured via build flags in
[`build.sh`](../build.sh) / [`flash.sh`](../flash.sh); the values there are the
common integrated-S3 + ST7789 mapping and are a **starting point**, not a
verified fact for this exact kit:

| Function | GPIO (starting point — confirm) |
|---|---|
| TFT MOSI / SCLK | 45 / 40 |
| TFT CS / DC / RST | 42 / 41 / 39 |
| TFT backlight (BL) | 14 |
| TFT MISO | unused (ST7789 is write-only) |
| BOOT button | 0 |

Get the real numbers from the **User_Setup.h that ships with the kit** (or the
kit's online docs / pinout diagram) and edit the `TFT_PINS` line in both
`build.sh` and `flash.sh` plus `TFT_BL_PIN` in
[`firmware/ClaudeUsageMonitor/config.h`](../firmware/ClaudeUsageMonitor/config.h)
to match.

## Quirks

- **Native USB**, not CH340. Flash over the USB-C port; the board appears as
  `/dev/ttyACM*` (Linux) / `/dev/cu.usbmodem*` (macOS). If the upload won't
  sync, hold BOOT + tap RST to force download mode.
- **ST7789 color inversion.** Many 2.0" IPS ST7789 panels display
  photo-negative unless inversion is enabled. If colors look wrong, add
  `-DTFT_INVERSION_ON` (or `_OFF`) to the `TFT_DRIVER` flags in build.sh.
- **Portrait-native panel.** 240×320 with `setRotation(1)` for landscape
  320×240 (TFT_eSPI flags stay `TFT_WIDTH=240 / TFT_HEIGHT=320`).
- **No PSRAM used.** The build sets `PSRAM=disabled`; the layout draws with
  partial fills (no full-screen sprite), so no PSRAM is needed even though the
  module may have some.
- **Flashing**: merged `.bin` at address `0x0000`.

## Related resources

- [LAFVIN AIoT Starter Kit docs](https://lafvin-aiot-starter-kit.readthedocs.io/) — official kit documentation / pinout
- [TFT_eSPI](https://github.com/Bodmer/TFT_eSPI) — display driver library, ST7789 support
- Upstream project: [`rafelton/claude-usage-view-esp32`](https://github.com/rafelton/claude-usage-view-esp32)
