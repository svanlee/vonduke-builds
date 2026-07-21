"""
Sidecar auth — issues and validates the tokens a daemon/sidecar pair uses
to authenticate WebSocket messages (see sidecar/protocol.py).

Uses PyJWT (HS256) when available. Falls back to a hand-rolled HMAC-SHA256
token of the same shape when PyJWT isn't installed (e.g. a bare-metal Pi
image that hasn't had it added yet) — same call signatures either way, so
callers never need to know which backend is active.
"""
import base64
import hashlib
import hmac
import json
import os
import time

try:
    import jwt as _pyjwt
    _HAVE_PYJWT = True
except ImportError:
    _HAVE_PYJWT = False

SECRET_PATH = os.path.join('data', 'sidecar_secret.key')
DEFAULT_TTL_SECONDS = 3600
ALGORITHM = 'HS256'


def _load_or_create_secret() -> bytes:
    """Shared HMAC/JWT signing secret, generated on first use and reused
    after that. Both ends of a daemon/sidecar pair need the same file
    (copy it over once, out of band — this scaffold doesn't do key
    exchange)."""
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, 'rb') as f:
            return f.read()
    os.makedirs(os.path.dirname(SECRET_PATH) or '.', exist_ok=True)
    secret = os.urandom(32)
    with open(SECRET_PATH, 'wb') as f:
        f.write(secret)
    os.chmod(SECRET_PATH, 0o600)
    return secret


def generate_token(subject: str, authority: int = 3, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Issue a signed token identifying `subject` (e.g. 'aksumael-overseer'
    or 'robocar-hub') with an authority level (see memory/goals.py's
    injected-goal authority gating for the same 1-5 convention)."""
    secret = _load_or_create_secret()
    now = int(time.time())
    payload = {'sub': subject, 'authority': authority, 'iat': now, 'exp': now + ttl_seconds}

    if _HAVE_PYJWT:
        return _pyjwt.encode(payload, secret, algorithm=ALGORITHM)

    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f'{body}.{sig}'


def validate_token(token: str) -> dict | None:
    """Return the decoded payload if `token` is well-formed, correctly
    signed, and unexpired — otherwise None. Never raises."""
    secret = _load_or_create_secret()

    if _HAVE_PYJWT:
        try:
            return _pyjwt.decode(token, secret, algorithms=[ALGORITHM])
        except Exception:
            return None

    try:
        body, sig = token.split('.', 1)
        expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padded = body + '=' * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None
