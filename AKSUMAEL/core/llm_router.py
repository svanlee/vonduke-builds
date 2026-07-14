# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Shared LLM Router                         ║
# ║  Single choke point for every local/Gemini/Claude     ║
# ║  call in the codebase. Routing policy:                ║
# ║    1. Local mesh-llm (localhost:9337) is primary for  ║
# ║       every call.                                     ║
# ║    2. Gemini is pinged in the background every Nth    ║
# ║       call that local serves successfully — purely to ║
# ║       monitor availability/diversity. It never blocks ║
# ║       and never replaces a successful local response. ║
# ║    3. If local fails, Gemini is tried for real as the ║
# ║       synchronous fallback.                           ║
# ║    4. If both local and Gemini fail, Claude is the    ║
# ║       emergency backup.                               ║
# ╚══════════════════════════════════════════════════════╝

import base64
import json
import threading
import time
import urllib.error
import urllib.request

import config

GEMINI_INTERVAL = 15   # ping Gemini for diversity/monitoring every Nth call local serves

CLAUDE_MAX_RETRIES  = 3
CLAUDE_BACKOFF_BASE = 1.0   # seconds; doubles each retry (1s, 2s, 4s)


class _RateLimiter:
    """Token-bucket shared by every route_llm_call() invocation that reaches
    Gemini or Claude, so real fallback traffic and the periodic diversity
    ping can never together trip provider rate limits."""

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


# Gemini free tier: 15 req/min, 1500 req/day — cap under that even with the
# diversity ping added on top of real fallback traffic.
_GEMINI_LIMITER = _RateLimiter(per_minute=10, burst=3)
_CLAUDE_LIMITER = _RateLimiter(per_minute=20, burst=3)
# Local model runs on-box — no external quota, so no rate limiter for it.

_lock          = threading.Lock()
_call_counter  = 0
_call_counts   = {'local': 0, 'gemini': 0, 'gemini_ping': 0, 'claude': 0}
_last_provider = None


def get_router_call_counts() -> dict:
    """Session-total call counts across every caller of route_llm_call(), for
    diagnostics. Callers that need their own scoped counts (e.g. vision-only)
    should track the `provider` returned from route_llm_call() themselves."""
    with _lock:
        return dict(_call_counts)


def frame_to_b64(frame) -> str:
    """Encode an OpenCV BGR frame as a base64 JPEG string, for the `images`
    argument to route_llm_call()."""
    import cv2
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode('utf-8')


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines)
    return text.strip()


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _try_local(prompt: str, max_tokens: int, images: list, timeout: float,
               retries: int) -> str:
    """Query the on-box Mesh-LLM server (OpenAI-compatible /v1/chat/completions).
    Tries a multimodal request first when `images` is given; if the loaded
    model rejects image content (HTTP 400/422 — text-only model), retries
    once with a text-only prompt that relies on whatever detection/context
    text is already folded into `prompt`.

    Returns the raw text response on success, or None on any failure
    (connection refused, timeout, non-recoverable HTTP status) — None is the
    signal for route_llm_call() to move on to Gemini.
    """
    if not config.LOCAL_LLM_ENABLED:
        return None

    url     = f"{config.LOCAL_LLM_URL}/chat/completions"
    headers = {'Content-Type': 'application/json'}

    def _multimodal_content():
        parts = [{"type": "text", "text": prompt}]
        for b64 in images:
            parts.append({"type": "image_url",
                           "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        return parts

    def _extract(data: dict) -> str:
        text = data['choices'][0]['message']['content'].strip()
        return _strip_fences(text)

    for attempt in range(max(1, retries)):
        content = _multimodal_content() if images else prompt
        payload = {"model": config.LOCAL_LLM_MODEL, "temperature": 0.2,
                   "max_tokens": max_tokens,
                   "messages": [{"role": "user", "content": content}]}
        try:
            return _extract(_post_json(url, payload, headers, timeout))
        except urllib.error.HTTPError as e:
            if images and e.code in (400, 422):
                # Loaded model rejected multimodal content — retry text-only.
                text_payload = {"model": config.LOCAL_LLM_MODEL, "temperature": 0.2,
                                "max_tokens": max_tokens,
                                "messages": [{"role": "user", "content": prompt}]}
                try:
                    return _extract(_post_json(url, text_payload, headers, timeout))
                except Exception:
                    pass
            if e.code not in (429, 500, 502, 503, 529):
                return None   # non-transient — don't retry
        except Exception:
            pass   # connection refused / timeout / malformed response

        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    return None


def _try_gemini(prompt: str, max_tokens: int, images: list,
                 timeout: float = 10.0) -> str:
    """Real (synchronous) Gemini call. Returns the raw text response, or
    None on any failure — never raises."""
    if not config.GEMINI_API_KEY:
        return None

    parts = [{"inline_data": {"mime_type": "image/jpeg", "data": b64}} for b64 in (images or [])]
    parts.append({"text": prompt})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")

    try:
        data = _post_json(url, payload, {'Content-Type': 'application/json'}, timeout)
        candidates = data.get('candidates') or []
        if not candidates:
            return None
        parts_out = candidates[0].get('content', {}).get('parts') or []
        if not parts_out or 'text' not in parts_out[0]:
            return None
        return _strip_fences(parts_out[0]['text'].strip())
    except Exception:
        return None


def _try_claude(prompt: str, max_tokens: int, images: list,
                 timeout: float = 15.0) -> str:
    """Emergency-backup Claude call, with retry/backoff on transient errors.
    Returns the raw text response, or None on any failure — never raises."""
    if not config.ANTHROPIC_API_KEY:
        return None

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}
               for b64 in (images or [])]
    content.append({"type": "text", "text": prompt})

    payload = {"model": config.CLAUDE_MODEL, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": content}]}
    headers = {
        'Content-Type': 'application/json',
        'x-api-key': config.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
    }

    for attempt in range(CLAUDE_MAX_RETRIES):
        try:
            data = _post_json("https://api.anthropic.com/v1/messages", payload, headers, timeout)
            text_block = next(
                (b for b in data.get('content', []) if b.get('type') == 'text'), None)
            if text_block is None:
                return None
            return _strip_fences(text_block['text'].strip())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 529):
                return None   # non-transient (bad request/auth) — don't retry
        except Exception:
            pass

        if attempt < CLAUDE_MAX_RETRIES - 1:
            time.sleep(CLAUDE_BACKOFF_BASE * (2 ** attempt))

    return None


def _record(provider: str):
    global _last_provider
    with _lock:
        _call_counts[provider] = _call_counts.get(provider, 0) + 1
    _last_provider = provider


def _ping_gemini_diversity(prompt: str, max_tokens: int, images: list, call_num: int):
    """Fire-and-forget Gemini call for quality/diversity monitoring. Runs in
    a background thread, never blocks the caller, and never replaces the
    local result that was already returned — it only logs whether Gemini is
    reachable right now."""
    def _run():
        _GEMINI_LIMITER.acquire()
        ok = _try_gemini(prompt, max_tokens, images) is not None
        with _lock:
            _call_counts['gemini_ping'] += 1
        print(f'[LLM_ROUTER] gemini diversity ping (call #{call_num}): '
              f'{"reachable" if ok else "unreachable"}')

    threading.Thread(target=_run, daemon=True).start()


def route_llm_call(prompt: str, max_tokens: int = 800, images: list = None,
                    timeout: float = 45.0, local_retries: int = 1):
    """
    Route a single-turn LLM prompt through the 3-tier policy described at the
    top of this module.

    Args:
        prompt:        the text prompt (already includes any context/detections).
        max_tokens:    max output tokens.
        images:        optional list of base64-encoded JPEG strings (see
                       frame_to_b64()) for multimodal calls.
        timeout:       per-request timeout in seconds for the local tier.
        local_retries: number of local-tier attempts before falling back.

    Returns:
        (text, provider) — provider is 'local', 'gemini', 'claude', or None
        if every tier failed. `text` is None iff provider is None.
    """
    global _call_counter
    with _lock:
        _call_counter += 1
        call_num = _call_counter

    result = _try_local(prompt, max_tokens, images, timeout, local_retries)
    if result is not None:
        _record('local')
        if call_num % GEMINI_INTERVAL == 0:
            _ping_gemini_diversity(prompt, max_tokens, images, call_num)
        return result, 'local'

    # Local failed — real (synchronous) fallback to Gemini.
    _GEMINI_LIMITER.acquire()
    result = _try_gemini(prompt, max_tokens, images)
    if result is not None:
        _record('gemini')
        return result, 'gemini'

    # Both local and Gemini failed — Claude is the emergency backup.
    _CLAUDE_LIMITER.acquire()
    result = _try_claude(prompt, max_tokens, images)
    if result is not None:
        _record('claude')
        return result, 'claude'

    global _last_provider
    _last_provider = None
    return None, None
