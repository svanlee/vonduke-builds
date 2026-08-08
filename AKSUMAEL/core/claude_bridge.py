# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Claude Bridge                      ║
# ║  Loopback REST window into a running bot, so an       ║
# ║  operator (or Claude in a shell) can read state and   ║
# ║  steer goals without tailing logs or editing JSON.    ║
# ╚══════════════════════════════════════════════════════╝
#
# Runs as a daemon thread inside the main bot process (started from
# main.py). Binds 127.0.0.1 only — 7682 is ttyd, 8765 is the DisplayThread
# frame server, so this takes 7683. No auth by design: loopback-only, and
# anything that can reach it can already read the repo.
#
# Deliberately *disk-backed* rather than holding references to the live
# GoalStack/pipeline objects. core/runtime.py's run() is one long function
# whose state (vision_ok, pipeline, goals) lives in locals, and reaching
# into it would mean threading a handle through the whole loop. Everything
# below is already published to disk by the running bot each tick, so the
# bridge stays a pure reader and can start before run() does — nothing to
# initialise, nothing to break if the loop dies.
#
# The one writer is POST /goal, which appends to data/injected_goals.json
# in exactly the {"queue": [...]} shape axon/hub.py._enqueue_goal writes,
# so memory.goals.GoalStack.check_injected_goals() drains a Claude-sent
# goal identically to a voice command or a hive assignment.

import json
import logging
import os
import threading
import time

import config

BRIDGE_VERSION = '1.0.0'
DEFAULT_PORT   = 7683

# ── Sources ─────────────────────────────────────────────────────────
MONOLOGUE_PATH = 'data/cognitive/inner_monologue.json'
BELIEF_PATH    = 'data/cognitive/belief_state.json'
GOALS_PATH     = 'data/goals.json'
INJECTED_PATH  = 'data/injected_goals.json'   # memory/goals.py owns the schema

# Mirrors core/runtime.py's HEALTH_LOG_PATH (kept as a literal rather than
# imported — importing core.runtime pulls in the whole vision/YOLO stack).
HEALTH_PATH    = '/tmp/aksumael_health.txt'

# The systemd unit sets StandardOutput=append:/tmp/aksumael_live.log, so the
# log path is a deployment detail rather than a config.py value. Overridable
# for anyone running the bot by hand into a different file.
LOG_PATH       = os.environ.get('AKSUMAEL_LOG', '/tmp/aksumael_live.log')

# Source of truth is axon/hub.py's VALID_GOALS — duplicated here so the
# bridge doesn't import axon.hub, which pulls in the audio/TTS stack at
# module scope. Reject-with-list on an unknown goal, so a caller driving
# this blind from curl gets the valid set back in the error body.
VALID_GOALS = frozenset({
    "find_and_chop_tree", "mine_stone", "mine_iron", "mine_diamonds",
    "craft_wood_pickaxe", "craft_stone_pickaxe", "craft_iron_pickaxe",
    "explore", "rebuild_fort", "return_to_base",
    "dig_up", "escape_underground",
})

# POST /goal takes priority 1-3; memory/goals.py grades injected goals on a
# 1-5 "authority" scale (<=2 lands only while the bot is idle, >=4 overrides
# a survival-critical goal). Map onto the meaningful bands, skipping 1 and 4
# — they behave identically to 2 and 5 respectively.
_PRIORITY_TO_AUTHORITY = {1: 2, 2: 3, 3: 5}

MAX_LOG_LINES      = 1000   # cap so /log can't be used to slurp the whole file
MAX_MONOLOGUE      = 10     # most recent thoughts returned by /state
_LOG_TAIL_BYTES    = 256 * 1024

_STARTED_AT = None          # set by start()
_inject_lock = threading.Lock()   # serialises the injected-goals read-modify-write


# ── Helpers ─────────────────────────────────────────────────────────
def _read_json(path, default=None):
    """Load a JSON file, returning `default` if it's missing or mid-write.

    The bot rewrites these files in place while ticking, so a read landing
    between truncate and flush sees invalid JSON. That's transient — report
    it as absent rather than 500ing the whole /state call.
    """
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _age_s(path):
    """Seconds since `path` was last written, or None if it doesn't exist.

    Every snapshot below is served with its age: the bot writes them at
    different cadences (health every 60 ticks, monologue every ~8s) and a
    stale file is indistinguishable from a live one without this.
    """
    try:
        return round(time.time() - os.path.getmtime(path), 1)
    except OSError:
        return None


def _read_health():
    """Parse core/runtime.py's `key: value` health snapshot into a dict."""
    try:
        with open(HEALTH_PATH) as f:
            raw = f.read()
    except OSError:
        return {}
    out = {}
    for line in raw.splitlines():
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        out[key.strip()] = value.strip()
    return out


def _tail(path, lines):
    """Last `lines` lines of `path`, reading only the tail of the file."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - _LOG_TAIL_BYTES))
            chunk = f.read()
    except OSError:
        return None
    text = chunk.decode('utf-8', errors='replace')
    if size > _LOG_TAIL_BYTES:
        # Drop the first line — the seek almost certainly landed mid-line.
        text = text.partition('\n')[2]
    return text.splitlines()[-lines:]


def _node():
    """Deployment identity — which box this bot is, per config.NODE_NAME.

    getattr-with-default rather than a direct read so an older checkout
    without these constants degrades to "unknown" instead of 500ing every
    /state call; the bridge is a diagnostic tool and is least useful when
    it refuses to answer.
    """
    return {
        'node_name': getattr(config, 'NODE_NAME', 'unknown'),
        'hardware_expected': getattr(config, 'NODE_HARDWARE', {}),
    }


def _state():
    """Assemble the /state payload from the bot's on-disk snapshots."""
    monologue = _read_json(MONOLOGUE_PATH, []) or []
    goals     = _read_json(GOALS_PATH, {}) or {}
    injected  = _read_json(INJECTED_PATH, {}) or {}
    health    = _read_health()

    camera = health.get('camera', '')
    # runtime writes "NONE (vision-less)" when CaptureThread never settled on
    # a device; anything else is a real /dev/videoN it's actually reading.
    vision_ok = bool(camera) and not camera.startswith('NONE')

    return {
        'node': _node(),
        'monologue': {
            'recent': monologue[-MAX_MONOLOGUE:],
            'count': len(monologue),
            'age_s': _age_s(MONOLOGUE_PATH),
        },
        # Legacy: core/cognitive.py removed BeliefState (it was a write-only
        # no-op nothing read back), so this file is frozen at whatever the
        # last version that still wrote it left behind. Served because it was
        # asked for — check age_s before believing any of it.
        'belief_state': {
            'data': _read_json(BELIEF_PATH),
            'age_s': _age_s(BELIEF_PATH),
            'note': 'legacy file — BeliefState was removed from core/cognitive.py',
        },
        'active_goals': {
            'current': goals.get('current'),
            'stack': goals.get('stack', []),
            # Goals sent but not yet drained by the runtime tick.
            'pending_injected': injected.get('queue', []),
            'age_s': _age_s(GOALS_PATH),
        },
        'vision_ok': vision_ok,
        'hardware_status': {
            'camera': camera or 'unknown',
            'ttyUSB0': health.get('ttyUSB0', 'unknown'),
            'vision_route': health.get('vision_route', 'unknown'),
            'tick': health.get('tick'),
            'last_reward': health.get('last_reward'),
            'health_age_s': _age_s(HEALTH_PATH),
        },
        'uptime_s': round(time.time() - _STARTED_AT, 1) if _STARTED_AT else None,
    }


def _enqueue_goal(goal, priority):
    """Append one goal to data/injected_goals.json. Returns the queue depth.

    Same read-modify-write append axon/hub.py uses. GoalStack deletes the
    file once it drains it, so a concurrent drain can at worst lose a goal
    queued in the same instant — acceptable here (the caller sees the depth
    and can re-send), and the lock keeps two HTTP requests from clobbering
    each other, which is the realistic collision.
    """
    authority = _PRIORITY_TO_AUTHORITY[priority]
    with _inject_lock:
        existing = _read_json(INJECTED_PATH, {}) or {}
        queue = existing.get('queue', []) if isinstance(existing, dict) else []
        if not isinstance(queue, list):
            queue = []
        queue.append({
            'goal': goal,
            'reason': 'claude_bridge',
            'authority': authority,
            'received_at': time.time(),
        })
        os.makedirs(os.path.dirname(INJECTED_PATH) or '.', exist_ok=True)
        with open(INJECTED_PATH, 'w') as f:
            json.dump({'queue': queue}, f)
    print(f'[BRIDGE] queued goal "{goal}" (priority={priority} authority={authority})')
    return authority, len(queue)


# ── App ─────────────────────────────────────────────────────────────
def _build_app():
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.get('/health')
    def health():
        return jsonify({
            'ok': True,
            # Cheapest possible "am I talking to the right box?" check —
            # /state carries the full node block, but a bare ping should
            # already be enough to catch a wrong-instance mistake.
            'node_name': getattr(config, 'NODE_NAME', 'unknown'),
            'version': BRIDGE_VERSION,
            'uptime_s': round(time.time() - _STARTED_AT, 1) if _STARTED_AT else None,
            'pid': os.getpid(),
        })

    @app.get('/state')
    def state():
        return jsonify(_state())

    @app.get('/log')
    def log():
        try:
            lines = int(request.args.get('lines', 50))
        except ValueError:
            return jsonify({'error': 'lines must be an integer'}), 400
        lines = max(1, min(MAX_LOG_LINES, lines))
        tail = _tail(LOG_PATH, lines)
        if tail is None:
            return jsonify({'error': f'log not readable: {LOG_PATH}'}), 404
        return jsonify({
            'path': LOG_PATH,
            'lines': tail,
            'returned': len(tail),
            'age_s': _age_s(LOG_PATH),
        })

    @app.post('/goal')
    def goal():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({'error': 'expected a JSON object body'}), 400

        name = body.get('goal')
        if not name or not isinstance(name, str):
            return jsonify({'error': 'missing "goal" (string)'}), 400
        if name not in VALID_GOALS:
            return jsonify({
                'error': f'unknown goal: {name}',
                'valid_goals': sorted(VALID_GOALS),
            }), 400

        try:
            priority = int(body.get('priority', 2))
        except (TypeError, ValueError):
            return jsonify({'error': 'priority must be an integer 1-3'}), 400
        if priority not in _PRIORITY_TO_AUTHORITY:
            return jsonify({'error': 'priority must be 1, 2, or 3'}), 400

        try:
            authority, depth = _enqueue_goal(name, priority)
        except OSError as e:
            return jsonify({'error': f'could not write goal queue: {e}'}), 500

        return jsonify({
            'queued': True,
            'goal': name,
            'priority': priority,
            'authority': authority,
            'queue_depth': depth,
            # Injected goals are graded, not commands — memory/goals.py drops
            # a low-authority goal outright if the bot is mid-task. Say so,
            # rather than letting "queued" read as "accepted".
            'note': 'drained on the next runtime tick; may be dropped if '
                    'authority is too low for the goal in progress',
        })

    return app


def _serve(app, port):
    try:
        app.run(host='127.0.0.1', port=port, threaded=True,
                debug=False, use_reloader=False)
    except Exception as e:
        # A dead bridge must never take the bot down with it.
        print(f'[BRIDGE] server stopped: {e}')


def start(port=DEFAULT_PORT):
    """Start the bridge on a daemon thread. Returns True if it came up.

    Never raises: Flask missing, port already taken, or any other failure
    degrades to a log line and a disabled bridge. The bot is the product;
    this is a window onto it.
    """
    global _STARTED_AT
    try:
        app = _build_app()
    except ImportError as e:
        print(f'[BRIDGE] flask unavailable ({e}) — bridge disabled')
        return False
    except Exception as e:
        print(f'[BRIDGE] init failed: {e} — bridge disabled')
        return False

    # Werkzeug logs a line per request at INFO; left on, every poll would
    # land in the bot's own log and drown the tick output /log serves.
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    _STARTED_AT = time.time()
    threading.Thread(target=_serve, args=(app, port),
                     name='claude-bridge', daemon=True).start()
    print(f'[BRIDGE] Claude bridge → http://localhost:{port}/  '
          f'(/health /state /goal /log)  node={getattr(config, "NODE_NAME", "unknown")}')
    return True
