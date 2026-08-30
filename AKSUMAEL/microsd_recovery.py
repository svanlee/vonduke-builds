#!/usr/bin/env python3
"""
microsd_recovery.py — MicroSD Diagnostic & Recovery Assistant

Hits the existing mesh-llm service (localhost:9337) for reasoning.
The LLM recommends; the user confirms; the wrapper executes.
No destructive command runs without the user typing the exact device path.

Usage:
    python3 microsd_recovery.py
"""

import json
import re
import subprocess
import sys
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────
LLM_URL   = "http://localhost:9337/v1/chat/completions"
LLM_MODEL = "qwen3"   # whatever mesh-llm serves; adjust if needed

SYSTEM_PROMPT = """You are a diagnostic assistant for microSD card recovery on Linux.
You have access to these tools (the wrapper calls them on your recommendation):

  check_dmesg()          — runs: dmesg | tail -50
  list_block_devices()   — runs: lsblk -o NAME,SIZE,TYPE,MOUNTPOINT && sudo fdisk -l
  raw_read_test(device)  — runs: sudo dd if=DEVICE of=/dev/null bs=1M count=100 status=progress
  image_device(device, out_path) — runs: sudo ddrescue -d -r3 DEVICE OUT_PATH OUT_PATH.log
  recover_files(image_path)      — runs: sudo photorec IMAGE_PATH

Rules you must follow:
1. Always start with check_dmesg + list_block_devices before anything else.
2. Never suggest raw_read_test, image_device, or recover_files unless the user has
   typed the exact device path (e.g. /dev/sdb) themselves. Do not infer or guess it.
3. If dmesg or lsblk output is ambiguous, ask the user to confirm the device before proceeding.
4. Explain findings in plain language before recommending the next step.
5. Always recommend imaging with ddrescue before any file recovery — never run photorec
   against the raw device.

When you want the wrapper to run a tool, output a line in this exact format:
  TOOL: tool_name(arg1, arg2)
Otherwise just reply in plain text."""

# ── LLM call ─────────────────────────────────────────────────────────────────

def ask_llm(messages: list) -> str:
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 512,
    }).encode()
    req = urllib.request.Request(
        LLM_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM error: {e}]"

# ── Safe subprocess runner ────────────────────────────────────────────────────

def run_cmd(cmd: list, timeout: int = 60) -> str:
    """Run a command; return combined stdout+stderr."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        out = (result.stdout + result.stderr).strip()
        return out[:4000] if len(out) > 4000 else out  # truncate for LLM
    except subprocess.TimeoutExpired:
        return "[command timed out]"
    except Exception as e:
        return f"[error running command: {e}]"

# ── Tool implementations ──────────────────────────────────────────────────────

def check_dmesg() -> str:
    print("  → running: dmesg | tail -50")
    return run_cmd(["bash", "-c", "dmesg | tail -50"])


def list_block_devices() -> str:
    print("  → running: lsblk + fdisk -l")
    lsblk = run_cmd(["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,LABEL"])
    fdisk = run_cmd(["sudo", "fdisk", "-l"])
    return f"=== lsblk ===\n{lsblk}\n\n=== fdisk -l ===\n{fdisk}"


def raw_read_test(device: str) -> str:
    """100 MB raw read sanity check — non-destructive."""
    cmd = ["sudo", "dd", f"if={device}", "of=/dev/null",
           "bs=1M", "count=100", "status=progress"]
    print(f"  → running: {' '.join(cmd)}")
    return run_cmd(cmd, timeout=120)


def image_device(device: str, out_path: str) -> str:
    """ddrescue imaging — reads device, never writes to it."""
    log_path = out_path + ".log"
    cmd = ["sudo", "ddrescue", "-d", "-r3", device, out_path, log_path]
    print(f"  → running: {' '.join(cmd)}")
    print("  (this may take a long time — ddrescue will show progress)")
    return run_cmd(cmd, timeout=7200)


def recover_files(image_path: str) -> str:
    """Run photorec against the image (not raw device)."""
    cmd = ["sudo", "photorec", image_path]
    print(f"  → running: {' '.join(cmd)}")
    print("  (photorec is interactive — follow its prompts in this terminal)")
    # photorec is interactive; just exec it directly
    try:
        subprocess.run(cmd)
        return "[photorec session ended]"
    except Exception as e:
        return f"[error: {e}]"

# ── Safety gate ───────────────────────────────────────────────────────────────

DESTRUCTIVE_TOOLS = {"raw_read_test", "image_device", "recover_files"}
DEVICE_RE = re.compile(r"/dev/[a-z]+\d*")


def confirm_destructive(tool_name: str, args: list, user_messages: list) -> bool:
    """
    Only allow a destructive tool if:
    1. The device/path appears verbatim in recent user messages (not just LLM output).
    2. The user types an explicit confirmation with the device path.
    """
    # Collect all user-typed text from history
    user_text = " ".join(
        m["content"] for m in user_messages if m["role"] == "user"
    )
    # The first arg should be a device or image path
    target = args[0] if args else ""
    if not target:
        print("[SAFETY] Tool requires a device/path argument.")
        return False

    # Check user explicitly mentioned this path themselves
    if target not in user_text:
        print(f"[SAFETY] '{target}' was not typed by you — only paths you name are accepted.")
        print("         If this is the right device, type it yourself to confirm.")
        return False

    # Extra confirmation for write-capable tools
    if tool_name in ("image_device", "recover_files"):
        print(f"\n⚠  CONFIRMATION REQUIRED for {tool_name}")
        print(f"   Target: {target}")
        if tool_name == "image_device":
            print(f"   Output: {args[1] if len(args) > 1 else '(none specified)'}")
        print(f"   Type exactly: yes, {target}")
        answer = input("   Your answer: ").strip()
        if answer != f"yes, {target}":
            print("[SAFETY] Confirmation did not match. Command cancelled.")
            return False

    return True


# ── Tool dispatch ─────────────────────────────────────────────────────────────

TOOL_MAP = {
    "check_dmesg":      (check_dmesg,       False),
    "list_block_devices": (list_block_devices, False),
    "raw_read_test":    (raw_read_test,      True),
    "image_device":     (image_device,       True),
    "recover_files":    (recover_files,      True),
}

TOOL_RE = re.compile(r"TOOL:\s*(\w+)\(([^)]*)\)")


def parse_and_run_tool(llm_reply: str, user_messages: list) -> str | None:
    """If the LLM's reply contains a TOOL: line, validate and run it."""
    match = TOOL_RE.search(llm_reply)
    if not match:
        return None
    tool_name = match.group(1).strip()
    raw_args  = match.group(2).strip()
    args = [a.strip().strip("'\"") for a in raw_args.split(",") if a.strip()]

    if tool_name not in TOOL_MAP:
        return f"[unknown tool: {tool_name}]"

    fn, is_destructive = TOOL_MAP[tool_name]

    if is_destructive:
        if not confirm_destructive(tool_name, args, user_messages):
            return "[tool cancelled by safety gate]"

    print(f"\n[running {tool_name}]")
    try:
        result = fn(*args)
    except TypeError as e:
        result = f"[wrong args for {tool_name}: {e}]"
    return result

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  MicroSD Diagnostic & Recovery Assistant")
    print("  LLM: mesh-llm @ localhost:9337")
    print("  Type 'quit' to exit.")
    print("=" * 60)
    print()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    user_messages: list = []  # track only user turns for safety gate

    # Kick off with a greeting from the LLM
    messages.append({"role": "user", "content":
                     "Hello. I have a microSD card that may need recovery. "
                     "Please start with the diagnostic sequence."})
    user_messages.append(messages[-1])

    while True:
        print("Apex [thinking...]")
        reply = ask_llm(messages)

        # Strip any TOOL: line from the display text
        display = TOOL_RE.sub("", reply).strip()
        if display:
            print(f"\nAssistant: {display}\n")

        # Try to run a tool if one was recommended
        tool_result = parse_and_run_tool(reply, user_messages)
        if tool_result:
            print(f"\n[tool output]\n{tool_result}\n")
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user",
                             "content": f"[tool output]\n{tool_result}"})
            user_messages.append(messages[-1])
            # Let the LLM interpret the result
            continue

        messages.append({"role": "assistant", "content": reply})

        # Get next user input
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            sys.exit(0)

        if user_input.lower() in ("quit", "exit", "q"):
            print("Exiting.")
            sys.exit(0)

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        user_messages.append(messages[-1])


if __name__ == "__main__":
    main()
