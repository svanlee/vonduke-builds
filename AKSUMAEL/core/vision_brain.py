import base64
import json
import threading
import time
import urllib.request
import urllib.error
import cv2

import config

CLAUDE_MAX_RETRIES  = 3
CLAUDE_BACKOFF_BASE = 1.0   # seconds; doubles each retry (1s, 2s, 4s)


class _RateLimiter:
    """Token-bucket shared by every caller of ask_vision() — the main tick
    loop AND the scan pipeline's threat-identify burst (up to
    SCAN_MAX_THREATS calls in ~1s) both go through this one function, so
    this is the single choke point that caps real outbound request rate
    regardless of how many call sites fire at once."""

    def __init__(self, per_minute: float, burst: int):
        self._rate     = per_minute / 60.0   # tokens per second
        self._capacity = float(burst)
        self._tokens   = float(burst)
        self._last     = time.monotonic()
        self._lock     = threading.Lock()

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                self._tokens = 0.0
            else:
                self._tokens -= 1.0
                wait = 0.0
        if wait > 0:
            time.sleep(wait)


# Conservative ceilings, held well under provider limits even if every call
# site (main loop + scan burst) fires back-to-back.
# Gemini free tier: 15 req/min, 1500 req/day — cap at 10/min so a 3-call
# scan burst plus a main-loop tick can never trip the per-minute limit.
_GEMINI_LIMITER = _RateLimiter(per_minute=10, burst=3)
_CLAUDE_LIMITER = _RateLimiter(per_minute=20, burst=3)
# Local model runs on-box — no external quota, so no rate limiter/bucket for it.

_call_counts = {'local': 0, 'gemini': 0, 'claude': 0}
_last_provider = None


def get_call_counts() -> dict:
    """Session-total vision-LLM call counts, for health reporting."""
    return dict(_call_counts)


def get_last_provider() -> str:
    """Provider used on the most recent ask_vision() call, for health reporting."""
    return _last_provider


def _frame_to_b64(frame) -> str:
    """Encode OpenCV frame as base64 JPEG."""
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode('utf-8')


def _ask_local(frame, context: str, recent_history: str):
    """
    Query the on-box Mesh-LLM server (OpenAI-compatible /v1/chat/completions).
    Tries a multimodal request first (base64 JPEG + prompt); if the loaded
    model rejects image content (HTTP 400 — text-only model), retries once
    with a text-only prompt that relies on the YOLO-detections block already
    folded into `context`.

    Returns the raw JSON-observation string on success, or None on any
    failure (connection refused, timeout, non-200) — None is the fallback
    signal for ask_vision() to move on to Gemini.
    """
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

    url = f"{config.LOCAL_LLM_URL}/chat/completions"

    def _post(payload: dict):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=config.LOCAL_LLM_TIMEOUT) as resp:
            data = json.loads(resp.read())
        text = data['choices'][0]['message']['content'].strip()
        if text.startswith('```'):
            text = '\n'.join(text.split('\n')[1:-1])
        return text

    vision_payload = {
        "model": config.LOCAL_LLM_MODEL,
        "temperature": 0.2,
        "max_tokens": 400,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url":
                    {"url": f"data:image/jpeg;base64,{_frame_to_b64(frame)}"}}
            ]
        }]
    }

    try:
        return _post(vision_payload)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            return None   # non-recoverable (5xx, auth, etc.) — fall back
    except Exception:
        return None   # connection refused / timeout / malformed response

    # Model rejected multimodal content (400) — retry text-only, relying on
    # the YOLO-detections block already embedded in `context`.
    text_payload = {
        "model": config.LOCAL_LLM_MODEL,
        "temperature": 0.2,
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        return _post(text_payload)
    except Exception:
        return None


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
        "model": config.CLAUDE_VISION_MODEL,
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


def ask_vision(frame, recent_history: str = "", objects: list = None,
                phase: str = None) -> dict:
    """
    Send frame to vision LLM. Returns parsed action dict.

    3-tier routing: local (Mesh-LLM, on-box, no rate cap) is primary for
    every call. If it's disabled, unreachable, or times out, falls back to
    Gemini. If Gemini also fails, Claude (haiku) is the last resort for
    complex reasoning.

    `phase` (wood/stone/iron/diamond/nether/end), if given, selects the
    phase-specific tactical checklist appended to GAME_CONTEXT.
    """
    base_context = config.game_context_for_phase(phase)
    context = f"{base_context}\n\n{_format_detections(objects or [])}"

    global _last_provider
    raw, provider = None, None

    if config.LOCAL_LLM_ENABLED:
        # No rate limiter — local calls don't touch the Gemini/Claude buckets.
        raw = _ask_local(frame, context, recent_history)
        if raw:
            provider = 'local'

    if raw is None:
        _GEMINI_LIMITER.acquire()
        _call_counts['gemini'] += 1
        gemini_raw = _ask_gemini(frame, context, recent_history)
        if gemini_raw and "error" not in gemini_raw.lower():
            raw, provider = gemini_raw, 'gemini'

    if raw is None:
        _CLAUDE_LIMITER.acquire()
        _call_counts['claude'] += 1
        raw, provider = _ask_claude(frame, context, recent_history), 'claude'

    if provider == 'local':
        _call_counts['local'] += 1
    _last_provider = provider

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"observation": raw[:200], "action": "wait",
                  "key": None, "click": None, "confidence": 0.0}

    return result
