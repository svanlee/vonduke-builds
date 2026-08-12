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
    injected_path = BASE_DIR / "data" / "injected_goals.json"
    goals_path = BASE_DIR / "data" / "goals.json"

    # Load existing injected goals
    try:
        with open(injected_path) as f:
            existing = json.load(f)
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []

    entry = {"goal": goal, "priority": priority, "source": f"jarvis:{reason or 'voice'}"}
    existing.append(entry)

    with open(injected_path, "w") as f:
        json.dump(existing, f, indent=2)

    return {"status": "injected", "goal": goal, "priority": priority}


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
        "robocar-hub": "192.168.0.156",
        "AK-01": "192.168.0.202",
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
}


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
