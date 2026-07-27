#!/usr/bin/env bash
# ============================================================
# Claude Usage Monitor — build script (Linux / macOS)
# Board: LAFVIN ESP32-S3 AIoT Starter Kit ("Axon rig").
# Display: 2.0" TFT ST7789, 240x320 (landscape 320x240).
# Generates a merged .bin in dist/, flashable at address 0x0000.
#
# Uses arduino-cli and the libraries in ~/Arduino/libraries or
# ~/Documents/Arduino/libraries (TFT_eSPI 2.5.x, ArduinoJson 7.x,
# WiFiManager 2.x). Needs the esp32 core with S3 support:
#   arduino-cli core install esp32:esp32
# ============================================================

set -euo pipefail

ARDUINO_CLI="${ARDUINO_CLI:-arduino-cli}"
# ESP32-S3, native USB CDC on boot so Serial works over the USB-C port.
FQBN="esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,CPUFreq=240,FlashFreq=80,FlashMode=qio,FlashSize=8M,PartitionScheme=default,PSRAM=disabled,DebugLevel=none"

ROOT="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$ROOT/firmware/ClaudeUsageMonitor"
DIST="$ROOT/dist"
BUILDDIR="$ROOT/build/LAFVIN_S3"
mkdir -p "$DIST"

if ! command -v "$ARDUINO_CLI" >/dev/null 2>&1; then
  echo "ERROR: arduino-cli not found." >&2
  echo "  Linux: curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh" >&2
  echo "  macOS: brew install arduino-cli" >&2
  exit 1
fi

# ============================================================
# >>> CONFIRM THESE PINS AGAINST YOUR KIT'S User_Setup.h <<<
# TFT_eSPI is configured entirely via build flags (no User_Setup.h).
# The LAFVIN AIoT board's exact ST7789 GPIOs are in the kit docs /
# the User_Setup.h that ships with it. The values below are the common
# integrated-S3 + ST7789 mapping and are a STARTING POINT only — a blank
# or garbled screen almost always means these don't match your board.
# ST7789 is write-only, so MISO is unused (-1).
# ============================================================
TFT_PINS="-DTFT_MOSI=45 -DTFT_MISO=-1 -DTFT_SCLK=40 -DTFT_CS=42 -DTFT_DC=41 -DTFT_RST=39 -DTFT_BL=14 -DTFT_BACKLIGHT_ON=HIGH"
TFT_FONTS="-DLOAD_GLCD -DLOAD_FONT2 -DLOAD_FONT4 -DLOAD_FONT6 -DLOAD_FONT7 -DLOAD_FONT8 -DLOAD_GFXFF -DSMOOTH_FONT"
TFT_SPEED="-DSPI_FREQUENCY=40000000 -DSPI_READ_FREQUENCY=20000000"
TFT_DRIVER="-DST7789_DRIVER -DTFT_WIDTH=240 -DTFT_HEIGHT=320"
# Many 2.0" ST7789 IPS panels need inverted colors. If the display looks
# photo-negative, add -DTFT_INVERSION_ON here (or _OFF if it starts inverted).
TFT_FLAGS="-DUSER_SETUP_LOADED $TFT_PINS $TFT_FONTS $TFT_SPEED $TFT_DRIVER"

rm -rf "$BUILDDIR"

"$ARDUINO_CLI" compile \
  --fqbn "$FQBN" \
  --build-path "$BUILDDIR" \
  --build-property "compiler.cpp.extra_flags=$TFT_FLAGS" \
  "$SKETCH"

cp "$BUILDDIR/ClaudeUsageMonitor.ino.merged.bin" "$DIST/ClaudeUsageMonitor-2.0.bin"

echo
echo "============================================================"
echo "Build OK. Artifact: $DIST/ClaudeUsageMonitor-2.0.bin"
echo "Flash at address 0x0000 (esptool / esptool-js), or ./flash.sh"
echo "============================================================"
