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

SYSTEM_PROMPT = """You are JARVIS — an advanced AI assistant integrated into AKSUMAEL, Scott's autonomous AI platform. AKSUMAEL is a Minecraft bot running on a gaming laptop (robocar-hub) connected to a hive of AI nodes including AK-01 (a RoboCar) and an Axon voice hub.

Your personality: calm, precise, proactive. Brief spoken responses — one to three sentences maximum unless detail is explicitly requested. You have access to tools to check what the bot is doing, inject goals, inspect memory, and run shell commands on robocar-hub.

Key facts:
- The bot is AKSUMAEL, running Minecraft autonomously via YOLO vision + FSM + LLM cognition
- KB2040 microcontroller emulates keyboard/mouse HID for in-game control
- Voice mode is PTT (push-to-talk, F9 key) due to game audio bleed
- You respond via text-to-speech so keep answers short and spoken-word natural
- When injecting goals, use snake_case: explore, mine_diamonds, find_food, return_to_base, find_and_chop_tree, craft_crafting_table, gather_resources
- If asked what the bot is doing, always check get_bot_state first — don't guess

Do not use markdown, bullet points, or headers in your responses — speak naturally."""


class JarvisBrain:
    """Stateful brain with conversation history and tool_use loop."""

    def __init__(self, api_key: str | None = None):
        self._client = None
        self._api_key = api_key or self._load_key()
        self._history: list[dict] = []
        self._model = JARVIS_MODEL

    def _load_key(self) -> str | None:
        key_file = os.path.expanduser("~/.config/anthropic/key")
        try:
            with open(key_file) as f:
                return f.read().strip()
        except Exception:
            return os.environ.get("ANTHROPIC_API_KEY")

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                raise RuntimeError("anthropic package not installed — run: pip install anthropic")
        return self._client

    def respond(self, user_text: str, timeout: float = 30.0) -> str:
        """
        Process a voice utterance and return the spoken response string.
        Runs the tool-use loop internally — may call tools before answering.
        """
        from jarvis.tools import TOOL_SCHEMAS, call_tool

        self._history.append({"role": "user", "content": user_text})

        client = self._get_client()
        messages = list(self._history)

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                resp = client.messages.create(
                    model=self._model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
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
                            system=SYSTEM_PROMPT,
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
                # Trim history to last 20 turns to avoid token bloat
                if len(self._history) > 20:
                    self._history = self._history[-20:]
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
            messages.append({"role": "user", "content": tool_results})

        # Shouldn't reach here
        return "I ran into a loop — please try again."

    def clear_history(self):
        """Reset conversation context."""
        self._history.clear()


# ── Singleton for voice thread to import ─────────────────────────────────────
_brain: JarvisBrain | None = None


def get_brain() -> JarvisBrain:
    global _brain
    if _brain is None:
        _brain = JarvisBrain()
    return _brain
