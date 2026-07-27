#!/usr/bin/env bash
# ============================================================
# Claude Usage Monitor — flash helper (macOS / Linux)
# Compiles + uploads to a connected CYD 4.0" via arduino-cli.
#
# Usage:   ./flash.sh [port]
# Example (Linux/Axon rig): ./flash.sh /dev/ttyUSB0
# Example (macOS):          ./flash.sh /dev/cu.usbserial-0001
# If port is omitted, the first CH340 device found is used.
# On the Axon rig (Linux) that is /dev/ttyUSB* or /dev/ttyACM*;
# on macOS it is /dev/cu.usbserial-* (CH340 driver required).
# ============================================================

set -euo pipefail

ARDUINO_CLI="${ARDUINO_CLI:-arduino-cli}"
FQBN="esp32:esp32:esp32:UploadSpeed=460800,CPUFreq=240,FlashFreq=80,FlashMode=qio,FlashSize=4M,PartitionScheme=default,DebugLevel=none,PSRAM=disabled"

ROOT="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$ROOT/ClaudeUsageMonitor"

PORT="${1:-}"

TFT_PINS="-DTFT_MOSI=13 -DTFT_MISO=12 -DTFT_SCLK=14 -DTFT_CS=15 -DTFT_DC=2 -DTFT_RST=-1 -DTFT_BL=27 -DTFT_BACKLIGHT_ON=HIGH -DTOUCH_CS=33"
TFT_FONTS="-DLOAD_GLCD -DLOAD_FONT2 -DLOAD_FONT4 -DLOAD_FONT6 -DLOAD_FONT7 -DLOAD_FONT8 -DLOAD_GFXFF -DSMOOTH_FONT"
TFT_SPEED="-DSPI_FREQUENCY=40000000 -DSPI_READ_FREQUENCY=20000000 -DSPI_TOUCH_FREQUENCY=2500000"
TFT_DRIVER="-DST7796_DRIVER -DTFT_WIDTH=320 -DTFT_HEIGHT=480"
TFT_FLAGS="-DUSER_SETUP_LOADED $TFT_PINS $TFT_FONTS $TFT_SPEED $TFT_DRIVER"

if [[ -z "$PORT" ]]; then
  if [[ "$(uname -s)" == "Linux" ]]; then
    # Axon rig / Linux: CH340 enumerates as ttyUSB*, some clones as ttyACM*.
    PORT=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -n1 || true)
  else
    PORT=$(ls /dev/cu.usbserial-* /dev/cu.wchusbserial* /dev/cu.SLAB_USBtoUART* 2>/dev/null | head -n1 || true)
  fi
fi

if [[ -z "$PORT" ]]; then
  echo "ERROR: no serial port found and none provided." >&2
  if [[ "$(uname -s)" == "Linux" ]]; then
    echo "Plug the CYD into USB and check 'ls /dev/ttyUSB* /dev/ttyACM*'." >&2
    echo "If it appears but upload fails with permission denied, add yourself" >&2
    echo "to the dialout group:  sudo usermod -aG dialout \"\$USER\"  (re-login)." >&2
  else
    echo "Plug the CYD into USB and check 'ls /dev/cu.*'." >&2
    echo "If nothing shows up, install the CH340 driver." >&2
  fi
  exit 1
fi

echo "Port: $PORT"

"$ARDUINO_CLI" compile \
  --fqbn "$FQBN" \
  --build-property "compiler.cpp.extra_flags=$TFT_FLAGS" \
  --upload --port "$PORT" \
  "$SKETCH"
