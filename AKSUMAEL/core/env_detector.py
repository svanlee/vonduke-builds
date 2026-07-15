# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Boot-time Environment Detector             ║
# ║  "What am I plugged into right now?"                   ║
# ╚══════════════════════════════════════════════════════╝
#
# Runs once at startup (see core/runtime.py): grab a frame off the capture
# card, ask mesh-llm what it's looking at, and load (or mint) the matching
# core/env_profile.py profile. This is what makes AKSUMAEL game-agnostic —
# everything downstream (YOLO weights, skill library, action space) keys
# off whatever this returns instead of a hardcoded config.ACTIVE_ENV.

from __future__ import annotations

import json
import re
import time

import cv2

import config
from core import env_profile
from core.llm_router import route_llm_call, frame_to_b64

_DETECT_PROMPT = """Look at this screenshot. What environment, operating \
system, application, or game is this? Be specific (e.g. "Minecraft Java \
Edition survival", "Windows 11 desktop", "Fallout 4", "VS Code editor").

Respond in JSON only, no other text:
{"env_type": string, "confidence": 0.0-1.0, "description": string}
"""

# How long to wait for a usable frame from the capture card before giving
# up and falling back — startup shouldn't hang indefinitely if the card
# isn't plugged in yet.
_GRAB_TIMEOUT_SEC = 5.0


def _grab_frame(device_index: int | None = None, timeout: float = _GRAB_TIMEOUT_SEC):
    """One-shot frame grab straight off the capture card, independent of
    core.capture.VideoCapturePipeline (which needs a YOLODetector/UI already
    constructed) — detection needs to run before either exists at boot."""
    dev = config.CAMERA_INDEX if device_index is None else device_index
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    deadline = time.time() + timeout
    frame = None
    # Discard failed reads — some capture cards return nothing (or a black
    # frame) for the first few reads right after opening the device.
    while time.time() < deadline:
        ret, f = cap.read()
        if ret and f is not None:
            frame = f
            break
        time.sleep(0.05)
    cap.release()
    return frame


def _parse_llm_json(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    # Best-effort: pull the first {...} block out of a chatty response
    # instead of failing outright on stray prose/fences.
    match = re.search(r'\{.*\}', raw or '', re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def detect(frame=None, fallback_env_id: str | None = None) -> env_profile.EnvProfile:
    """
    Boot-time environment detection:
      1. Capture a frame (or use `frame` if the caller already grabbed one).
      2. Ask mesh-llm what it's looking at.
      3. Fuzzy-match the answer against data/env_profiles/* (see
         core/env_profile.match_env_type) — load the match, or mint a new
         bootstrap profile and flag it for the initial observation phase.

    Never raises: on any failure (no capture device, mesh-llm unreachable,
    unparsable response) this falls back to `fallback_env_id`
    (config.ACTIVE_ENV by default) so a detection hiccup never blocks
    startup — the existing Minecraft-only boot path is exactly this
    fallback with confidence effectively 1.0.
    """
    fallback_env_id = fallback_env_id or getattr(config, 'ACTIVE_ENV', 'minecraft')

    if frame is None:
        print('[ENV_DETECTOR] capturing frame for environment detection...')
        frame = _grab_frame()

    if frame is None:
        print(f'[ENV_DETECTOR] no frame available — falling back to "{fallback_env_id}"')
        return env_profile.get_or_create(fallback_env_id)

    raw, provider = route_llm_call(
        _DETECT_PROMPT, max_tokens=200, images=[frame_to_b64(frame)],
        timeout=config.LOCAL_LLM_TIMEOUT)

    if raw is None:
        print(f'[ENV_DETECTOR] mesh-llm unreachable — falling back to "{fallback_env_id}"')
        return env_profile.get_or_create(fallback_env_id)

    result = _parse_llm_json(raw)
    if not result or not result.get('env_type'):
        print(f'[ENV_DETECTOR] unparsable response ({provider}): {raw[:200]!r} '
              f'— falling back to "{fallback_env_id}"')
        return env_profile.get_or_create(fallback_env_id)

    env_type    = str(result['env_type'])
    confidence  = float(result.get('confidence') or 0.0)
    description = str(result.get('description', ''))
    print(f'[ENV_DETECTOR] detected "{env_type}" (confidence={confidence:.2f}, '
          f'via {provider}): {description}')

    matched_id = env_profile.match_env_type(env_type)
    if matched_id:
        profile = env_profile.load(matched_id)
        print(f'[ENV_DETECTOR] matched existing profile: {matched_id}')
    else:
        new_id  = env_profile.env_id_from_type(env_type)
        profile = env_profile.create(new_id, display_name=env_type, description=description)
        print(f'[ENV_DETECTOR] no match for "{env_type}" — new environment, '
              f'flagged for initial observation phase: {new_id}')

    profile.touch()
    return profile
