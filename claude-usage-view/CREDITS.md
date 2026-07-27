# Credits

The ESP32 firmware (`firmware/ClaudeUsageMonitor/`), the `build.sh`/`flash.sh`
wrappers, `docs/HARDWARE.md`, and the original macOS bridge
(`bridge/claude_usage_bridge.macos.py`) are the work of Rafael, from
[`rafelton/claude-usage-view-esp32`](https://github.com/rafelton/claude-usage-view-esp32)
(MIT).

Vendored here as the Axon rig deployment of that project. Changes made in this
directory:

- `bridge/claude_usage_bridge.py` — Linux port of the bridge. Reads the Claude
  Code OAuth token from `~/.claude/.credentials.json` (with a `secret-tool`
  fallback) instead of the macOS Keychain. Otherwise byte-for-byte the same
  usage-fetch / simplify / serve logic.
- `bridge/claude-usage-bridge.service` + `bridge/install.sh` — systemd `--user`
  service replacing the launchd plist, matching AKSUMAEL's `axon.service`
  convention on the rig.
- `flash.sh` — serial-port autodetect extended to Linux `/dev/ttyUSB*` /
  `/dev/ttyACM*`.
- `firmware/.../config.h`, `wifi_setup.cpp`, `config_local.h.example` — bridge
  URL / portal copy point at the Axon rig instead of a Mac.
