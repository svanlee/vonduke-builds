#!/usr/bin/env bash
# ============================================================
# Claude Usage Monitor — build script (macOS / Linux)
# Board: Sunton CYD 4.0" (ESP32-4827S040), ST7796S 320x480.
# Generates a merged .bin in dist/, flashable at address 0x0000.
#
# Uses arduino-cli (brew install arduino-cli) and the libraries
# already installed in ~/Documents/Arduino/libraries
# (TFT_eSPI 2.5.x, ArduinoJson 7.x, WiFiManager 2.x).
# ============================================================

set -euo pipefail

ARDUINO_CLI="${ARDUINO_CLI:-arduino-cli}"
FQBN="esp32:esp32:esp32:UploadSpeed=921600,CPUFreq=240,FlashFreq=80,FlashMode=qio,FlashSize=4M,PartitionScheme=default,DebugLevel=none,PSRAM=disabled"

ROOT="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$ROOT/ClaudeUsageMonitor"
DIST="$ROOT/dist"
BUILDDIR="$ROOT/build/CYD40"
mkdir -p "$DIST"

if ! command -v "$ARDUINO_CLI" >/dev/null 2>&1; then
  echo "ERROR: arduino-cli not found." >&2
  echo "  Linux: curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh" >&2
  echo "  macOS: brew install arduino-cli" >&2
  exit 1
fi

# TFT_eSPI configured entirely via build flags (no User_Setup.h).
# Sunton CYD shared pinout; CYD 4.0" backlight is GPIO 27.
TFT_PINS="-DTFT_MOSI=13 -DTFT_MISO=12 -DTFT_SCLK=14 -DTFT_CS=15 -DTFT_DC=2 -DTFT_RST=-1 -DTFT_BL=27 -DTFT_BACKLIGHT_ON=HIGH -DTOUCH_CS=33"
TFT_FONTS="-DLOAD_GLCD -DLOAD_FONT2 -DLOAD_FONT4 -DLOAD_FONT6 -DLOAD_FONT7 -DLOAD_FONT8 -DLOAD_GFXFF -DSMOOTH_FONT"
TFT_SPEED="-DSPI_FREQUENCY=40000000 -DSPI_READ_FREQUENCY=20000000 -DSPI_TOUCH_FREQUENCY=2500000"
TFT_DRIVER="-DST7796_DRIVER -DTFT_WIDTH=320 -DTFT_HEIGHT=480"
TFT_FLAGS="-DUSER_SETUP_LOADED $TFT_PINS $TFT_FONTS $TFT_SPEED $TFT_DRIVER"

rm -rf "$BUILDDIR"

"$ARDUINO_CLI" compile \
  --fqbn "$FQBN" \
  --build-path "$BUILDDIR" \
  --build-property "compiler.cpp.extra_flags=$TFT_FLAGS" \
  "$SKETCH"

cp "$BUILDDIR/ClaudeUsageMonitor.ino.merged.bin" "$DIST/ClaudeUsageMonitor-4.0.bin"

echo
echo "============================================================"
echo "Build OK. Artifact: $DIST/ClaudeUsageMonitor-4.0.bin"
echo "Flash at address 0x0000 (esptool / esptool-js), or ./flash.sh"
echo "============================================================"
