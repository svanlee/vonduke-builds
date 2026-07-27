# claude-usage-view — Axon rig desk display

A dedicated desk display that shows live **Claude subscription usage** — the
5-hour session window and the weekly limits, the same numbers Claude Code's
`/usage` prints — refreshed every 60 s. Green under 70 %, yellow 70–90 %, red
above.

This is the **Axon rig deployment** of
[`rafelton/claude-usage-view-esp32`](https://github.com/rafelton/claude-usage-view-esp32).
The Axon rig is the **LAFVIN ESP32-S3 AIoT Starter Kit** with its 2.0" ST7789
screen. Upstream targets different hardware on both ends — a Sunton CYD 4.0"
(ESP32-WROOM + ST7796 480×320) driven by a macOS host bridge — so both ends are
retargeted here: the ESP32 firmware to the S3 + ST7789 240×320 panel, and the
host bridge to Linux + systemd (it runs on the AKSUMAEL laptop, which holds the
Claude Code login).

## How it fits together

```
  ┌──────────────────────────────┐        WiFi / LAN        ┌───────────────────────┐
  │  Bridge host (Linux laptop)  │   HTTP GET :8787/usage   │  Axon rig             │
  │  — runs AKSUMAEL, has the    │ <─────────────────────── │  LAFVIN ESP32-S3      │
  │    Claude Code login         │ ─────────────────────>   │  ST7789 320x240 TFT   │
  │  claude_usage_bridge.py      │      digested JSON       │  polls every 60s,     │
  │   • reads ~/.claude/          │                          │  draws session/week   │
  │     .credentials.json         │                          │  bars + reset times   │
  │   • GET api/oauth/usage       │                          └───────────────────────┘
  │   • systemd --user service    │
  └──────────────────────────────┘
```

The bridge never stores or refreshes credentials — Claude Code keeps its OAuth
token fresh, the bridge only reads it. Nothing sensitive touches the ESP32: it
only ever sees pre-digested percentages and reset times.

## What changed from upstream

**ESP32 firmware (CYD 4.0" → LAFVIN ESP32-S3):**

| Piece | Upstream | Axon rig (LAFVIN S3) |
|---|---|---|
| MCU / flashing | ESP32-WROOM, CH340 serial | ESP32-S3, **native USB CDC** (`/dev/ttyACM*`) |
| Display driver | ST7796 480×320 | **ST7789 240×320** (landscape 320×240) |
| Layout | two big cards, font 8 | reflowed for 240px height, font 6 percents |
| Touch handling | drives XPT2046 CS | none (guarded out — panel has no touch) |

**Host bridge (macOS → Linux):**

| Piece | Upstream (macOS) | Axon rig (Linux) |
|---|---|---|
| Token source | Keychain (`security find-generic-password`) | `~/.claude/.credentials.json`, `secret-tool` fallback |
| Service manager | launchd plist + `launchctl` | systemd `--user` unit + `install.sh` |

The original macOS bridge is kept alongside as
[`bridge/claude_usage_bridge.macos.py`](bridge/claude_usage_bridge.macos.py)
for reference.

> **⚠️ Confirm the TFT pins before flashing.** The LAFVIN board's exact ST7789
> GPIOs are board-specific. The `TFT_PINS` in `build.sh`/`flash.sh` are the
> common integrated-S3 mapping and a starting point only — a blank or garbled
> screen almost always means they don't match. Get the real values from the
> `User_Setup.h` that ships with the kit and edit `build.sh`, `flash.sh`, and
> `config.h`. See [`docs/HARDWARE.md`](docs/HARDWARE.md).

## Setup

**1. Bridge (on the AKSUMAEL laptop, as `ros`):**

```bash
cd ~/vonduke-builds/claude-usage-view/bridge
./install.sh                      # installs + enables the systemd --user service
systemctl --user status claude-usage-bridge.service
curl -s http://localhost:8787/usage | python3 -m json.tool   # sanity check
hostname -I                       # note the host's LAN IP for the ESP32
```

`install.sh` warns if Claude Code isn't logged in on the host (no credentials
file). Log in once with `claude` and the bridge starts returning data.

**2. Firmware (build host with `arduino-cli` + the LAFVIN board on USB-C):**

```bash
cd ~/vonduke-builds/claude-usage-view
# First: confirm the TFT pins in build.sh/flash.sh against your kit (see above).
./build.sh                        # -> dist/ClaudeUsageMonitor-2.0.bin
./flash.sh /dev/ttyACM0           # compile + upload to the S3
```

Needs the esp32 core with S3 support (`arduino-cli core install esp32:esp32`)
and libraries in `~/Arduino/libraries`: TFT_eSPI 2.5.x, ArduinoJson 7.x,
WiFiManager 2.x. If the upload won't sync, hold BOOT + tap RST to force download
mode. Permission denied on the port → add yourself to `dialout`
(`sudo usermod -aG dialout "$USER"`, then re-login).

**3. Wire the display to the bridge:** on first boot the ESP32 broadcasts a
`ClaudeUsage-Config` WiFi AP. Join it, open `192.168.4.1`, pick the network, and
set the bridge URL to `http://<bridge-host-ip>:8787/usage`. Hold BOOT for 3 s
any time to re-enter the portal. To skip the portal, copy
`firmware/ClaudeUsageMonitor/config_local.h.example` to `config_local.h` and
hardcode the SSID/pass/URL before flashing.

## Hardware

LAFVIN ESP32-S3 AIoT Starter Kit, 2.0" ST7789 240×320 panel used landscape. Full
pinout (confirm-before-flash) and quirks in [`docs/HARDWARE.md`](docs/HARDWARE.md).

## Credits

Firmware and original macOS bridge by Rafael
([`rafelton/claude-usage-view-esp32`](https://github.com/rafelton/claude-usage-view-esp32)),
MIT-licensed. This directory retargets the firmware to the LAFVIN ESP32-S3 +
ST7789 and ports the bridge to Linux/systemd. See [`CREDITS.md`](CREDITS.md).
