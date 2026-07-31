"""
core/escalation_ipc.py — file-based IPC for supervisor escalation.

When the FSM supervisor escalates (Tier 1 ESCALATE ruling), it can't call
Axon directly — they're separate processes. Instead:

  FSM side  : call request_human(verdict)  → writes data/supervisor_esc.json
  Axon side : call poll_and_respond(...)   → reads file, speaks, listens, writes answer
  FSM side  : call await_response(...)     → blocks up to timeout, returns True/False

Flow:
  [FSM] escalation fires
    → request_human() writes {status:"pending", ...}
    → await_response() polls for up to TIMEOUT_S
  [Axon] poll_and_respond() fires on next tick
    → announces via TTS
    → records LISTEN_SEC of audio
    → transcribes → writes {status:"resolved", approved:true/false}
  [FSM] await_response() returns the decision
    → True  → supervisor allows the action (human override)
    → False → supervisor refuses (default safe)
"""

import json
import os
import time

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESC_PATH     = os.path.join(BASE_DIR, "data", "supervisor_esc.json")
TIMEOUT_S    = 12.0   # FSM waits up to this long for a human answer
POLL_INTERVAL = 0.1   # FSM poll rate while waiting


def request_human(verdict) -> None:
    """FSM side: write an escalation request to disk."""
    os.makedirs(os.path.dirname(ESC_PATH), exist_ok=True)
    with open(ESC_PATH, "w") as f:
        json.dump({
            "status":    "pending",
            "proposed":  verdict.proposed,
            "reason":    verdict.reason,
            "requested_at": time.time(),
        }, f)
    print(f"[ESCALATION-IPC] wrote pending request: {verdict.proposed} — {verdict.reason}")


def await_response(timeout_s: float = TIMEOUT_S) -> bool:
    """
    FSM side: block until Axon writes a resolution or timeout expires.
    Returns True if human approved, False otherwise.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with open(ESC_PATH) as f:
                data = json.load(f)
            if data.get("status") == "resolved":
                approved = bool(data.get("approved", False))
                print(f"[ESCALATION-IPC] resolved: approved={approved}")
                # Clear the file so next escalation starts fresh
                os.remove(ESC_PATH)
                return approved
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(POLL_INTERVAL)

    print(f"[ESCALATION-IPC] timeout after {timeout_s}s — refusing by default")
    try:
        os.remove(ESC_PATH)
    except OSError:
        pass
    return False


def poll_pending() -> dict | None:
    """
    Axon side: check if a pending escalation request exists.
    Returns the request dict or None.
    """
    try:
        with open(ESC_PATH) as f:
            data = json.load(f)
        if data.get("status") == "pending":
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def resolve(approved: bool) -> None:
    """Axon side: write the human decision back to disk."""
    try:
        with open(ESC_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data["status"]      = "resolved"
    data["approved"]    = approved
    data["resolved_at"] = time.time()
    with open(ESC_PATH, "w") as f:
        json.dump(data, f)
    print(f"[ESCALATION-IPC] wrote resolution: approved={approved}")
