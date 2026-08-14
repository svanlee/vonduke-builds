"""
jarvis/tools.py — Tool implementations for the Jarvis brain.

Each tool is a plain Python function. The brain (jarvis/brain.py) maps
Claude tool_use calls to these functions at runtime.
"""

import json
import os
import pathlib
import sqlite3
import subprocess
import time

BASE_DIR = pathlib.Path(__file__).parent.parent

# ── Tool definitions (schema for Claude) ────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "get_bot_state",
        "description": (
            "Get the current state of the AKSUMAEL bot: what it's doing, "
            "its current goal, last reward, camera status, tick count, "
            "and whether it's running."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "inject_goal",
        "description": (
            "Send a new goal to AKSUMAEL. The bot will execute it at the "
            "specified priority (1=low, 10=critical, preempts everything). "
            "Use this to direct the bot: explore, mine_diamonds, find_food, "
            "return_to_base, craft_crafting_table, find_and_chop_tree, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "snake_case goal name, e.g. explore, mine_diamonds",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority 1-10 (default 5). 10 = critical.",
                    "minimum": 1,
                    "maximum": 10,
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason for logging (optional).",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "clear_goals",
        "description": "Clear the bot's current goal and goal stack so it goes idle.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_recent_memory",
        "description": (
            "Retrieve the bot's recent episodic memory — what it has been "
            "doing, what goals it pursued, what outcomes it got. Useful for "
            "answering questions about what happened recently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of recent episodes to retrieve (default 10).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_hive_status",
        "description": (
            "Get the status of all known Hive nodes: robocar-hub (main machine), "
            "AK-01 (RoboCar at 192.168.0.202), Axon voice hub. "
            "Returns reachability and any available health data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "run_shell",
        "description": (
            "Run a shell command on robocar-hub and return stdout+stderr. "
            "Use for system queries, file reads, service checks. "
            "NOT for destructive operations without confirming with the user first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 10).",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "restart_bot",
        "description": "Restart the AKSUMAEL bot service cleanly.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_system_telemetry",
        "description": (
            "Get real-time system health for the Victus laptop: CPU %, frequency, "
            "temperature, RAM used/total, disk, GPU utilization, GPU memory used, "
            "GPU temperature, GPU power draw (watts), and battery status. "
            "Use this when asked about machine health, power consumption, "
            "whether the GPU is being used, or how hot things are running."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_usb_devices",
        "description": (
            "List all USB devices currently plugged into the machine (via lsusb), "
            "plus any serial/video devices visible under /dev/. "
            "Use when asked what's connected, whether the KB2040 is attached, "
            "or whether /dev/ttyUSB0 / /dev/video2 are present."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_camera_status",
        "description": (
            "Check the status of all video capture devices (/dev/video*), "
            "with a detailed health check of /dev/video2 (the AKSUMAEL capture card). "
            "Use when asked whether the camera is working, what's on video2, "
            "or if there are capture card issues."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "gpio_read_pins",
        "description": (
            "Read current GPIO chip and pin state (read-only, safe). "
            "Use when asked about GPIO state, what pins are active, "
            "or whether a GPIO device is responding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "read_display_info",
        "description": (
            "Get information about connected displays — which screens are connected, "
            "their resolution, and refresh rate. Use when asked about displays, "
            "whether Minecraft is on a connected screen, or display setup."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "send_keystrokes",
        "description": (
            "Send keyboard input to the currently focused application using xdotool. "
            "Use for typing text, pressing key combinations, or sending control keys. "
            "Examples: type 'hello world', press 'ctrl+c', press 'Return', "
            "press 'F9'. This sends input to whatever window has focus — "
            "be careful when Minecraft is the focused window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["type", "key"],
                    "description": "'type' sends characters, 'key' sends a key name or combo.",
                },
                "value": {
                    "type": "string",
                    "description": "Text to type, or key name (e.g. 'Return', 'ctrl+c', 'F9').",
                },
                "window_title": {
                    "type": "string",
                    "description": "Optional: target a specific window by title fragment.",
                },
            },
            "required": ["action", "value"],
        },
    },
    {
        "name": "capture_screen",
        "description": (
            "Take a screenshot of the current display and save it to /tmp/jarvis_screen.png. "
            "Returns the path and basic image info. Use when asked what's on screen, "
            "whether Minecraft is showing something specific, or to visually check state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "self_eval",
        "description": (
            "Compute performance metrics from AURORA and learn_log: avg reward per goal, "
            "total episodes recorded, top/bottom performing goals, episode count by env. "
            "Use to answer 'how am I doing?', 'what's working?', or to decide which goal "
            "to inject next based on past performance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "switch_domain",
        "description": (
            "Switch the bot's active training domain. Writes to data/attention_focus.json "
            "and data/axon_mode.txt. Available domains: training, minecraft, robocar, vehicle. "
            "Use when you want AKSUMAEL to focus on a different environment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "enum": ["training", "minecraft", "robocar", "vehicle"],
                    "description": "The domain to switch to.",
                },
            },
            "required": ["domain"],
        },
    },
    {
        "name": "query_aurora",
        "description": (
            "Query the AURORA episode store for recent history, stats, or entity facts. "
            "Use to recall what the system has done, what outcomes were seen, or what "
            "is known about a specific entity (goal, location, mob, item)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {
                    "type": "string",
                    "description": "Filter by env: jarvis, overseer, minecraft, training, or omit for all.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max episodes to return (default 10).",
                    "minimum": 1,
                    "maximum": 50,
                },
                "entity": {
                    "type": "string",
                    "description": "Optional: recall facts about a specific entity by name.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "build_preferences",
        "description": (
            "Build a DPO/preference training dataset from learn_log.jsonl. "
            "Pairs high-reward ticks (chosen) with low-reward ticks (rejected) per goal. "
            "Writes to data/learning/preferences.jsonl. Use periodically to generate "
            "training data for reward model or policy fine-tuning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_skills",
        "description": (
            "List all learned skills from data/skills/. Returns skill name, avg_reward, "
            "success_count, last_used, and trigger objects. Use to understand what "
            "capabilities AKSUMAEL has mastered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "activate_skill",
        "description": (
            "Activate a skill by injecting its name as a goal. The bot will pursue the "
            "skill's steps on the next tick. Use when you want AKSUMAEL to apply a "
            "specific learned skill immediately (e.g. mine_diamond_ore, chop_tree, fish)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Exact skill name from list_skills (e.g. mine_diamond_ore).",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're activating this skill (for logging).",
                },
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "send_to_ak01",
        "description": (
            "Send a shell command to AK-01 (the RoboCar Pi at 192.168.0.104) via SSH. "
            "Use to check AK-01 status, read sensor data, or send ROS2 topic messages. "
            "SSH key must already be installed on AK-01 (ros@192.168.0.104)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run on AK-01.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 5).",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "propose_improvement",
        "description": (
            "Propose an improvement to your own behavior or the AKSUMAEL system. "
            "Proposals are written to data/jarvis_improvements.json and applied as "
            "additional context in future LLM calls. This is how you evolve yourself — "
            "use it when you notice a recurring problem, a better way to handle a goal, "
            "or a pattern in AURORA that suggests a change. Be specific: proposals are "
            "injected verbatim as behavioral guidelines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "area": {
                    "type": "string",
                    "description": "One of: system_prompt, goal_priority, tool_behavior, skill_selection, other",
                    "enum": ["system_prompt", "goal_priority", "tool_behavior", "skill_selection", "other"],
                },
                "suggestion": {
                    "type": "string",
                    "description": "The concrete improvement to make (specific and actionable).",
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this will improve outcomes (optional but helpful for review).",
                },
            },
            "required": ["area", "suggestion"],
        },
    },
    {
        "name": "score_goal",
        "description": (
            "Score a goal using the locally-trained reward model. Returns a scalar "
            "reward estimate based on preference pairs learned from AKSUMAEL's own "
            "experience. Use before inject_goal to rank candidate goals — higher score "
            "means the local model thinks this goal tends to produce good outcomes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "Goal name to score (e.g. mine_diamonds, explore)"},
                "action": {"type": "string", "description": "Optional: action context string"},
                "belief": {"type": "string", "description": "Optional: current belief/state context"},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "lora_infer",
        "description": (
            "Run inference with the locally fine-tuned LoRA model. This model was "
            "trained on AKSUMAEL's own experience (preference pairs from learn_log). "
            "Use it to get a fast local prediction of the best action for a goal, "
            "without an API call. Only works after lora_train has been run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The goal to get an action prediction for"},
                "belief": {"type": "string", "description": "Optional: current belief/state context"},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "lora_train",
        "description": (
            "Start a LoRA fine-tuning run using the current preference pairs. "
            "Trains a local TinyLlama model with SFT on data/learning/preferences.jsonl. "
            "Auto-selects GPU or CPU based on available VRAM. "
            "Check data/learning/lora_stats.json for results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_steps": {"type": "integer", "description": "Training steps (default 200)"},
            },
            "required": [],
        },
    },
    {
        "name": "spawn_subagent",
        "description": (
            "Spawn a focused sub-agent to handle a complex task that would take too long "
            "inline or requires deep research, planning, or multi-step analysis. "
            "The sub-agent runs a separate Claude call with a focused system prompt and returns "
            "a structured result. Use this for: crafting plans, threat assessment, terrain "
            "analysis, skill design, exploration planning, or anything needing sustained focus. "
            "You remain responsive during sub-agent execution."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear description of what the sub-agent should do and return.",
                },
                "domain": {
                    "type": "string",
                    "description": "Domain focus: minecraft, crafting, exploration, threat, hardware, planning, analysis",
                    "enum": ["minecraft", "crafting", "exploration", "threat", "hardware", "planning", "analysis"],
                },
                "context": {
                    "type": "string",
                    "description": "Relevant context to give the sub-agent (bot state, goal, inventory, etc.)",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max tokens for the sub-agent response (default 600).",
                },
            },
            "required": ["task", "domain"],
        },
    },
]


# ── Tool implementations ──────────────────────────────────────────────────────

def get_bot_state() -> dict:
    health_path = "/tmp/aksumael_health.txt"
    try:
        with open(health_path) as f:
            lines = f.read().strip().splitlines()
        state = {}
        for line in lines:
            if ':' in line:
                k, v = line.split(':', 1)
                state[k.strip()] = v.strip()
        # Check if process is running
        result = subprocess.run(
            ["pgrep", "-f", "venv/bin/python3.*main.py"],
            capture_output=True, text=True
        )
        state["running"] = bool(result.stdout.strip())
        state["health_age_sec"] = int(time.time() - os.path.getmtime(health_path))
        return state
    except FileNotFoundError:
        return {"running": False, "error": "no health log — bot may not have started yet"}
    except Exception as e:
        return {"error": str(e)}


def inject_goal(goal: str, priority: int = 5, reason: str = "") -> dict:
    """Write goal to injected_goals.json in {"queue": [...]} format.
    authority maps priority 1-10 → 1-5 (GoalStack.check_injected_goals scale)."""
    injected_path = BASE_DIR / "data" / "injected_goals.json"

    # Load existing queue
    try:
        with open(injected_path) as f:
            data = json.load(f)
        queue = data.get("queue", []) if isinstance(data, dict) else list(data)
    except Exception:
        queue = []

    authority = max(1, min(5, round(priority / 2)))  # map 1-10 → 1-5
    entry = {
        "goal": goal,
        "authority": authority,
        "source": f"jarvis:{reason or 'voice'}",
        "ts": time.time(),
    }
    queue.append(entry)

    with open(injected_path, "w") as f:
        json.dump({"queue": queue}, f, indent=2)

    # Persist intent so overseer can re-inject after restarts
    intent_path = BASE_DIR / "data" / "last_intent.json"
    try:
        with open(intent_path, "w") as f:
            json.dump({
                "goal": goal,
                "priority": priority,
                "authority": authority,
                "reason": reason,
                "ts": time.time(),
                "ts_human": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }, f, indent=2)
    except Exception:
        pass

    return {"status": "injected", "goal": goal, "authority": authority}


def clear_goals() -> dict:
    goals_path = BASE_DIR / "data" / "goals.json"
    with open(goals_path, "w") as f:
        json.dump({"current": None, "stack": []}, f)
    return {"status": "cleared"}


def get_recent_memory(n: int = 10) -> dict:
    db_path = BASE_DIR / "data" / "memory.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        rows = conn.execute(
            "SELECT timestamp, fsm_state, goal, action, outcome FROM episodes "
            "ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        episodes = [
            {
                "time": time.strftime("%H:%M:%S", time.localtime(r[0])),
                "state": r[1], "goal": r[2], "action": r[3], "outcome": r[4],
            }
            for r in reversed(rows)
        ]
        return {"episodes": episodes, "count": len(episodes)}
    except Exception as e:
        return {"error": str(e)}


def get_hive_status() -> dict:
    nodes = {
        "robocar-hub": "192.168.0.156",   # HP Victus, RTX 4050 Laptop GPU
        "AK-01": "192.168.0.104",         # Pi 4 queen node (will be RDK X5)
    }
    status = {}
    for name, ip in nodes.items():
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            capture_output=True, text=True
        )
        status[name] = {"reachable": result.returncode == 0, "ip": ip}

    # Add AKSUMAEL health
    status["AKSUMAEL"] = get_bot_state()
    return status


def run_shell(command: str, timeout: int = 10) -> dict:
    # Safety: block obviously destructive commands
    blocked = ["rm -rf", "mkfs", "dd if=", ":(){", "shutdown", "reboot", "halt"]
    if any(b in command for b in blocked):
        return {"error": f"blocked — command matches safety filter: {command}"}
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"command timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


def restart_bot() -> dict:
    result = subprocess.run(
        ["systemctl", "--user", "restart", "aksumael.service"],
        capture_output=True, text=True
    )
    return {"status": "restarted" if result.returncode == 0 else "failed",
            "stderr": result.stderr}


def get_system_telemetry() -> dict:
    """CPU, GPU (NVIDIA), RAM, disk, power — everything on this machine."""
    out: dict = {}

    # ── CPU ───────────────────────────────────────────────────────────────────
    try:
        import psutil
        out["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        out["cpu_freq_mhz"] = psutil.cpu_freq().current if psutil.cpu_freq() else None
        mem = psutil.virtual_memory()
        out["ram_used_gb"] = round(mem.used / 1e9, 2)
        out["ram_total_gb"] = round(mem.total / 1e9, 2)
        out["ram_percent"] = mem.percent
        # Disk
        disk = psutil.disk_usage("/")
        out["disk_used_gb"] = round(disk.used / 1e9, 1)
        out["disk_total_gb"] = round(disk.total / 1e9, 1)
        out["disk_percent"] = disk.percent
        # CPU temp
        try:
            temps = psutil.sensors_temperatures()
            cpu_temps = temps.get("k10temp", temps.get("coretemp", []))
            if cpu_temps:
                out["cpu_temp_c"] = cpu_temps[0].current
        except Exception:
            pass
    except ImportError:
        out["cpu_error"] = "psutil not installed — run: pip install psutil --break-system-packages"

    # ── GPU (NVIDIA) ──────────────────────────────────────────────────────────
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            parts = [p.strip() for p in r.stdout.strip().split(",")]
            out["gpu_name"] = parts[0]
            out["gpu_util_pct"] = parts[1]
            out["gpu_mem_used_mb"] = parts[2]
            out["gpu_mem_total_mb"] = parts[3]
            out["gpu_temp_c"] = parts[4]
            out["gpu_power_w"] = parts[5]
        else:
            out["gpu_error"] = r.stderr.strip()[:200]
    except Exception as e:
        out["gpu_error"] = str(e)

    # ── Battery / power ───────────────────────────────────────────────────────
    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat:
            out["battery_pct"] = bat.percent
            out["battery_plugged"] = bat.power_plugged
            if bat.secsleft and bat.secsleft > 0:
                out["battery_time_min"] = round(bat.secsleft / 60)
    except Exception:
        pass

    return out


def list_usb_devices() -> dict:
    """List USB devices currently connected to this machine."""
    try:
        r = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
        devices = r.stdout.strip().splitlines() if r.returncode == 0 else []
        # Also check /dev/ttyUSB* and /dev/ttyACM* for serial devices
        serial_r = subprocess.run(
            ["bash", "-c", "ls /dev/ttyUSB* /dev/ttyACM* /dev/video* 2>/dev/null"],
            capture_output=True, text=True, timeout=3
        )
        serial_devs = serial_r.stdout.strip().splitlines()
        return {"usb_devices": devices, "serial_video_devs": serial_devs, "count": len(devices)}
    except Exception as e:
        return {"error": str(e)}


def get_camera_status() -> dict:
    """Check which video devices are available and whether the capture card is live."""
    result = {}
    try:
        r = subprocess.run(
            ["bash", "-c", "for d in /dev/video*; do echo \"$d: $(v4l2-ctl -d $d --all 2>/dev/null | grep 'Card type' || echo unknown)\"; done"],
            capture_output=True, text=True, timeout=8
        )
        result["video_devices"] = r.stdout.strip().splitlines()
        # Check health of /dev/video2 specifically (capture card)
        r2 = subprocess.run(
            ["v4l2-ctl", "-d", "/dev/video2", "--all"],
            capture_output=True, text=True, timeout=4
        )
        result["video2_ok"] = r2.returncode == 0
        result["video2_info"] = r2.stdout[:400] if r2.returncode == 0 else r2.stderr[:200]
    except Exception as e:
        result["error"] = str(e)
    return result


def gpio_read_pins() -> dict:
    """Read current GPIO pin states via gpioinfo (safe, read-only)."""
    try:
        r = subprocess.run(["gpioinfo"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            # Summarise — full output is very long
            lines = r.stdout.splitlines()
            chips = [l for l in lines if l.startswith("gpiochip")]
            return {"gpio_chips": chips, "raw_lines": len(lines)}
        return {"error": r.stderr[:200]}
    except FileNotFoundError:
        # Try alternative
        r2 = subprocess.run(["raspi-gpio", "get"], capture_output=True, text=True, timeout=5)
        if r2.returncode == 0:
            return {"gpio_raw": r2.stdout[:1000]}
        return {"error": "gpioinfo not found — not a Pi, or libgpiod not installed"}
    except Exception as e:
        return {"error": str(e)}


def read_display_info() -> dict:
    """Get connected display information (local screens, resolution, refresh)."""
    try:
        # xrandr is the most portable option
        r = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
        )
        if r.returncode == 0:
            # Parse connected displays
            lines = r.stdout.splitlines()
            connected = [l for l in lines if " connected" in l]
            return {"connected_displays": connected, "display_count": len(connected)}
        return {"error": r.stderr[:200]}
    except Exception as e:
        return {"error": str(e)}


def send_keystrokes(action: str, value: str, window_title: str = "") -> dict:
    """Send keyboard input to a window using xdotool."""
    # Safety: block dangerous key combos
    dangerous = ["ctrl+alt+del", "ctrl+alt+f", "super+l"]
    if any(d in value.lower() for d in dangerous):
        return {"error": f"blocked: '{value}' matches safety filter"}
    try:
        env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
        if window_title:
            # Focus the target window first
            subprocess.run(
                ["xdotool", "search", "--name", window_title, "windowfocus"],
                capture_output=True, text=True, timeout=3, env=env
            )
        if action == "type":
            r = subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--", value],
                capture_output=True, text=True, timeout=5, env=env
            )
        else:  # key
            r = subprocess.run(
                ["xdotool", "key", "--clearmodifiers", value],
                capture_output=True, text=True, timeout=5, env=env
            )
        if r.returncode == 0:
            return {"status": "sent", "action": action, "value": value}
        return {"error": r.stderr[:200], "returncode": r.returncode}
    except FileNotFoundError:
        return {"error": "xdotool not installed — run: sudo apt install xdotool"}
    except Exception as e:
        return {"error": str(e)}


def capture_screen() -> dict:
    """Take a screenshot and save to /tmp/jarvis_screen.png."""
    try:
        env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
        path = "/tmp/jarvis_screen.png"
        r = subprocess.run(
            ["scrot", path, "--overwrite"],
            capture_output=True, text=True, timeout=10, env=env
        )
        if r.returncode != 0:
            # fallback: try import (ImageMagick)
            r2 = subprocess.run(
                ["import", "-window", "root", path],
                capture_output=True, text=True, timeout=10, env=env
            )
            if r2.returncode != 0:
                return {"error": f"scrot: {r.stderr[:100]}  import: {r2.stderr[:100]}"}
        import os
        size = os.path.getsize(path)
        return {"path": path, "size_bytes": size, "status": "ok"}
    except Exception as e:
        return {"error": str(e)}


def self_eval() -> dict:
    """Compute performance metrics from AURORA + learn_log."""
    result = {}
    try:
        from memory import aurora_memory
        # Episode counts by env
        import sqlite3, os as _os
        db = _os.path.join('data', 'aurora.db')
        conn = sqlite3.connect(db)
        rows = conn.execute(
            'SELECT env, COUNT(*) FROM episodes GROUP BY env ORDER BY COUNT(*) DESC'
        ).fetchall()
        result['episodes_by_env'] = {r[0]: r[1] for r in rows}
        result['total_episodes'] = sum(v for v in result['episodes_by_env'].values())
        # Goal reward stats from vault
        entities = conn.execute(
            'SELECT name, attributes FROM entities WHERE type="goal" ORDER BY last_seen DESC'
        ).fetchall()
        goal_perf = {}
        for name, attrs_json in entities:
            try:
                attrs = json.loads(attrs_json) if attrs_json else {}
                goal_perf[name] = {
                    'avg_reward': attrs.get('avg_reward'),
                    'samples': attrs.get('sample_count'),
                }
            except Exception:
                pass
        result['goal_performance'] = goal_perf
        if goal_perf:
            ranked = sorted(
                [(g, v['avg_reward']) for g, v in goal_perf.items() if v['avg_reward'] is not None],
                key=lambda x: x[1], reverse=True
            )
            result['best_goal'] = ranked[0][0] if ranked else None
            result['worst_goal'] = ranked[-1][0] if len(ranked) > 1 else None
        conn.close()
    except Exception as e:
        result['error'] = str(e)
    # learn_log line count
    try:
        lp = pathlib.Path('data/learning/learn_log.jsonl')
        result['learn_log_ticks'] = sum(1 for _ in lp.open()) if lp.exists() else 0
    except Exception:
        pass
    return result


def switch_domain(domain: str) -> dict:
    """Switch the active attention domain by writing attention_focus.json."""
    focus_path = BASE_DIR / 'data' / 'attention_focus.json'
    axon_path = BASE_DIR / 'data' / 'axon_mode.txt'
    try:
        focus_path.write_text(json.dumps({'active': domain, 'ts': time.time()}))
        if axon_path.exists():
            axon_path.write_text(domain)
        return {'status': 'switched', 'domain': domain}
    except Exception as e:
        return {'error': str(e)}


def query_aurora(env: str = None, limit: int = 10, entity: str = None) -> dict:
    """Query AURORA for recent episodes and/or entity facts."""
    try:
        from memory import aurora_memory
        result = {}
        if entity:
            result['entity'] = aurora_memory.recall_entity(entity)
            result['facts'] = aurora_memory.recall_facts(entity, limit=10)
        result['episodes'] = aurora_memory.recent(env=env, limit=limit)
        result['stats'] = aurora_memory.stats(env=env)
        return result
    except Exception as e:
        return {'error': str(e)}


# ── Dispatch ──────────────────────────────────────────────────────────────────

TOOL_DISPATCH = {
    "get_bot_state": lambda args: get_bot_state(),
    "inject_goal": lambda args: inject_goal(**args),
    "clear_goals": lambda args: clear_goals(),
    "get_recent_memory": lambda args: get_recent_memory(n=args.get("n", 10)),
    "get_hive_status": lambda args: get_hive_status(),
    "run_shell": lambda args: run_shell(args["command"], args.get("timeout", 10)),
    "restart_bot": lambda args: restart_bot(),
    "get_system_telemetry": lambda args: get_system_telemetry(),
    "list_usb_devices": lambda args: list_usb_devices(),
    "get_camera_status": lambda args: get_camera_status(),
    "gpio_read_pins": lambda args: gpio_read_pins(),
    "read_display_info": lambda args: read_display_info(),
    "send_keystrokes": lambda args: send_keystrokes(args["action"], args["value"], args.get("window_title", "")),
    "capture_screen": lambda args: capture_screen(),
    "self_eval": lambda args: self_eval(),
    "switch_domain": lambda args: switch_domain(args["domain"]),
    "query_aurora": lambda args: query_aurora(args.get("env"), args.get("limit", 10), args.get("entity")),
    "build_preferences": lambda args: _build_preferences(),
    "list_skills": lambda args: list_skills(),
    "activate_skill": lambda args: activate_skill(args["skill_name"], args.get("reason", "")),
    "send_to_ak01": lambda args: send_to_ak01(args["command"], args.get("timeout", 5)),
    "propose_improvement": lambda args: propose_improvement(args["area"], args["suggestion"], args.get("rationale", "")),
    "score_goal": lambda args: score_goal_tool(args["goal"], args.get("action", ""), args.get("belief", "")),
    "lora_infer": lambda args: lora_infer_tool(args["goal"], args.get("belief", "")),
    "lora_train": lambda args: lora_train_tool(args.get("max_steps", 200)),
    "spawn_subagent": lambda args: spawn_subagent(
        args["task"], args["domain"],
        args.get("context", ""), args.get("max_tokens", 600)
    ),
}


def list_skills() -> dict:
    """List all learned skills from data/skills/."""
    skills_dir = BASE_DIR / "data" / "skills"
    skills = []
    try:
        for f in sorted(skills_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                skills.append({
                    "name": data.get("name", f.stem),
                    "avg_reward": data.get("avg_reward"),
                    "success_count": data.get("success_count", 0),
                    "trigger_objects": data.get("trigger_objects", []),
                    "blacklisted": data.get("blacklisted", False),
                })
            except Exception:
                pass
        # Sort by avg_reward descending
        skills.sort(key=lambda s: s.get("avg_reward") or 0, reverse=True)
        return {"skills": skills, "count": len(skills)}
    except Exception as e:
        return {"error": str(e)}


def activate_skill(skill_name: str, reason: str = "") -> dict:
    """Activate a skill by injecting it as a high-priority goal."""
    # Verify skill exists
    skills_dir = BASE_DIR / "data" / "skills"
    skill_file = skills_dir / f"{skill_name}.json"
    if not skill_file.exists():
        # Try partial match
        matches = list(skills_dir.glob(f"*{skill_name}*.json"))
        if not matches:
            return {"error": f"skill '{skill_name}' not found in data/skills/"}
        skill_name = matches[0].stem
    return inject_goal(skill_name, priority=7, reason=f"skill_activation:{reason}")


def send_to_ak01(command: str, timeout: int = 5) -> dict:
    """SSH a command to AK-01 at 192.168.0.104."""
    blocked = ["rm -rf", "shutdown", "reboot", "halt", "mkfs"]
    if any(b in command for b in blocked):
        return {"error": f"blocked: matches safety filter"}
    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
             "ros@192.168.0.104", command],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        return {
            "stdout": result.stdout[:1000],
            "stderr": result.stderr[:300],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"SSH to AK-01 timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


def lora_infer_tool(goal: str, belief: str = "") -> dict:
    """Run inference with the locally fine-tuned LoRA model."""
    try:
        from memory.lora_trainer import infer, LORA_PATH
        if not LORA_PATH.exists():
            return {"error": "LoRA adapter not trained yet", "tip": "run: python3 -m memory.lora_trainer"}
        result = infer(goal, belief)
        return {"goal": goal, "predicted_action": result}
    except Exception as e:
        return {"error": str(e)}


def lora_train_tool(max_steps: int = 200) -> dict:
    """Start a LoRA fine-tuning run from current preference data."""
    try:
        from memory.lora_trainer import train, check_readiness
        readiness = check_readiness()
        if readiness.get("missing_packages"):
            return readiness
        if not readiness.get("ready_to_train"):
            return readiness
        # Run in background thread so this tool returns quickly
        import threading
        def _run():
            result = train(max_steps=max_steps)
            print(f"[LORA] training done: {result}")
        t = threading.Thread(target=_run, daemon=True, name="LoRATrainer")
        t.start()
        return {"status": "training_started", "max_steps": max_steps, "pairs": readiness["preference_pairs"]}
    except Exception as e:
        return {"error": str(e)}


def score_goal_tool(goal: str, action: str = "", belief: str = "") -> dict:
    """Score a goal using the local reward model trained from preference data."""
    try:
        from memory.local_trainer import score_goal, MODEL_PATH
        score = score_goal(goal, action, belief)
        if score is None:
            pairs_path = BASE_DIR / "data" / "learning" / "preferences.jsonl"
            n_pairs = sum(1 for _ in open(pairs_path)) if pairs_path.exists() else 0
            return {"error": "reward model not trained yet", "preference_pairs": n_pairs, "tip": "run: python3 -m memory.local_trainer"}
        return {"goal": goal, "reward_score": score, "model_path": str(MODEL_PATH)}
    except Exception as e:
        return {"error": str(e)}


def _build_preferences() -> dict:
    """Build DPO preference dataset from learn_log."""
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from memory.preference_builder import build_and_save
        return build_and_save()
    except Exception as e:
        return {"error": str(e)}


def propose_improvement(area: str, suggestion: str, rationale: str = "") -> dict:
    """
    Propose an improvement to Jarvis's own behavior, system prompt, or tooling.

    Improvements are written to data/jarvis_improvements.json. The overseer
    reads this file every 5 min and appends accepted proposals to the system
    prompt context — making this the self-improvement loop.

    Args:
        area: One of 'system_prompt', 'goal_priority', 'tool_behavior', 'skill_selection', 'other'
        suggestion: Concrete change to make (be specific — will be injected verbatim as context)
        rationale: Why this change would improve outcomes (used for review)
    """
    imp_path = BASE_DIR / "data" / "jarvis_improvements.json"
    try:
        if imp_path.exists():
            existing = json.loads(imp_path.read_text())
        else:
            existing = {"proposals": [], "applied": []}
        proposal = {
            "ts": time.time(),
            "ts_human": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "area": area,
            "suggestion": suggestion,
            "rationale": rationale,
            "status": "pending",
        }
        existing.setdefault("proposals", []).append(proposal)
        imp_path.write_text(json.dumps(existing, indent=2))
        # Record to AURORA
        try:
            from memory import aurora_memory
            aurora_memory.record(
                env="jarvis",
                action=f"propose_improvement:{area}",
                outcome=suggestion[:200],
                notes=rationale[:200],
                metadata={"area": area},
            )
        except Exception:
            pass
        return {"status": "proposed", "area": area, "total_proposals": len(existing["proposals"])}
    except Exception as e:
        return {"error": str(e)}


def spawn_subagent(task: str, domain: str, context: str = "", max_tokens: int = 600) -> dict:
    """
    Spawn a focused sub-agent (local mesh-llm) for a task requiring sustained analysis.

    Fully local — no API calls. Uses the domain-specific system prompt to give the
    local model a focused persona, then runs a single-turn completion via llm_router.

    Architecture: AKSUMAEL (executive) → spawn_subagent → domain specialist → result
    """
    DOMAIN_PROMPTS = {
        "minecraft": "You are AKSUMAEL's Minecraft specialist. Analyze gameplay situations and suggest optimal strategies, goal sequences, or survival priorities. Be concrete and specific.",
        "crafting": "You are AKSUMAEL's crafting specialist. Given inventory and goals, produce the optimal crafting sequence with exact steps. Reference vanilla Minecraft recipes.",
        "exploration": "You are AKSUMAEL's exploration specialist. Analyze terrain, biome, and structure data to suggest high-value exploration targets and navigation strategies.",
        "threat": "You are AKSUMAEL's threat assessment specialist. Evaluate survival risks (health, hunger, mobs, environment) and recommend immediate priority actions.",
        "hardware": "You are AKSUMAEL's hardware specialist. Analyze device state, sensor readings, and connectivity issues. Suggest diagnostics and fixes.",
        "planning": "You are AKSUMAEL's planning specialist. Given a high-level goal, break it into an ordered sequence of achievable sub-goals with dependencies and estimated completion times.",
        "analysis": "You are AKSUMAEL's data analysis specialist. Examine logs, metrics, and telemetry to surface patterns, anomalies, and actionable insights.",
    }

    system = (
        DOMAIN_PROMPTS.get(domain, DOMAIN_PROMPTS["analysis"])
        + "\n\nYou are operating as a sub-agent of AKSUMAEL. "
        + "Return a concise, structured response — no markdown headers, no filler. "
        + "Focus on actionable output."
    )

    user_msg = f"Task: {task}"
    if context:
        user_msg += f"\n\nContext:\n{context[:800]}"

    # Route entirely through local mesh-llm — no cloud calls ever.
    # The system parameter is passed as a leading system message (see llm_router._try_local).
    try:
        from core.llm_router import call_local_llm
        result_text = call_local_llm(
            prompt=user_msg,
            max_tokens=max_tokens,
            timeout=60.0,
            system=system,
        )
        if result_text is None:
            return {
                "error": "local mesh-llm unavailable — check llama-server at localhost:9337",
                "domain": domain,
                "task": task,
            }

        # Log to AURORA
        try:
            from memory import aurora_memory
            aurora_memory.record(
                env="subagent",
                action=f"{domain}:{task[:80]}",
                outcome=result_text[:200],
                metadata={"domain": domain, "task": task[:200], "model": "local"},
            )
        except Exception:
            pass

        return {
            "domain": domain,
            "task": task,
            "result": result_text,
            "model": "local",
        }
    except Exception as e:
        return {"error": str(e), "domain": domain, "task": task}


def call_tool(name: str, args: dict) -> str:
    """Call a tool by name and return JSON string result."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        result = fn(args)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
