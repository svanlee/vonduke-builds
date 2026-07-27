# claude-usage-view — Axon rig desk display

A dedicated ESP32 desk display that shows live **Claude subscription usage** —
the 5-hour session window and the weekly limits, the same numbers Claude Code's
`/usage` prints — refreshed every 60 s. Green under 70 %, yellow 70–90 %, red
above.

This is the **Axon rig deployment** of
[`rafelton/claude-usage-view-esp32`](https://github.com/rafelton/claude-usage-view-esp32).
Upstream targets macOS; the Axon rig is a Linux laptop (HP Victus, the box that
runs AKSUMAEL and its `axon` voice hub), so the host-side bridge is ported to
Linux + systemd here. The ESP32 firmware itself is portable and vendored
essentially unchanged.

## How it fits together

```
  ┌─────────────────────────────┐         WiFi / LAN        ┌──────────────────┐
  │  Axon rig (Linux, user ros) │   HTTP GET :8787/usage    │  ESP32 CYD 4.0"  │
  │                             │ <──────────────────────── │  ST7796 480x320  │
  │  claude_usage_bridge.py     │ ──────────────────────>   │  polls every 60s │
  │   • reads ~/.claude/         │      digested JSON        │  draws the bars  │
  │     .credentials.json        │                           └──────────────────┘
  │   • GET api/oauth/usage      │
  │   • systemd --user service   │
  └─────────────────────────────┘
```

The bridge never stores or refreshes credentials — Claude Code keeps its OAuth
token fresh, the bridge only reads it. Nothing sensitive touches the ESP32: it
only ever sees pre-digested percentages and reset times.

## What changed from upstream (macOS → Axon rig / Linux)

| Piece | Upstream (macOS) | Axon rig (Linux) |
|---|---|---|
| Token source | Keychain (`security find-generic-password`) | `~/.claude/.credentials.json`, `secret-tool` fallback |
| Service manager | launchd plist + `launchctl` | systemd `--user` unit + `install.sh` |
| Flash port autodetect | `/dev/cu.usbserial-*` only | adds `/dev/ttyUSB*` / `/dev/ttyACM*` |
| Bridge URL / portal copy | "your-mac-ip" | "axon-rig-ip" |

The original macOS bridge is kept alongside as
[`bridge/claude_usage_bridge.macos.py`](bridge/claude_usage_bridge.macos.py)
for reference.

## Setup on the Axon rig

**1. Bridge (on the rig, as `ros`):**

```bash
cd ~/vonduke-builds/claude-usage-view/bridge
./install.sh                      # installs + enables the systemd --user service
systemctl --user status claude-usage-bridge.service
curl -s http://localhost:8787/usage | python3 -m json.tool   # sanity check
hostname -I                       # note the rig's LAN IP for the ESP32
```

`install.sh` warns if Claude Code isn't logged in on the rig (no credentials
file). Log in once with `claude` and the bridge starts returning data.

**2. Firmware (build host with `arduino-cli` + the CYD plugged in):**

```bash
cd ~/vonduke-builds/claude-usage-view
./build.sh                        # -> dist/ClaudeUsageMonitor-4.0.bin
./flash.sh /dev/ttyUSB0           # compile + upload to the CYD
```

Libraries needed in `~/Documents/Arduino/libraries`: TFT_eSPI 2.5.x,
ArduinoJson 7.x, WiFiManager 2.x. If upload fails with a permissions error,
add yourself to `dialout` (`sudo usermod -aG dialout "$USER"`, then re-login).

**3. Wire the display to the bridge:** on first boot the ESP32 broadcasts a
`ClaudeUsage-Config` WiFi AP. Join it, open `192.168.4.1`, pick the rig's
network, and set the bridge URL to `http://<axon-rig-ip>:8787/usage`. Hold BOOT
for 3 s any time to re-enter the portal. To skip the portal, copy
`firmware/ClaudeUsageMonitor/config_local.h.example` to `config_local.h` and
hardcode the SSID/pass/URL before flashing.

## Hardware

Sunton CYD 4.0" (ESP32-4827S040), ST7796S 320×480 panel used landscape. Full
pinout and quirks in [`docs/HARDWARE.md`](docs/HARDWARE.md).

## Credits

Firmware and original macOS bridge by Rafael
([`rafelton/claude-usage-view-esp32`](https://github.com/rafelton/claude-usage-view-esp32)),
MIT-licensed. This directory adds the Linux/systemd bridge port and Axon rig
wiring. See [`CREDITS.md`](CREDITS.md).
