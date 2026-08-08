---
name: session-2026-08-01c
description: Controller trigger fix deployed; bot in HUMAN mode auto-start; Dispatch rendering broken
metadata:
  type: project
---

## State as of 2026-08-01 session

**Trigger fix committed** (44d0434): `_norm_trigger` now clamps `lo=0` for signed axes.
Xbox BT triggers were reading 127 at rest → permanent sneak+sprint. Now read 0 at rest.

**Auto-HUMAN-mode commit** (b330d1d): `human_mode = True` in `__init__` so bot starts in HUMAN mode without needing Start press. TEMPORARY — revert to `False` after trigger fix is confirmed working.

**Bot status**: Running (HUMAN mode), but controller is disconnected after restart. User needs to press Xbox button to reconnect BT. HumanAssist probes every 5s and will auto-detect.

**Dispatch rendering bug**: SendUserMessage says "delivered" but doesn't render in Cowork Dispatch UI this session. Code tab tasks DO render.

**Why:** After context compaction, the Dispatch rendering breaks. Known issue from prior sessions.
