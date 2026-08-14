"""
jarvis/brain.py — Jarvis conversational brain.

Routes all voice input through the LOCAL mesh-llm server (localhost:9337,
OpenAI-compatible API) with tool_use. Falls back to Anthropic only if the
local server is unreachable.

This keeps AKSUMAEL fully local for everything Jarvis does — no cloud
dependency for real-time voice control or goal injection.
"""

import json
import os
import pathlib
import time

BASE_DIR = pathlib.Path(__file__).parent.parent

# ── Model config ──────────────────────────────────────────────────────────────
LOCAL_URL        = "http://localhost:9337/v1"
LOCAL_MODEL      = "local"            # llama-server accepts any string here
ANTHROPIC_MODEL  = "claude-fable-5"  # fallback only — used when local is down
FALLBACK_MODEL   = "claude-opus-4-5"
MAX_TOOL_ROUNDS  = 5
MAX_TOKENS       = 600                # more headroom for thinking + answer
TIMEOUT          = 45.0

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
- Goal names are snake_case: explore, mine_diamonds, find_food, return_to_base,
  find_and_chop_tree, craft_crafting_table
- Use spawn_subagent for tasks requiring deep research or multi-step analysis
  so this voice thread stays responsive
- You run LOCALLY on the RTX 4050. Prefer decisive, efficient answers.
"""
)

HISTORY_PATH = BASE_DIR / "data" / "jarvis_history.json"
HISTORY_KEEP = 20


def _anthropic_to_openai_tools(anthropic_schemas: list) -> list:
    """Convert Anthropic tool schema format to OpenAI function-calling format."""
    out = []
    for s in anthropic_schemas:
        out.append({
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", ""),
                "parameters": s.get("input_schema", {"type": "object", "properties": {}}),
            }
        })
    return out


class JarvisBrain:
    """Stateful brain with conversation history and tool-use loop.

    Routing priority:
      1. Local mesh-llm (localhost:9337) — OpenAI-compatible, fully offline
      2. Anthropic Claude — fallback only when local server is unreachable

    History persists across restarts via data/jarvis_history.json.
    """

    def __init__(self, api_key: str | None = None):
        self._api_key     = api_key or self._load_key()
        self._history: list[dict] = self._load_history()
        self._local_client  = None
        self._claude_client = None
        self._using_local   = True   # optimistic — probe on first call

    # ── Key loading ───────────────────────────────────────────────────────────
    def _load_key(self) -> str | None:
        key_file = os.path.expanduser("~/.config/anthropic/key")
        try:
            with open(key_file) as f:
                return f.read().strip()
        except Exception:
            return os.environ.get("ANTHROPIC_API_KEY")

    # ── History ───────────────────────────────────────────────────────────────
    def _load_history(self) -> list:
        try:
            with open(HISTORY_PATH) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data[-HISTORY_KEEP:]
        except Exception:
            pass
        return []

    def _save_history(self):
        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_PATH, 'w') as f:
                json.dump(self._history[-HISTORY_KEEP:], f, indent=2, default=str)
        except Exception as e:
            print(f'[JARVIS] history save error: {e}')

    # ── Client construction ───────────────────────────────────────────────────
    def _get_local_client(self):
        if self._local_client is None:
            import openai
            self._local_client = openai.OpenAI(
                base_url=LOCAL_URL,
                api_key="not-needed",
                timeout=TIMEOUT,
            )
        return self._local_client

    def _get_claude_client(self):
        if self._claude_client is None:
            import anthropic
            self._claude_client = anthropic.Anthropic(api_key=self._api_key)
        return self._claude_client

    # ── System prompt (augmented with memory) ────────────────────────────────
    def _build_system_prompt(self) -> str:
        memory_section = ''
        try:
            from memory import aurora_memory
            episodes = aurora_memory.recent(limit=8)
            ep_lines = []
            for ep in reversed(episodes):
                ep_lines.append(
                    f"  [{ep['env']}] {ep['timestamp']} | "
                    f"{ep['action'][:60]} → {ep['outcome'][:60]}"
                )
            ep_block = '\n'.join(ep_lines) if ep_lines else '  (none yet)'
            world_ctx = aurora_memory.context_for_llm(max_tokens=200)
            memory_section = (
                f"\n\n## AURORA Memory (past actions and outcomes)\n{ep_block}"
                + (f"\n\n## Known entities\n{world_ctx}" if world_ctx else '')
            )
        except Exception:
            pass

        improvement_section = ''
        try:
            extra = getattr(self, '_extra_context', [])
            if extra:
                improvement_section = (
                    '\n\n## Self-Improvement Guidelines (from your own proposals)\n'
                    + '\n'.join(f'- {e}' for e in extra[-10:])
                )
        except Exception:
            pass

        # ── Mode-aware context ────────────────────────────────────────────────
        # In desktop mode, inject a recent screen/camera snapshot as text so
        # the brain knows what's happening on the machine right now.
        mode_section = ''
        try:
            from core.mode import current_mode
            mode = current_mode()
            if mode == 'desktop':
                ctx_file = BASE_DIR / 'data' / 'desktop_context.txt'
                if ctx_file.exists():
                    mode_section = '\n\n' + ctx_file.read_text()[:600]
            elif mode == 'game':
                mode_section = '\n\n## Current Mode\nGame-agent mode active (capture card + KB2040 present).'
        except Exception:
            pass

        return SYSTEM_PROMPT + memory_section + improvement_section + mode_section

    # ── Local inference (OpenAI-compatible) ──────────────────────────────────
    def _respond_local(self, user_text: str, tool_schemas: list) -> str:
        """Run tool-use loop against local llama-server."""
        from jarvis.tools import call_tool
        client      = self._get_local_client()
        oai_tools   = _anthropic_to_openai_tools(tool_schemas)
        system_text = self._build_system_prompt()

        # Build message list: system + history + new user turn
        messages = [{"role": "system", "content": system_text}]
        for h in self._history[-(HISTORY_KEEP - 2):]:
            messages.append(h)
        messages.append({"role": "user", "content": user_text})

        for _round in range(MAX_TOOL_ROUNDS):
            resp = client.chat.completions.create(
                model=LOCAL_MODEL,
                messages=messages,
                tools=oai_tools if oai_tools else None,
                tool_choice="auto" if oai_tools else None,
                max_tokens=MAX_TOKENS,
                temperature=0.2,
            )
            choice = resp.choices[0]
            msg    = choice.message

            tool_calls = msg.tool_calls or []

            if not tool_calls:
                # Final text response
                answer = (msg.content or '').strip()
                # Strip Qwen3 think blocks from spoken output
                import re
                answer = re.sub(r'<think>.*?</think>', '', answer,
                                flags=re.DOTALL).strip()
                return answer

            # Append assistant turn with tool calls
            messages.append({
                "role": "assistant",
                "content": msg.content or '',
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in tool_calls
                ],
            })

            # Execute each tool and append results
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or '{}')
                except json.JSONDecodeError:
                    args = {}
                print(f'[JARVIS/local] tool: {tc.function.name}({json.dumps(args)[:80]})')
                result_str = call_tool(tc.function.name, args)
                print(f'[JARVIS/local] result: {result_str[:120]}')
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })
                # AURORA record
                try:
                    from memory import aurora_memory
                    aurora_memory.record(
                        env='jarvis',
                        action=f'{tc.function.name}({json.dumps(args)[:120]})',
                        outcome=result_str[:300],
                        notes=f'prompt: {user_text[:100]}',
                        metadata={'tool': tc.function.name, 'model': 'local'},
                    )
                except Exception as _ae:
                    pass

        return "I ran into a loop — please try again."

    # ── Claude fallback (Anthropic SDK) ──────────────────────────────────────
    def _respond_claude(self, user_text: str, tool_schemas: list) -> str:
        """Fallback to Anthropic Claude when local server is unreachable."""
        from jarvis.tools import call_tool
        client      = self._get_claude_client()
        system_text = self._build_system_prompt()
        messages    = list(self._history[-(HISTORY_KEEP - 2):])
        messages.append({"role": "user", "content": user_text})
        model       = ANTHROPIC_MODEL

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=MAX_TOKENS,
                    system=system_text,
                    tools=tool_schemas,
                    messages=messages,
                )
            except Exception as e:
                if model != FALLBACK_MODEL:
                    model = FALLBACK_MODEL
                    continue
                return f"Both local and cloud failed: {e}"

            text_parts = []
            tool_calls = []
            for block in resp.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if not tool_calls:
                return " ".join(text_parts).strip()

            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for tc in tool_calls:
                print(f'[JARVIS/claude] tool: {tc.name}({json.dumps(tc.input)[:80]})')
                result_str = call_tool(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result_str,
                })
            messages.append({"role": "user", "content": tool_results})

        return "I ran into a loop — please try again."

    # ── Public entry point ────────────────────────────────────────────────────
    def respond(self, user_text: str, timeout: float = TIMEOUT) -> str:
        """Process a voice utterance and return the spoken response string."""
        from jarvis.tools import TOOL_SCHEMAS

        self._history.append({"role": "user", "content": user_text})

        answer = None
        # Local ONLY — no cloud fallback (prevents API spend when local is down)
        try:
            answer = self._respond_local(user_text, TOOL_SCHEMAS)
            self._using_local = True
            provider = 'local'
        except Exception as local_err:
            print(f'[JARVIS] local failed ({local_err}), staying offline (no cloud fallback)')
            self._using_local = False
            answer = "Local model is loading — try again in a moment."
            provider = 'none'

        if not answer:
            answer = "I didn't get a response — please try again."

        print(f'[JARVIS] answered via {provider}: {answer[:80]}')
        self._history.append({"role": "assistant", "content": answer})
        if len(self._history) > HISTORY_KEEP:
            self._history = self._history[-HISTORY_KEEP:]
        self._save_history()
        return answer

    def handle_gesture(self, command) -> bool:
        """Route a GestureCommand through Jarvis tool dispatch.

        Gestures are deterministic — no LLM round-trip needed. This method
        calls call_tool() directly so gesture events use the same tool
        infrastructure as voice commands without the latency of a full
        respond() call.

        Returns True if the command was dispatched, False if unrecognised.
        """
        from jarvis.tools import call_tool
        try:
            from gesture.recognizer import GestureCommand
        except ImportError:
            print('[JARVIS] gesture module not available')
            return False

        cmd_name = command.value if hasattr(command, 'value') else str(command)

        if command == GestureCommand.HOLD:
            print(f'[JARVIS/gesture] HOLD → clear_goals')
            call_tool('clear_goals', {})
            return True

        if command == GestureCommand.STOP:
            print(f'[JARVIS/gesture] STOP → inject_goal stop (priority 9)')
            call_tool('inject_goal', {'goal': 'stop', 'priority': 9,
                                       'reason': 'gesture:STOP'})
            return True

        if command == GestureCommand.FORWARD:
            print(f'[JARVIS/gesture] FORWARD → inject_goal explore (priority 5)')
            call_tool('inject_goal', {'goal': 'explore', 'priority': 5,
                                       'reason': 'gesture:FORWARD'})
            return True

        if command in (GestureCommand.TURN_LEFT, GestureCommand.TURN_RIGHT):
            # Drive commands — no goal injection; handled by UDP in dispatcher.
            print(f'[JARVIS/gesture] {cmd_name} → UDP only (no goal injection)')
            return True

        print(f'[JARVIS/gesture] unrecognised gesture command: {cmd_name}')
        return False

    def clear_history(self):
        """Reset conversation context and wipe persisted history."""
        self._history.clear()
        self._save_history()


# ── Singleton ─────────────────────────────────────────────────────────────────
_brain: JarvisBrain | None = None


def get_brain() -> JarvisBrain:
    global _brain
    if _brain is None:
        _brain = JarvisBrain()
    return _brain
