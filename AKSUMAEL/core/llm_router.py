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
import os
import threading
import time
import urllib.error
import urllib.request

import config

# Sampling temperature sent on every routed call. This is the value that
# actually applies: an OpenAI-compatible `temperature` in the request body
# overrides llama-server's own --temp, so the mesh-llm unit file is not the
# knob for this — this constant is. Raised 0.2 → 0.4 on 2026-08-09 and reverted
# to 0.2 the same day: the diversity it bought was already there at 0.2, and one
# a-3 run in four fabricated death, tick and reward figures to defend a refusal.
# Callers that need determinism (the Overseer, safety/core) pass their own
# temperature and are unaffected.
LLM_TEMPERATURE = 0.2

_lock          = threading.Lock()
_call_counter  = 0
_call_counts   = {'local': 0, 'gemini': 0, 'claude': 0}
_last_provider = None

# Latched True the first time the local server rejects an image payload
# (HTTP 400, 422, or 500 with images).  Once set, all subsequent calls to
# _try_local() silently strip images so the text-only Qwen3-8B never sees
# vision data again until the server is restarted with a VLM.
_model_rejects_images: bool = False

# Liveness probe. llama-server's /health answers 200 off a flag set at model
# load and never reads the generation path, so a wedged server — slots stuck,
# KV cache exhausted, sampler hung — reports healthy while every completion
# comes back empty. The only reading that distinguishes those two states is a
# generation that actually returns tokens, so that is what is measured.
#
# Native /completion rather than the OpenAI /chat/completions this module
# otherwise uses: no chat template, no thinking-mode interaction, three tokens
# of output. It is the cheapest request that still exercises the path being
# tested.
PROBE_TIMEOUT = 5.0    # seconds — a wedged server hangs, so this IS the test
PROBE_TTL     = 30.0   # seconds a good result stays good, so the probe costs
                       # one tiny request per half-minute, not one per call
_probe        = {'ok': None, 'at': 0.0}


def _probe_url() -> str:
    """llama-server's native endpoint sits at the server root, while
    config.LOCAL_LLM_URL points at the OpenAI-compatible /v1 prefix."""
    base = config.LOCAL_LLM_URL.rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    return f'{base.rstrip("/")}/completion'


def mesh_llm_live(force: bool = False) -> bool:
    """True if the local server both answers AND generates.

    A cached True is reused for PROBE_TTL seconds; a failure is never cached,
    so recovery is picked up on the next call rather than after a timeout.

    An HTTP status from the endpoint itself (404/405 — a front end that does
    not expose /completion) is reported as live: that says the probe does not
    apply here, not that the server is wedged, and refusing every call on it
    would be a self-inflicted outage. Silence, a timeout, and an empty
    completion are the failures this exists to catch.
    """
    now = time.monotonic()
    if not force and _probe['ok'] and (now - _probe['at']) < PROBE_TTL:
        return True

    ok = False
    try:
        data = _post_json(_probe_url(), {'prompt': '1+1=', 'n_predict': 3},
                          {'Content-Type': 'application/json'}, PROBE_TIMEOUT)
        ok = bool((data.get('content') or '').strip())
        if not ok:
            print('[LLM_ROUTER] mesh-llm probe returned empty content — '
                  'server is up but not generating')
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            ok = True      # endpoint absent, not a wedged server
        else:
            print(f'[LLM_ROUTER] mesh-llm probe failed: HTTP {e.code}')
    except Exception as e:
        print(f'[LLM_ROUTER] mesh-llm probe failed: {e}')

    _probe['ok'] = ok
    _probe['at'] = now
    return ok


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
    model rejects image content (HTTP 400/422/500 — text-only model), latches
    _model_rejects_images and retries text-only.  All subsequent calls also
    skip image encoding until the service is restarted with a VLM.

    `system`, when given, is sent as a leading {"role": "system", ...}
    message ahead of the user message — needed for vision calls, where the
    loaded model (Qwen3.5-4B-Vision) otherwise defaults to GUI-element
    bounding-box detection instead of answering the actual prompt (see
    core/overseer.py's planning directive calls).

    Returns the raw text response on success, or None on any failure
    (connection refused, timeout, non-recoverable HTTP status).
    """
    global _model_rejects_images
    if not config.LOCAL_LLM_ENABLED:
        return None
    if not mesh_llm_live():
        print('[LLM_ROUTER] mesh-llm not generating — treating as down')
        return None

    # Qwen3-8B (and any other text-only model) returns HTTP 500 with
    # "image input is not supported".  Once we've seen that once, strip
    # images from every subsequent call rather than wasting tokens and RAM.
    if _model_rejects_images and images:
        images = None

    url     = f"{config.LOCAL_LLM_URL}/chat/completions"
    headers = {'Content-Type': 'application/json'}

    def _multimodal_content():
        parts = [{"type": "text", "text": prompt}]
        for b64 in images:
            parts.append({"type": "image_url",
                           "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        return parts

    def _extract(data: dict) -> str:
        """None on an empty completion. The same outage the probe catches can
        open mid-call, and an empty string counted as success here is how a
        wedged server reaches a caller as a blank answer instead of a
        failure."""
        text = data['choices'][0]['message']['content'].strip()
        text = _strip_fences(text)
        return text or None

    def _messages(content) -> list:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": content})
        return msgs

    for attempt in range(max(1, retries)):
        content = _multimodal_content() if images else prompt
        payload = {"model": config.LOCAL_LLM_MODEL,
                   "temperature": LLM_TEMPERATURE,
                   "max_tokens": max_tokens,
                   # The loaded model (Qwen3.5) is a reasoning model that by
                   # default spends its whole max_tokens budget on
                   # <think>-style reasoning_content and never emits a
                   # `content` reply (finish_reason=length, content="").
                   # Disable that so max_tokens is spent on the actual answer.
                   "chat_template_kwargs": {"enable_thinking": False},
                   "messages": _messages(content)}
        try:
            out = _extract(_post_json(url, payload, headers, timeout))
            if out is not None:
                return out
            # Empty completion: fall through to the retry/backoff below rather
            # than returning it, since an empty answer is the outage symptom.
        except urllib.error.HTTPError as e:
            if images and e.code in (400, 422, 500):
                # Loaded model rejected multimodal content — latch the flag so
                # every future call skips image encoding, then retry text-only.
                _model_rejects_images = True
                print(f'[LLM_ROUTER] model rejects images (HTTP {e.code}) — '
                      'switching to text-only mode for this session')
                text_payload = {"model": config.LOCAL_LLM_MODEL,
                                "temperature": LLM_TEMPERATURE,
                                "max_tokens": max_tokens,
                                "chat_template_kwargs": {"enable_thinking": False},
                                "messages": _messages(prompt)}
                try:
                    out = _extract(_post_json(url, text_payload, headers, timeout))
                    if out is not None:
                        return out
                except Exception:
                    pass
                return None   # text-only retry also failed; no point looping
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


def call_anthropic(prompt: str, max_tokens: int = 800, images: list = None,
                   timeout: float = 30.0, system: str = None,
                   model: str = 'claude-haiku-4-5-20251001') -> str | None:
    """Call Anthropic API directly for tasks the local model can't handle
    (e.g. inventory screen parsing). Uses the API key at ~/.config/anthropic/key.
    Returns the raw text response, or None on failure."""
    key_path = os.path.expanduser('~/.config/anthropic/key')
    try:
        with open(key_path) as f:
            api_key = f.read().strip()
    except OSError:
        print('[LLM_ROUTER] Anthropic key not found at ~/.config/anthropic/key')
        return None

    try:
        import anthropic as _anthropic
    except ImportError:
        print('[LLM_ROUTER] anthropic package not installed — run: pip install anthropic')
        return None

    content = []
    if images:
        for b64 in images:
            content.append({
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64},
            })
    content.append({'type': 'text', 'text': prompt})

    kwargs = dict(model=model, max_tokens=max_tokens, messages=[{'role': 'user', 'content': content}])
    if system:
        kwargs['system'] = system

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(**kwargs)
        text = resp.content[0].text.strip()
        _record('claude')
        return text
    except Exception as e:
        print(f'[LLM_ROUTER] Anthropic call failed: {e}')
        return None


def route_llm_call(prompt: str, max_tokens: int = 800, images: list = None,
                   timeout: float = 45.0, local_retries: int = 1,
                   use_cloud: bool = False, system: str = None):
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

    result = _try_local(prompt, max_tokens, images, timeout, local_retries, system=system)
    if result is not None:
        _record('local')
        return result, 'local'

    print('[LLM_ROUTER] local mesh-llm call failed — returning safe default')
    global _last_provider
    _last_provider = None
    return None, None


def try_local_llm(prompt: str, max_tokens: int = 1024, images: list = None,
                   timeout: float = 15.0) -> str | None:
    """Routes to local mesh-llm. Named entry point for callers
    (e.g. tools/claude_autolabel.py) that need a single local-only call
    without the general route_llm_call() retry/fallback chain.
    Returns the raw text response, or None on failure — never raises."""
    result = _try_local(prompt, max_tokens, images, timeout, retries=1)
    if result is not None:
        _record('local')
    return result


# Backward-compat alias — remove once all callers updated.
try_claude = try_local_llm


def llm_train_call(prompt: str, max_tokens: int = 800, images: list = None,
                    timeout: float = 45.0, local_retries: int = 1):
    """
    Entry point for training-related LLM work (label generation, reflection
    summaries, dataset annotation). Routes to local mesh-llm only — no
    cloud fallback.
    """
    return route_llm_call(prompt, max_tokens=max_tokens, images=images,
                           timeout=timeout, local_retries=local_retries)


def call_local_llm(prompt: str, max_tokens: int = 800, images: list = None,
                    timeout: float = 15.0, system: str = None) -> str:
    """
    Routes to local mesh-llm — single call, no retry/fallback chain.
    Use for latency-sensitive paths (overseer decisions, tool calls) where
    route_llm_call()'s extra retries aren't worth the wait.

    `system`, when given, is prepended as a system message — see
    _try_local() for details.

    Returns the raw text response, or None on failure — never raises.
    """
    result = _try_local(prompt, max_tokens, images, timeout, retries=1, system=system)
    if result is not None:
        _record('local')
    return result


# Backward-compat alias — remove once all callers updated.
call_claude_direct = call_local_llm
