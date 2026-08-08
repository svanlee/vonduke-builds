# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Honcho persistent memory           ║
# ║  Cross-session self-model, injected into the monologue║
# ╚══════════════════════════════════════════════════════╝
#
# Thin wrapper over the honcho-ai SDK (Apache-2.0) talking HTTP to a
# self-hosted Honcho server on config.HONCHO_URL. See docs/HONCHO_SPIKE.md
# for the server setup, the working config.toml, and the three blockers
# (embeddings mandatory, enable_thinking=false, 4096-token ctx).
#
# Two hard rules from the spike, both enforced here:
#
#  1. **Exactly two peers.** "aksumael" (the bot, its own peer — the
#     deriver pointed at it is what produces the self-model) and "scott".
#     A peer per mob/entity would turn Honcho into the entity tracker we
#     deliberately kept hand-rolled.
#  2. **Call context() on a cadence, not per tick.** context() is a
#     network round-trip against Postgres+pgvector, and the deriver it
#     reads from occupies mesh-llm for 2-7s per derivation — the same
#     single llama.cpp instance the vision loop needs. Results are cached
#     for HONCHO_CONTEXT_REFRESH_SECONDS.
#
# Everything degrades to None/no-op if the server is unreachable: the
# import is soft, every call is wrapped, and repeated failures back off
# so a dead server costs one attempt per backoff window rather than a
# stalled monologue thread every cadence.

from __future__ import annotations

import os
import threading
import time

import config

try:
    from honcho import Honcho
    _HONCHO_AVAILABLE = True
except Exception:                                   # pragma: no cover
    Honcho = None
    _HONCHO_AVAILABLE = False

BOT_PEER   = 'aksumael'
HUMAN_PEER = 'scott'

# Roles the caller may pass to add_message(), mapped onto the two peers.
_ROLE_PEERS = {
    'assistant': BOT_PEER,
    'aksumael':  BOT_PEER,
    'bot':       BOT_PEER,
    'self':      BOT_PEER,
    'user':      HUMAN_PEER,
    'scott':     HUMAN_PEER,
    'human':     HUMAN_PEER,
}


_BOOT_TS = time.time()


def default_session_id() -> str:
    """One Honcho session per bot process. Stable for the life of the
    run so a restart starts a new session while cross-session recall
    (client.search) still spans all of them."""
    return time.strftime('run-%Y%m%d-%H%M%S', time.localtime(_BOOT_TS))


class HonchoContext:
    """Persistent memory adapter. All methods are safe to call from the
    monologue's background thread; none are safe to call on the tick loop
    (they do blocking HTTP)."""

    def __init__(self, base_url: str | None = None,
                 workspace_id: str | None = None,
                 refresh_seconds: float | None = None,
                 enabled: bool = True):
        self.base_url = base_url or getattr(config, 'HONCHO_URL',
                                            'http://localhost:8000')
        self.workspace_id = workspace_id or getattr(config, 'HONCHO_WORKSPACE',
                                                    'aksumael')
        self.refresh_seconds = (refresh_seconds
                                if refresh_seconds is not None
                                else getattr(config, 'HONCHO_CONTEXT_REFRESH_SECONDS', 30.0))
        self.max_tokens = getattr(config, 'HONCHO_CONTEXT_MAX_TOKENS', 600)

        self._client = None
        self._peers: dict[str, object] = {}
        self._sessions: dict[str, object] = {}
        self._lock = threading.Lock()
        self._cache: dict[str, str | None] = {}     # session_id -> context text
        self._cache_ts: dict[str, float] = {}
        self._fail_until = 0.0                      # backoff deadline
        self._fail_streak = 0
        self.messages_added = 0
        self.contexts_fetched = 0

        if not enabled:
            self.enabled = False
            print('[HONCHO] disabled by config.HONCHO_CONTEXT_ENABLED')
        elif not _HONCHO_AVAILABLE:
            self.enabled = False
            print('[HONCHO] honcho-ai not installed — context disabled')
        else:
            self.enabled = True

    # ── connection ─────────────────────────────────────────────

    def _connect(self):
        """Lazy client construction. The SDK requires a non-empty API key
        even against a local server (see spike, Blocker notes) — any
        placeholder works and a leaked cloud call would 401 loudly."""
        if self._client is not None:
            return self._client
        self._client = Honcho(
            base_url=self.base_url,
            workspace_id=self.workspace_id,
            api_key=os.environ.get('HONCHO_API_KEY', 'not-needed'),
            timeout=getattr(config, 'HONCHO_TIMEOUT', 10.0),
            max_retries=0,      # we do our own backoff; retries stall the thread
        )
        return self._client

    def _peer(self, peer_id: str):
        p = self._peers.get(peer_id)
        if p is None:
            p = self._connect().peer(peer_id)
            self._peers[peer_id] = p
        return p

    def _session(self, session_id: str):
        """Get/create the session with both peers attached.

        `client.peer(id)` and `client.session(id)` are lazy handles — they
        do not touch the server, so context() against a fresh workspace
        fails with "Peer aksumael not found". Passing `peers=` forces the
        create round-trip, and this is the only place peers are ever
        created: two, always the same two (see module docstring)."""
        s = self._sessions.get(session_id)
        if s is None:
            s = self._connect().session(session_id,
                                        peers=[BOT_PEER, HUMAN_PEER])
            self._sessions[session_id] = s
        return s

    def _in_backoff(self) -> bool:
        return time.time() < self._fail_until

    def _note_failure(self, what: str, exc: Exception):
        self._fail_streak += 1
        # 5s, 10s, 20s ... capped at 5min. Reconnect from scratch next try:
        # a dead server usually means the whole stack was restarted.
        backoff = min(5.0 * (2 ** (self._fail_streak - 1)), 300.0)
        self._fail_until = time.time() + backoff
        self._client = None
        self._peers.clear()
        self._sessions.clear()
        if self._fail_streak <= 3 or self._fail_streak % 20 == 0:
            print(f'[HONCHO] {what} failed ({exc}) — degrading to no-op, '
                  f'retry in {backoff:.0f}s')

    def _note_success(self):
        if self._fail_streak:
            print('[HONCHO] reconnected')
        self._fail_streak = 0
        self._fail_until = 0.0

    # ── reads ──────────────────────────────────────────────────

    def get_context(self, session_id: str | None = None,
                    search_query: str | None = None,
                    force: bool = False) -> str | None:
        """Derived self-model + recent session history for "aksumael",
        formatted for prompt injection. Returns None when Honcho is off,
        unreachable, or has nothing derived yet.

        At most one network call per refresh_seconds per session; between
        those, the previous result is returned verbatim (including None)."""
        if not self.enabled:
            return None
        session_id = session_id or default_session_id()

        with self._lock:
            fresh = (not force
                     and session_id in self._cache_ts
                     and time.time() - self._cache_ts[session_id] < self.refresh_seconds)
            if fresh:
                return self._cache[session_id]
            if self._in_backoff():
                return self._cache.get(session_id)

        text = None
        try:
            ctx = self._session(session_id).context(
                summary=False,          # [summary] ENABLED = false server-side
                tokens=self.max_tokens,
                peer_target=BOT_PEER,
                search_query=search_query,
            )
            text = self._format(ctx)
            self._note_success()
            self.contexts_fetched += 1
        except Exception as e:
            self._note_failure('context()', e)
            with self._lock:
                # Keep serving the last good context through a transient
                # outage rather than blanking the prompt.
                return self._cache.get(session_id)

        with self._lock:
            self._cache[session_id] = text
            self._cache_ts[session_id] = time.time()
        return text

    def _format(self, ctx) -> str | None:
        """SessionContext -> a compact block. The derived representation
        is the valuable part (durable behavioural rules like "carries a
        water bucket below y=20"); recent messages are tail context."""
        parts: list[str] = []
        rep = (getattr(ctx, 'peer_representation', None) or '').strip()
        if rep:
            parts.append(rep)
        msgs = getattr(ctx, 'messages', None) or []
        recent = []
        for m in msgs[-6:]:
            content = (getattr(m, 'content', '') or '').strip()
            if not content:
                continue
            who = getattr(m, 'peer_id', '') or '?'
            recent.append(f'- {who}: {content}')
        if recent:
            parts.append('Recently:\n' + '\n'.join(recent))
        out = '\n'.join(parts).strip()
        return out or None

    def search(self, query: str, limit: int = 3) -> list[str]:
        """Cross-session episodic recall. Not on the monologue path —
        exposed for callers that want explicit lookup."""
        if not self.enabled or self._in_backoff():
            return []
        try:
            res = self._connect().search(query, limit=limit)
            self._note_success()
            return [(getattr(m, 'content', '') or '').strip()
                    for m in res if getattr(m, 'content', None)]
        except Exception as e:
            self._note_failure('search()', e)
            return []

    # ── writes ─────────────────────────────────────────────────

    def add_message(self, role: str, content: str,
                    session_id: str | None = None) -> bool:
        """Append one message to the session. Blocking HTTP — call from a
        background thread. Feed this sparingly (one summarised line per
        episode, not every tick): each message the deriver picks up costs
        2-7s of exclusive mesh-llm GPU time that the vision loop wants."""
        content = (content or '').strip()
        if not self.enabled or not content:
            return False
        if self._in_backoff():
            return False
        peer_id = _ROLE_PEERS.get((role or '').lower())
        if peer_id is None:
            print(f'[HONCHO] unknown role {role!r} — dropping message')
            return False
        session_id = session_id or default_session_id()
        try:
            self._session(session_id).add_messages(
                [self._peer(peer_id).message(content)])
            self._note_success()
            self.messages_added += 1
            return True
        except Exception as e:
            self._note_failure('add_messages()', e)
            return False


_ctx: HonchoContext | None = None


def get_honcho() -> HonchoContext:
    """Process-wide singleton. Returns a disabled instance (all methods
    no-op) when HONCHO_CONTEXT_ENABLED is off."""
    global _ctx
    if _ctx is None:
        _ctx = HonchoContext(
            enabled=bool(getattr(config, 'HONCHO_CONTEXT_ENABLED', False)))
    return _ctx
