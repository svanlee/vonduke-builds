"""
jarvis/overseer.py — Jarvis Overseer: proactive background intelligence.

This is what makes AKSUMAEL "Jarvis-level" rather than "bot with LLM attached."
The overseer runs continuously, monitors all subsystems, detects problems, and
acts on them without being asked. Voice and external queries are just one input
channel to this same brain.

Architecture:
    JarvisOverseer (background thread)
        ↓ polls every POLL_INTERVAL seconds
        ↓ reads system state (service health, bot state, telemetry)
        ↓ scores "interestingness" — only escalates to LLM when something changed
        ↓ LLM decides: act, speak, or log silently
        ↓ executes actions via the same tool system voice uses

This inverts the old model (voice → brain) to the correct one (brain → everything).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import threading
import time
from typing import Optional

BASE_DIR = pathlib.Path(__file__).parent.parent

# How often the overseer polls (seconds). Keep low enough to catch crashes fast.
POLL_INTERVAL = 30

# How often the overseer may call the LLM unprompted (seconds).
# Prevents token burn on boring steady state.
MIN_LLM_INTERVAL = 120

# How many consecutive silent polls before the overseer does a mandatory
# "I'm still watching" LLM check-in (even if nothing changed).
SILENT_CHECKIN_AFTER = 20   # 20 × 30s = 10 minutes

# Path to overseer log
OVERSEER_LOG = BASE_DIR / "data" / "overseer.log"


def _log(msg: str):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] [OVERSEER] {msg}"
    print(line)
    try:
        OVERSEER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(OVERSEER_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Situation snapshot ────────────────────────────────────────────────────────

def _snap_service() -> dict:
    """Is aksumael.service running?"""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "aksumael.service"],
            capture_output=True, text=True, timeout=5,
        )
        active = r.stdout.strip() == "active"
    except Exception:
        active = False
    return {"service_active": active}


def _snap_bot_state() -> dict:
    """Read the bot's last-written state file."""
    state_path = BASE_DIR / "data" / "state.json"
    try:
        with open(state_path) as f:
            return json.load(f)
    except Exception:
        return {}


def _snap_video() -> dict:
    """Check if capture card is present."""
    import os
    devs = [d for d in ["/dev/video0", "/dev/video1", "/dev/video2"] if os.path.exists(d)]
    return {"video_devices": devs, "capture_card": "/dev/video2" in devs}


def _snap_usb() -> dict:
    """Is the KB2040 on ttyUSB0?"""
    import os
    return {"kb2040": os.path.exists("/dev/ttyUSB0")}


def _get_situation() -> dict:
    """Full current snapshot — cheap, no LLM."""
    snap = {}
    snap.update(_snap_service())
    snap.update(_snap_bot_state())
    snap.update(_snap_video())
    snap.update(_snap_usb())
    snap["ts"] = time.time()
    return snap


# ── Change detection ──────────────────────────────────────────────────────────

def _situation_key(snap: dict) -> str:
    """Stable string that captures things that matter for change detection."""
    return json.dumps({
        "svc": snap.get("service_active"),
        "goal": snap.get("goal"),
        "kb2040": snap.get("kb2040"),
        "capture_card": snap.get("capture_card"),
    }, sort_keys=True)


def _interestingness(prev: dict, curr: dict) -> tuple[int, list[str]]:
    """
    Score how interesting the current situation is vs. previous.
    Returns (score 0-10, list of notable changes).
    score ≥ 3 → worth asking the LLM about.
    score ≥ 7 → urgent (service crash, hardware loss).
    """
    score = 0
    notes = []

    # Service went down
    if prev.get("service_active") and not curr.get("service_active"):
        score += 8
        notes.append("aksumael.service stopped unexpectedly")

    # Service came back up
    if not prev.get("service_active") and curr.get("service_active"):
        score += 3
        notes.append("aksumael.service is now running")

    # KB2040 disconnected
    if prev.get("kb2040") and not curr.get("kb2040"):
        score += 5
        notes.append("KB2040 disconnected from ttyUSB0")

    # KB2040 reconnected
    if not prev.get("kb2040") and curr.get("kb2040"):
        score += 3
        notes.append("KB2040 reconnected on ttyUSB0")

    # Capture card appeared / disappeared
    if prev.get("capture_card") and not curr.get("capture_card"):
        score += 4
        notes.append("capture card /dev/video2 dropped")

    if not prev.get("capture_card") and curr.get("capture_card"):
        score += 4
        notes.append("capture card /dev/video2 appeared — can switch to live YOLO feed")

    # Goal changed
    prev_goal = prev.get("goal")
    curr_goal = curr.get("goal")
    if prev_goal and curr_goal and prev_goal != curr_goal:
        score += 2
        notes.append(f"goal changed: {prev_goal} → {curr_goal}")

    # Reward went sharply negative
    reward = curr.get("last_reward", 0)
    if isinstance(reward, (int, float)) and reward < -0.5:
        score += 3
        notes.append(f"reward sharply negative: {reward:.2f}")

    return score, notes


# ── Overseer thread ───────────────────────────────────────────────────────────

class JarvisOverseer(threading.Thread):
    """
    Background thread that watches all subsystems and acts without being asked.

    Usage:
        overseer = JarvisOverseer()
        overseer.start()
        # runs until overseer.stop() or process exits

    The overseer uses the same JarvisBrain and tool system as the voice handler.
    It is NOT a separate LLM instance — same history, same tools.
    """

    def __init__(self, speak_fn=None):
        super().__init__(name="JarvisOverseer", daemon=True)
        self._stop_event = threading.Event()
        self._speak = speak_fn   # optional TTS callback: fn(text) → None
        self._prev_snap: dict = {}
        self._last_llm_ts: float = 0.0
        self._silent_polls: int = 0
        self._brain = None

    def _get_brain(self):
        if self._brain is None:
            from jarvis.brain import get_brain
            self._brain = get_brain()
        return self._brain

    def _say(self, text: str):
        """Speak proactively if TTS is available, always log."""
        _log(f"SPEAK: {text}")
        if self._speak:
            try:
                self._speak(text)
            except Exception as e:
                _log(f"TTS error: {e}")

    def _maybe_restart_service(self):
        """Restart aksumael.service if it's down."""
        _log("attempting service restart...")
        try:
            subprocess.run(
                ["systemctl", "--user", "start", "aksumael.service"],
                timeout=10,
            )
            time.sleep(5)
            snap = _snap_service()
            if snap.get("service_active"):
                _log("service restarted successfully")
                return True
            else:
                _log("service restart failed")
                return False
        except Exception as e:
            _log(f"restart error: {e}")
            return False

    def _maybe_fix_uvcvideo(self):
        """Reload uvcvideo module to recover /dev/video2."""
        _log("reloading uvcvideo to recover capture card...")
        try:
            subprocess.run(["sudo", "modprobe", "-r", "uvcvideo"], timeout=10)
            time.sleep(2)
            subprocess.run(["sudo", "modprobe", "uvcvideo"], timeout=10)
            time.sleep(2)
            snap = _snap_video()
            recovered = snap.get("capture_card", False)
            _log(f"uvcvideo reload: capture_card={'recovered' if recovered else 'still missing'}")
            return recovered
        except Exception as e:
            _log(f"uvcvideo reload error: {e}")
            return False

    def _handle_event(self, score: int, notes: list[str], snap: dict):
        """Decide how to respond to a notable situation change."""
        now = time.time()
        since_last_llm = now - self._last_llm_ts

        notes_str = "; ".join(notes)
        _log(f"event (score={score}): {notes_str}")

        # ── Auto-remediation (no LLM needed for clear-cut fixes) ──────────
        def _aurora_event(action: str, outcome: str):
            try:
                from memory import aurora_memory
                aurora_memory.record(
                    env='overseer',
                    action=action,
                    outcome=outcome,
                    metadata={'score': score, 'notes': notes_str},
                )
            except Exception as _ae:
                _log(f"aurora record error: {_ae}")

        if "aksumael.service stopped unexpectedly" in notes:
            self._say("Service went down. Attempting restart.")
            ok = self._maybe_restart_service()
            if ok:
                self._say("Back online.")
                _aurora_event('restart_service', 'success')
            else:
                self._say("Restart failed. Manual intervention may be needed.")
                _aurora_event('restart_service', 'failed')
            self._silent_polls = 0
            return

        if "capture card /dev/video2 dropped" in notes:
            _log("auto-fixing uvcvideo drop")
            recovered = self._maybe_fix_uvcvideo()
            if recovered:
                self._say("Capture card recovered.")
            _aurora_event('fix_uvcvideo', 'recovered' if recovered else 'failed')
            self._silent_polls = 0

        if "KB2040 disconnected from ttyUSB0" in notes:
            self._say("KB2040 disconnected. Input control is disabled.")
            _aurora_event('kb2040_disconnect', 'detected')
            self._silent_polls = 0

        if "KB2040 reconnected on ttyUSB0" in notes:
            self._say("KB2040 back on ttyUSB0. Input control restored.")
            _aurora_event('kb2040_reconnect', 'detected')
            self._silent_polls = 0

        # ── LLM escalation for genuinely interesting situations ────────────
        if score >= 3 and since_last_llm >= MIN_LLM_INTERVAL:
            self._call_llm_with_context(notes_str, snap)

    def _call_llm_with_context(self, notes: str, snap: dict):
        """Ask the brain to reason about the current situation."""
        now = time.time()
        prompt = (
            f"[OVERSEER ALERT — do not wait to be asked] "
            f"Situation change detected: {notes}. "
            f"Current state: service_active={snap.get('service_active')}, "
            f"goal={snap.get('goal')}, kb2040={snap.get('kb2040')}, "
            f"capture_card={snap.get('capture_card')}. "
            f"Assess and act if needed. Respond in one to two sentences max."
        )
        response = None
        try:
            brain = self._get_brain()
            response = brain.respond(prompt)
            if response and response.strip():
                self._say(response)
        except Exception as e:
            _log(f"LLM call failed: {e}")
        # Record the overseer LLM decision as an AURORA episode
        try:
            from memory import aurora_memory
            aurora_memory.record(
                env='overseer',
                action=f'llm_alert: {notes[:200]}',
                outcome=(response or 'no_response')[:300],
                metadata={
                    'service_active': snap.get('service_active'),
                    'goal': snap.get('goal'),
                    'kb2040': snap.get('kb2040'),
                },
            )
        except Exception as _ae:
            _log(f"aurora record error: {_ae}")
        self._last_llm_ts = now

    def _periodic_checkin(self, snap: dict):
        """Periodic unprompted LLM check-in after long silence.
        The brain sees recent AURORA episodes so it can notice patterns and act."""
        now = time.time()
        # Pull recent learn_log for richer context
        learn_summary = ''
        try:
            learn_path = BASE_DIR / 'data' / 'learning' / 'learn_log.jsonl'
            if learn_path.exists():
                import json as _json
                lines = learn_path.read_text().strip().splitlines()[-5:]
                recent = [_json.loads(l) for l in lines if l]
                learn_summary = ' | '.join(
                    f"tick={r.get('tick')} goal={r.get('goal')} r={r.get('reward',0):.2f}"
                    for r in recent
                )
        except Exception:
            pass

        prompt = (
            f"[OVERSEER CHECKIN — proactive autonomous decision] "
            f"System quiet for {self._silent_polls * POLL_INTERVAL // 60} minutes. "
            f"Bot: service_active={snap.get('service_active')}, "
            f"mode=TRAINING, goal={snap.get('goal')}, tick={snap.get('tick')}. "
            f"Recent learning: {learn_summary or 'unavailable'}. "
            f"You have full tool access. Check bot state, assess what the system needs, "
            f"and take one useful action or report a specific observation. "
            f"One to two sentences. Act now."
        )
        response = None
        try:
            brain = self._get_brain()
            response = brain.respond(prompt)
            if response:
                _log(f"checkin: {response}")
        except Exception as e:
            _log(f"checkin LLM failed: {e}")
        # Record the checkin as an AURORA episode
        try:
            from memory import aurora_memory
            aurora_memory.record(
                env='overseer',
                action=f'periodic_checkin: tick={snap.get("tick")} goal={snap.get("goal")}',
                outcome=(response or 'no_response')[:300],
                metadata={'silent_polls': self._silent_polls, 'tick': snap.get('tick')},
            )
        except Exception as _ae:
            _log(f"aurora record error: {_ae}")
        self._last_llm_ts = now

    def _distill_learn_log(self):
        """Read learn_log.jsonl and write reward statistics per goal into the
        AURORA vault as facts. This closes the reward → self-knowledge loop:
        Jarvis can then say 'explore averages +0.4 reward, craft_crafting_table
        averages +0.2' and use that to guide goal decisions."""
        try:
            learn_path = BASE_DIR / 'data' / 'learning' / 'learn_log.jsonl'
            if not learn_path.exists():
                return
            import json as _json
            from collections import defaultdict
            lines = learn_path.read_text().strip().splitlines()[-500:]  # last 500 ticks
            goal_rewards: dict = defaultdict(list)
            for line in lines:
                try:
                    r = _json.loads(line)
                    g = r.get('goal')
                    reward = r.get('reward')
                    if g and reward is not None:
                        goal_rewards[g].append(float(reward))
                except Exception:
                    continue
            if not goal_rewards:
                return
            from memory import aurora_memory
            for goal, rewards in goal_rewards.items():
                avg = sum(rewards) / len(rewards)
                aurora_memory.remember_entity(goal, type='goal', attributes={
                    'avg_reward': round(avg, 3),
                    'sample_count': len(rewards),
                    'max_reward': round(max(rewards), 3),
                    'min_reward': round(min(rewards), 3),
                })
                aurora_memory.remember_fact(
                    entity=goal,
                    predicate='avg_reward_last_500_ticks',
                    value=round(avg, 3),
                    confidence=min(1.0, len(rewards) / 50),
                )
            _log(f"distilled learn_log: {len(goal_rewards)} goals → AURORA vault")
        except Exception as e:
            _log(f"distill_learn_log error: {e}")

    def _apply_improvements(self):
        """Read pending improvement proposals from jarvis_improvements.json and
        apply them to the brain's system prompt context. This is the self-improvement
        loop — Jarvis proposes, overseer applies."""
        imp_path = BASE_DIR / "data" / "jarvis_improvements.json"
        if not imp_path.exists():
            return
        try:
            data = json.loads(imp_path.read_text())
            proposals = data.get("proposals", [])
            pending = [p for p in proposals if p.get("status") == "pending"]
            if not pending:
                return
            brain = self._get_brain()
            applied = []
            for p in pending:
                # Append to brain's behavioral context
                area = p.get("area", "other")
                suggestion = p.get("suggestion", "")
                if suggestion:
                    # Add to brain's extra context (stored in brain._extra_context)
                    if not hasattr(brain, "_extra_context"):
                        brain._extra_context = []
                    brain._extra_context.append(
                        f"[SELF-IMPROVEMENT:{area.upper()}] {suggestion}"
                    )
                    p["status"] = "applied"
                    p["applied_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    applied.append(suggestion[:80])
                    _log(f"applied improvement [{area}]: {suggestion[:80]}")
            if applied:
                data["applied"] = data.get("applied", []) + applied
                imp_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            _log(f"apply_improvements error: {e}")

    def _load_training_domain_context(self) -> str:
        """Load summaries from training domain result files for startup context."""
        domain_ctx = []
        training_dir = BASE_DIR / "data" / "training"
        # Also check root-level day_*.md files
        search_dirs = [BASE_DIR, training_dir] if training_dir.exists() else [BASE_DIR]
        seen = set()
        for d in search_dirs:
            for f in sorted(d.glob("day_*.md")):
                if f.name in seen:
                    continue
                seen.add(f.name)
                try:
                    text = f.read_text()[:800]  # first 800 chars per domain
                    domain_ctx.append(f"=== {f.stem} ===\n{text}")
                except Exception:
                    pass
        if not domain_ctx:
            return ""
        combined = "\n\n".join(domain_ctx[:5])  # cap at 5 files
        return f"\n\nTraining domain results from prior sessions:\n{combined}"

    def _startup_briefing(self):
        """On first start, ask the brain to assess system state using AURORA context.
        This gives Jarvis awareness of what happened in prior sessions."""
        try:
            from memory import aurora_memory
            ep_count = 0
            try:
                import sqlite3, os as _os
                conn = sqlite3.connect(_os.path.join('data', 'aurora.db'))
                ep_count = conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0]
                conn.close()
            except Exception:
                pass
            # Load training domain results for cross-session awareness
            domain_ctx = self._load_training_domain_context()
            prompt = (
                f"[JARVIS STARTUP] System just started. "
                f"AURORA has {ep_count} recorded episodes from prior sessions. "
                + (f"Prior training results available.{domain_ctx[:400]} " if domain_ctx else "")
                + f"Check the current bot state, review your recent history via query_aurora, "
                f"run self_eval to see which goals perform best, then inject the highest-reward "
                f"goal if the bot is idle. Respond with a one-sentence status. Act now."
            )
            brain = self._get_brain()
            response = brain.respond(prompt)
            if response:
                _log(f"startup briefing: {response}")
                if self._speak:
                    self._speak(response)
            aurora_memory.record(
                env='overseer',
                action='startup_briefing',
                outcome=(response or 'no_response')[:300],
                metadata={'ep_count': ep_count},
            )
        except Exception as e:
            _log(f"startup briefing error: {e}")

    def run(self):
        _log("overseer started")
        # Warm up with an initial snapshot
        try:
            self._prev_snap = _get_situation()
        except Exception as e:
            _log(f"initial snapshot failed: {e}")
            self._prev_snap = {}
        # Give the bot 60s to fully start before the first briefing
        self._stop_event.wait(60)
        if not self._stop_event.is_set():
            self._startup_briefing()

        _distill_counter = 0
        while not self._stop_event.is_set():
            try:
                curr = _get_situation()
                score, notes = _interestingness(self._prev_snap, curr)

                if score > 0:
                    self._handle_event(score, notes, curr)
                    self._silent_polls = 0
                else:
                    self._silent_polls += 1

                # Distill learn_log into AURORA vault + preference pairs every ~5 minutes
                _distill_counter += 1
                if _distill_counter >= 10:  # 10 × 30s = 5 min
                    self._distill_learn_log()
                    try:
                        from memory.preference_builder import build_and_save
                        result = build_and_save()
                        _log(f"preference pairs: {result.get('preference_pairs', 0)} "
                             f"({result.get('goals_covered', 0)} goals)")
                    except Exception as _pe:
                        _log(f"preference build error: {_pe}")
                    # Apply any pending self-improvement proposals to brain context
                    self._apply_improvements()
                    # Retrain local reward model if stale (non-blocking best-effort)
                    try:
                        from memory.local_trainer import _run_periodic as _train
                        _result = _train()
                        if not _result.get("skipped"):
                            _log(f"reward model retrained: loss={_result.get('final_loss')} pairs={_result.get('n_pairs')}")
                    except Exception as _te:
                        _log(f"local trainer error: {_te}")
                    # Auto-trigger LoRA fine-tune when enough pairs exist (non-blocking)
                    try:
                        from memory.lora_trainer import check_readiness, _run_lora_if_ready
                        _lr = check_readiness()
                        if _lr.get("ready_to_train") and _lr.get("packages_ok") and _lr.get("cuda_available"):
                            _run_lora_if_ready()
                    except Exception as _le:
                        pass  # LoRA is optional — fail silently
                    _distill_counter = 0

                # Periodic silent check-in
                if self._silent_polls >= SILENT_CHECKIN_AFTER:
                    self._periodic_checkin(curr)
                    self._silent_polls = 0

                self._prev_snap = curr

            except Exception as e:
                _log(f"overseer tick error: {e}")

            self._stop_event.wait(POLL_INTERVAL)

        _log("overseer stopped")

    def stop(self):
        self._stop_event.set()


# ── Singleton ─────────────────────────────────────────────────────────────────

_overseer: Optional[JarvisOverseer] = None


def get_overseer(speak_fn=None) -> JarvisOverseer:
    global _overseer
    if _overseer is None:
        _overseer = JarvisOverseer(speak_fn=speak_fn)
    return _overseer


def start_overseer(speak_fn=None) -> JarvisOverseer:
    """Start the overseer if not already running. Call once at bot startup."""
    ov = get_overseer(speak_fn=speak_fn)
    if not ov.is_alive():
        ov.start()
        _log("overseer thread launched")
    return ov
