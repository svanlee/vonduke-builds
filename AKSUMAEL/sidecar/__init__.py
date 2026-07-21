"""
AKSUMAEL sidecar — multi-machine daemon/sidecar scaffold.

Topology: the main AKSUMAEL process (this repo, wherever it runs — usually
the gameplay/vision box) is the *daemon*. A *sidecar* is a lighter process
running on a separate machine (e.g. a Pi 4 or RDK X5 driving envs/robocar_env.py)
that AKSUMAEL's overseer wants to hand high-level directives to without
running the full stack there.

sidecar/auth.py     — JWT (or HMAC-fallback) token issuance/validation.
sidecar/protocol.py — the WebSocket message schema shared by both ends.
sidecar/server.py   — the asyncio WebSocket server the sidecar machine runs
                       to receive directives from the AKSUMAEL daemon.

Not wired into core/runtime.py yet — this is a scaffold, importable and
independently testable, for a future overseer -> sidecar directive channel.
"""
