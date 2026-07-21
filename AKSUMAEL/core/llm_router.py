# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Shared LLM Router                         ║
# ║  Single choke point for every LLM call in the         ║
# ║  codebase. Routing policy:                             ║
# ║    Every call — gameplay or training/labeling — goes  ║
# ║    to the local mesh-llm server (localhost:9337,       ║
# ║    OpenAI-compatible /v1/chat/completions). AKSUMAEL   ║
# ║    makes no outbound calls to Anthropic or Google;     ║
# ║    it runs fully offline. GEMINI_API_KEY and           ║
# ║    ANTHROPIC_API_KEY are read in config.py but unused  ║
# ║    here — kept in case cloud fallback is reinstated    ║
# ║    later.                                               ║
# ╚══════════════════════════════════════════════════════╝

import base64
import json
import threading
import time
import urllib.error
import urllib.request

import config

_lock          = threading.Lock()
_call_counter  = 0
_call_counts   = {'local': 0, 'gemini': 0, 'claude': 0}
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
               retries: int, system: str = None) -> str:
    """Query the on-box Mesh-LLM server (OpenAI-compatible /v1/chat/completions).
    Tries a multimodal request first when `images` is given; if the loaded
    model rejects image content (HTTP 400/422 — text-only model), retries
    once with a text-only prompt that relies on whatever detection/context
    text is already folded into `prompt`.

    `system`, when given, is sent as a leading {"role": "system", ...}
    message ahead of the user message — needed for vision calls, where the
    loaded model (Qwen3.5-4B-Vision) otherwise defaults to GUI-element
    bounding-box detection instead of answering the actual prompt (see
    core/overseer.py's planning directive calls).

    Returns the raw text response on success, or None on any failure
    (connection refused, timeout, non-recoverable HTTP status).
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

    def _messages(content) -> list:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": content})
        return msgs

    for attempt in range(max(1, retries)):
        content = _multimodal_content() if images else prompt
        payload = {"model": config.LOCAL_LLM_MODEL, "temperature": 0.2,
                   "max_tokens": max_tokens,
                   # The loaded model (Qwen3.5) is a reasoning model that by
                   # default spends its whole max_tokens budget on
                   # <think>-style reasoning_content and never emits a
                   # `content` reply (finish_reason=length, content="").
                   # Disable that so max_tokens is spent on the actual answer.
                   "chat_template_kwargs": {"enable_thinking": False},
                   "messages": _messages(content)}
        try:
            return _extract(_post_json(url, payload, headers, timeout))
        except urllib.error.HTTPError as e:
            if images and e.code in (400, 422):
                # Loaded model rejected multimodal content — retry text-only.
                text_payload = {"model": config.LOCAL_LLM_MODEL, "temperature": 0.2,
                                "max_tokens": max_tokens,
                                "chat_template_kwargs": {"enable_thinking": False},
                                "messages": _messages(prompt)}
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


def _record(provider: str):
    global _last_provider
    with _lock:
        _call_counts[provider] = _call_counts.get(provider, 0) + 1
    _last_provider = provider


def route_llm_call(prompt: str, max_tokens: int = 800, images: list = None,
                    timeout: float = 45.0, local_retries: int = 1,
                    use_cloud: bool = False):
    """
    Route a single-turn LLM prompt to the local mesh-llm server.

    `use_cloud` is kept for call-site compatibility but has no effect —
    AKSUMAEL no longer dials out to Gemini or Claude for any call, gameplay
    or training. If local mesh-llm fails, (None, None) is returned so the
    caller falls back to its own safe default (e.g. an empty inventory, a
    'wait' action) instead of hitting a cloud API.

    Args:
        prompt:        the text prompt (already includes any context/detections).
        max_tokens:    max output tokens.
        images:        optional list of base64-encoded JPEG strings (see
                       frame_to_b64()) for multimodal calls.
        timeout:       per-request timeout in seconds.
        local_retries: number of attempts before giving up.
        use_cloud:     unused — retained for backward-compatible call sites.

    Returns:
        (text, provider) — provider is 'local' or None if the call failed.
        `text` is None iff provider is None.
    """
    global _call_counter
    with _lock:
        _call_counter += 1

    result = _try_local(prompt, max_tokens, images, timeout, local_retries)
    if result is not None:
        _record('local')
        return result, 'local'

    print('[LLM_ROUTER] local mesh-llm call failed — returning safe default')
    global _last_provider
    _last_provider = None
    return None, None


def try_claude(prompt: str, max_tokens: int = 1024, images: list = None,
                timeout: float = 15.0) -> str | None:
    """Routes to local mesh-llm. Kept as a named entry point for callers
    (e.g. tools/claude_autolabel.py) that previously wanted Claude
    specifically for label-quality reasons — AKSUMAEL no longer calls
    Claude directly, so this is now equivalent to route_llm_call() against
    the local tier only. Returns the raw text response, or None on
    failure — never raises."""
    result = _try_local(prompt, max_tokens, images, timeout, retries=1)
    if result is not None:
        _record('local')
    return result


def llm_train_call(prompt: str, max_tokens: int = 800, images: list = None,
                    timeout: float = 45.0, local_retries: int = 1):
    """
    Entry point for training-related LLM work (label generation, reflection
    summaries, dataset annotation). Routes to local mesh-llm only — no
    cloud fallback.
    """
    return route_llm_call(prompt, max_tokens=max_tokens, images=images,
                           timeout=timeout, local_retries=local_retries)


def call_claude_direct(prompt: str, max_tokens: int = 800, images: list = None,
                        timeout: float = 15.0, system: str = None) -> str:
    """
    Routes to local mesh-llm. Kept as a named entry point for callers
    (e.g. core/overseer.py strategic decisions) that previously wanted
    Claude specifically, skipping the general route_llm_call() path —
    AKSUMAEL no longer calls Claude directly, so this is now equivalent to
    a local-only call.

    `system`, when given, is passed through to _try_local() as a leading
    system message — see its docstring for why vision calls need this.

    Returns the raw text response, or None on failure — never raises.
    """
    result = _try_local(prompt, max_tokens, images, timeout, retries=1, system=system)
    if result is not None:
        _record('local')
    return result
