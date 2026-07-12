import base64
import json
import time
import urllib.request
import urllib.error
import cv2

import config

CLAUDE_MAX_RETRIES  = 3
CLAUDE_BACKOFF_BASE = 1.0   # seconds; doubles each retry (1s, 2s, 4s)


def _frame_to_b64(frame) -> str:
    """Encode OpenCV frame as base64 JPEG."""
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode('utf-8')


def _ask_gemini(frame, context: str, recent_history: str) -> str:
    b64 = _frame_to_b64(frame)
    prompt = f"""{context}

Recent history:
{recent_history}

Look at this screenshot and respond with a JSON object:
{{
  "observation": "what you see in one sentence",
  "action": "one specific action to take (e.g. press W, click at center, wait)",
  "key": "keyboard key if applicable, else null",
  "click": [x_percent, y_percent] or null,
  "look": {{"dx": -15, "dy": 0}} or null,
  "confidence": 0.0-1.0
}}
Only output valid JSON, nothing else."""

    payload = json.dumps({
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                {"text": prompt}
            ]
        }],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400}
    }).encode('utf-8')

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")

    req = urllib.request.Request(url, data=payload,
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        candidates = data.get('candidates') or []
        if not candidates:
            block_reason = data.get('promptFeedback', {}).get('blockReason', 'unknown')
            raise ValueError(f"no candidates in Gemini response; blockReason={block_reason}")
        parts = candidates[0].get('content', {}).get('parts') or []
        if not parts or 'text' not in parts[0]:
            finish_reason = candidates[0].get('finishReason', 'unknown')
            raise ValueError(f"no text part in Gemini response; finishReason={finish_reason}")
        text = parts[0]['text'].strip()
        # strip markdown fences if present
        if text.startswith('```'):
            text = '\n'.join(text.split('\n')[1:-1])
        return text
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return json.dumps({"observation": f"Gemini error {e.code}: {body[:200]}",
                           "action": "wait", "key": None, "click": None, "confidence": 0.0})
    except Exception as e:
        return json.dumps({"observation": f"error: {e}",
                           "action": "wait", "key": None, "click": None, "confidence": 0.0})


def _ask_claude(frame, context: str, recent_history: str) -> str:
    b64 = _frame_to_b64(frame)
    prompt = f"""{context}

Recent history:
{recent_history}

Look at this screenshot and respond with a JSON object:
{{
  "observation": "what you see in one sentence",
  "action": "one specific action to take",
  "key": "keyboard key if applicable, else null",
  "click": [x_percent, y_percent] or null,
  "look": {{"dx": -15, "dy": 0}} or null,
  "goal": "optional short natural-language goal, e.g. 'go deeper to find diamonds'",
  "confidence": 0.0-1.0
}}
"goal" is optional — only include it when you have a multi-step objective in mind.
Only output valid JSON, nothing else."""

    payload = json.dumps({
        "model": config.CLAUDE_MODEL,
        "max_tokens": 400,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    }).encode('utf-8')

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': config.ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01'
        }
    )

    last_error = None
    for attempt in range(CLAUDE_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            # Find the first text block (skip tool_use or other block types)
            text_block = next(
                (b for b in data.get('content', []) if b.get('type') == 'text'),
                None
            )
            if text_block is None:
                raise ValueError(f"no text block in response; types={[b.get('type') for b in data.get('content', [])]}")
            text = text_block['text'].strip()
            if text.startswith('```'):
                text = '\n'.join(text.split('\n')[1:-1])
            return text
        except urllib.error.HTTPError as e:
            last_error = f"Claude error {e.code}: {e.read().decode()[:200]}"
            if e.code not in (429, 500, 502, 503, 529):
                break   # non-transient (e.g. bad request/auth) — don't retry
        except Exception as e:
            last_error = f"error: {e}"

        if attempt < CLAUDE_MAX_RETRIES - 1:
            time.sleep(CLAUDE_BACKOFF_BASE * (2 ** attempt))

    return json.dumps({"observation": last_error,
                       "action": "wait", "key": None, "click": None, "confidence": 0.0})


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


def ask_vision(frame, recent_history: str = "", objects: list = None) -> dict:
    """
    Send frame to vision LLM. Returns parsed action dict.
    Falls back to Gemini if Claude fails, and vice versa.
    """
    context = f"{config.GAME_CONTEXT}\n\n{_format_detections(objects or [])}"

    provider = config.VISION_PROVIDER.lower()
    raw = _ask_gemini(frame, context, recent_history) \
          if provider == "gemini" \
          else _ask_claude(frame, context, recent_history)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"observation": raw[:200], "action": "wait",
                  "key": None, "click": None, "confidence": 0.0}

    return result
