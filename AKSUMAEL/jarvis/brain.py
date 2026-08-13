"""
jarvis/brain.py — Jarvis conversational brain.

Routes all voice input through Claude (claude-fable-5 or best available)
with tool_use. Replaces the rigid rule-based voice parser for free-form
natural language control of AKSUMAEL and the Hive.

Usage:
    brain = JarvisBrain()
    response_text = brain.respond("mine some diamonds")
    response_text = brain.respond("what's the bot doing right now?")
    response_text = brain.respond("stop everything and go back to base")
"""

import json
import os
import pathlib
import time

BASE_DIR = pathlib.Path(__file__).parent.parent

# Best available model — Fable 5 for capability, fall back gracefully.
# claude-fable-5 is the primary; if unavailable the router will fall back.
JARVIS_MODEL   = "claude-fable-5"
FALLBACK_MODEL = "claude-opus-4-5"   # solid fallback
MAX_TOOL_ROUNDS = 4                   # max tool-use iterations per response
MAX_TOKENS      = 400                 # keep spoken responses concise

from core.identity import AKSUMAEL_IDENTITY, JARVIS_VOICE_PERSONA

SYSTEM_PROMPT = (
    AKSUMAEL_IDENTITY
    + JARVIS_VOICE_PERSONA
    + """
You have broad sensor and control access via tools:
- Bot state, goal injection, goal clearing, episodic memory
- System telemetry: CPU, GPU, RAM, disk, battery, temperatures, power draw
- USB/serial devices: KB2040 on ttyUSB0, capture card on /dev/video2
- Camera status, GPIO pins (read-only), display info
- Keyboard injection via xdotool, screenshot to /tmp/jarvis_screen.png
- Shell: arbitrary commands on robocar-hub (safety-filtered)
- Bot restart, sub-agent spawning for focused tasks

Key facts:
- Minecraft runs autonomously via YOLO + ByteTrack + DINOv2 ReID + FSM + LLM cognition
- Voice mode is PTT (F9) — game audio bleeds into mic, always-on VAD doesn't work
- Goal names are snake_case: explore, mine_diamonds, find_food, return_to_base, find_and_chop_tree, craft_crafting_table
- Use spawn_subagent for tasks requiring deep research, planning, or multi-step analysis
  so this voice thread stays responsive"""
)


HISTORY_PATH = BASE_DIR / "data" / "jarvis_history.json"
HISTORY_KEEP = 20   # max turns to persist


class JarvisBrain:
    """Stateful brain with conversation history and tool_use loop.
    History persists across restarts via data/jarvis_history.json."""

    def __init__(self, api_key: str | None = None):
        self._client = None
        self._api_key = api_key or self._load_key()
        self._history: list[dict] = self._load_history()
        self._model = JARVIS_MODEL

    def _load_key(self) -> str | None:
        key_file = os.path.expanduser("~/.config/anthropic/key")
        try:
            with open(key_file) as f:
                return f.read().strip()
        except Exception:
            return os.environ.get("ANTHROPIC_API_KEY")

    def _load_history(self) -> list:
        """Load conversation history from disk (last HISTORY_KEEP turns)."""
        try:
            with open(HISTORY_PATH) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data[-HISTORY_KEEP:]
        except Exception:
            pass
        return []

    def _save_history(self):
        """Persist conversation history to disk."""
        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_PATH, 'w') as f:
                json.dump(self._history[-HISTORY_KEEP:], f, indent=2, default=str)
        except Exception as e:
            print(f'[JARVIS] history save error: {e}')

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                raise RuntimeError("anthropic package not installed — run: pip install anthropic")
        return self._client

    def _build_system_prompt(self) -> str:
        """System prompt augmented with live AURORA memory context."""
        try:
            from memory import aurora_memory
            # Recent autonomous episodes — what Jarvis has done and what happened
            episodes = aurora_memory.recent(limit=8)
            ep_lines = []
            for ep in reversed(episodes):
                ep_lines.append(f"  [{ep['env']}] {ep['timestamp']} | {ep['action'][:60]} → {ep['outcome'][:60]}")
            ep_block = '\n'.join(ep_lines) if ep_lines else '  (none yet)'
            # Entity/fact context (what the world knows)
            world_ctx = aurora_memory.context_for_llm(max_tokens=200)
            memory_section = (
                f"\n\n## AURORA Memory (your past actions and their outcomes)\n{ep_block}"
                + (f"\n\n## Known entities\n{world_ctx}" if world_ctx else '')
            )
        except Exception:
            memory_section = ''

        # Self-improvement context — proposals applied by the overseer
        improvement_section = ''
        try:
            extra = getattr(self, '_extra_context', [])
            if extra:
                improvement_section = '\n\n## Self-Improvement Guidelines (from your own proposals)\n' + '\n'.join(f'- {e}' for e in extra[-10:])
        except Exception:
            pass

        return SYSTEM_PROMPT + memory_section + improvement_section

    def respond(self, user_text: str, timeout: float = 30.0) -> str:
        """
        Process a voice utterance and return the spoken response string.
        Runs the tool-use loop internally — may call tools before answering.
        """
        from jarvis.tools import TOOL_SCHEMAS, call_tool

        self._history.append({"role": "user", "content": user_text})

        client = self._get_client()
        messages = list(self._history)
        system_prompt = self._build_system_prompt()

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                resp = client.messages.create(
                    model=self._model,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
            except Exception as e:
                # Try fallback model
                if self._model != FALLBACK_MODEL:
                    print(f"[JARVIS] {self._model} failed ({e}), trying {FALLBACK_MODEL}")
                    self._model = FALLBACK_MODEL
                    try:
                        resp = client.messages.create(
                            model=self._model,
                            max_tokens=MAX_TOKENS,
                            system=system_prompt,
                            tools=TOOL_SCHEMAS,
                            messages=messages,
                        )
                    except Exception as e2:
                        err = f"Both models failed: {e2}"
                        print(f"[JARVIS] {err}")
                        self._history.append({"role": "assistant", "content": "I'm having trouble connecting right now."})
                        return "I'm having trouble connecting right now."
                else:
                    self._history.append({"role": "assistant", "content": "I'm having trouble connecting right now."})
                    return "I'm having trouble connecting right now."

            # Extract text and tool calls from response
            text_parts = []
            tool_calls = []
            for block in resp.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if not tool_calls:
                # Final answer
                answer = " ".join(text_parts).strip()
                self._history.append({"role": "assistant", "content": answer})
                # Trim history to last HISTORY_KEEP turns to avoid token bloat
                if len(self._history) > HISTORY_KEEP:
                    self._history = self._history[-HISTORY_KEEP:]
                self._save_history()
                return answer

            # Execute tool calls and continue the loop
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for tc in tool_calls:
                print(f"[JARVIS] tool: {tc.name}({json.dumps(tc.input)[:80]})")
                result_str = call_tool(tc.name, tc.input)
                print(f"[JARVIS] result: {result_str[:120]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result_str,
                })
                # Record every tool call as a Jarvis episode in AURORA
                try:
                    from memory import aurora_memory
                    aurora_memory.record(
                        env='jarvis',
                        action=f'{tc.name}({json.dumps(tc.input)[:120]})',
                        outcome=result_str[:300],
                        notes=f'prompt: {user_text[:100]}',
                        metadata={'tool': tc.name, 'model': self._model},
                    )
                except Exception as _ae:
                    print(f'[JARVIS] aurora record error: {_ae}')
            messages.append({"role": "user", "content": tool_results})

        # Shouldn't reach here
        return "I ran into a loop — please try again."

    def clear_history(self):
        """Reset conversation context and wipe persisted history."""
        self._history.clear()
        self._save_history()


# ── Singleton for voice thread to import ─────────────────────────────────────
_brain: JarvisBrain | None = None


def get_brain() -> JarvisBrain:
    global _brain
    if _brain is None:
        _brain = JarvisBrain()
    return _brain
