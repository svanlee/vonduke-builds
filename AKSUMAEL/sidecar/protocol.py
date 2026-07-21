"""
Sidecar protocol — the WebSocket message schema shared by the AKSUMAEL
daemon and a sidecar (see sidecar/__init__.py for the topology).

Every message is a flat JSON object:
    {"type": <MessageType>, "payload": {...}, "auth_token": <str>, "timestamp": <float>}

`type` says what `payload` means; `auth_token` is validated with
sidecar.auth.validate_token() before payload is trusted.
"""
import json
import time

# Daemon -> sidecar
DIRECTIVE = 'directive'   # a high-level command, e.g. {"goal": "return_to_dock"}
HEARTBEAT = 'heartbeat'   # keepalive, empty payload

# Sidecar -> daemon
STATUS = 'status'         # sidecar's current state snapshot
ACK    = 'ack'            # directive received/accepted
ERROR  = 'error'          # directive rejected or sidecar-side failure

VALID_TYPES = frozenset({DIRECTIVE, HEARTBEAT, STATUS, ACK, ERROR})


def build_message(type: str, payload: dict = None, auth_token: str = None) -> dict:
    """Construct a protocol-conformant message dict, ready for json.dumps."""
    if type not in VALID_TYPES:
        raise ValueError(f'unknown message type: {type!r}')
    return {
        'type': type,
        'payload': payload or {},
        'auth_token': auth_token,
        'timestamp': time.time(),
    }


def validate_message(msg: dict) -> bool:
    """Structural check only (shape + known type) — does not check
    auth_token; callers should run that separately via
    sidecar.auth.validate_token() before trusting `payload`."""
    if not isinstance(msg, dict):
        return False
    if msg.get('type') not in VALID_TYPES:
        return False
    if not isinstance(msg.get('payload', {}), dict):
        return False
    return isinstance(msg.get('timestamp'), (int, float))


def to_json(msg: dict) -> str:
    return json.dumps(msg)


def from_json(text: str) -> dict:
    """Parse a JSON message string. Returns None (rather than raising) on
    malformed input — callers are reading off an untrusted socket."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
