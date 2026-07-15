import json

import config
from core.llm_router import route_llm_call, frame_to_b64

_call_counts = {'local': 0, 'gemini': 0, 'claude': 0}
_last_provider = None


def get_call_counts() -> dict:
    """Session-total vision-LLM call counts, for health reporting."""
    return dict(_call_counts)


def get_last_provider() -> str:
    """Provider used on the most recent ask_vision() call, for health reporting."""
    return _last_provider


def _format_detections(objects: list) -> str:
    """Render YOLO detections as a text block for the LLM prompt."""
    if not objects:
        return 'YOLO detections: none'
    lines = []
    for o in objects:
        label = o.get('label', '?')
        conf  = o.get('conf', 0)
        box   = o.get('box', [])
        tag   = ' (unlabeled)' if o.get('unknown') else ''
        lines.append(f"  - {label} conf={conf:.2f} box={box}{tag}")
    return 'YOLO detections:\n' + '\n'.join(lines)


_VISION_PROMPT = """{context}

Recent history:
{recent_history}

Look at this screenshot and respond with a JSON object:
{{
  "observation": "what you see in one sentence",
  "action": "one specific action to take (e.g. press W, click at center, wait)",
  "key": "keyboard key if applicable, else null",
  "click": [x_percent, y_percent] or null,
  "look": {{"dx": -15, "dy": 0}} or null,
  "goal": "optional short natural-language goal, e.g. 'go deeper to find diamonds'",
  "confidence": 0.0-1.0
}}
"goal" is optional — only include it when you have a multi-step objective in mind.
Only output valid JSON, nothing else."""


def ask_vision(frame, recent_history: str = "", objects: list = None,
                phase: str = None) -> dict:
    """
    Send frame to vision LLM. Returns parsed action dict.

    Routing (see core/llm_router.route_llm_call()): local (Mesh-LLM, on-box,
    no rate cap) is primary for every call. Gemini gets an occasional
    background diversity/monitoring ping when local succeeds, and is the
    real synchronous fallback only when local fails. Claude is the emergency
    backup, tried only when both local and Gemini fail.

    `phase` (wood/stone/iron/diamond/nether/end), if given, selects the
    phase-specific tactical checklist appended to GAME_CONTEXT.
    """
    base_context = config.game_context_for_phase(phase)
    context = f"{base_context}\n\n{_format_detections(objects or [])}"
    prompt = _VISION_PROMPT.format(context=context, recent_history=recent_history)

    global _last_provider
    raw, provider = route_llm_call(
        # Generous budget/timeout — the local model 'thinks' before
        # answering, often burning 200-300+ tokens on hidden reasoning
        # before the actual JSON reply (see inventory_reader.py /
        # chest_manager.py, which hit and fixed this same issue). This is
        # the highest-frequency, highest-stakes call in the whole loop —
        # starving it caused the response to get cut off mid-thought,
        # fail to parse, and silently fall back to a 'wait'/confidence:0.0
        # no-op almost every tick.
        prompt, max_tokens=1200, images=[frame_to_b64(frame)],
        timeout=45, local_retries=3)

    if provider is not None:
        _call_counts[provider] += 1
    _last_provider = provider

    if raw is None:
        return {"observation": "all LLM tiers failed", "action": "wait",
                "key": None, "click": None, "confidence": 0.0}

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = None

    # The local model sometimes hallucinates a generic object-detection-style
    # JSON array (e.g. "[{'label': 'arrow', 'bbox': [...]}]") instead of the
    # requested action object — valid JSON, so json.loads() doesn't raise,
    # but every caller of ask_vision() calls .get() on the result expecting
    # a dict. That crashed the whole process (see 2026-07-15 —
    # AttributeError: 'list' object has no attribute 'get' at
    # runtime.py's `if action_dict.get('goal')`). Treat anything that isn't
    # a dict the same as a parse failure.
    if not isinstance(result, dict):
        result = {"observation": raw[:200], "action": "wait",
                  "key": None, "click": None, "confidence": 0.0}

    return result
