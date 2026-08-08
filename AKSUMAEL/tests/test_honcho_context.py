"""Tests for core/honcho_context.py.

Nothing here touches a Honcho server. The SDK client is replaced with a
stub, so these assert the two properties the tick loop depends on: the
context() cadence cap, and that an unreachable server degrades to
None/no-op instead of raising into the monologue thread."""

import types

import pytest

import config
from core import honcho_context as hc
from core.honcho_context import HonchoContext, BOT_PEER, HUMAN_PEER


class _StubSession:
    def __init__(self, parent, sid):
        self.parent, self.sid = parent, sid

    def context(self, **kwargs):
        self.parent.context_calls.append((self.sid, kwargs))
        if self.parent.raise_on_context:
            raise ConnectionError('honcho down')
        return types.SimpleNamespace(
            peer_representation=self.parent.representation,
            messages=self.parent.messages,
        )

    def add_messages(self, msgs):
        if self.parent.raise_on_add:
            raise ConnectionError('honcho down')
        self.parent.added.extend(msgs)
        return msgs


class _StubPeer:
    def __init__(self, pid):
        self.id = pid

    def message(self, content):
        return (self.id, content)


class _StubClient:
    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.context_calls = []
        self.session_peers = []
        self.added = []
        self.representation = 'aksumael carries a water bucket below y=20.'
        self.messages = []
        self.raise_on_context = False
        self.raise_on_add = False

    def session(self, sid, peers=None):
        # HonchoContext always passes peers= so the get/create round-trip
        # actually happens (lazy handles otherwise 404 on a fresh workspace).
        self.session_peers.append((sid, peers))
        return _StubSession(self, sid)

    def peer(self, pid):
        return _StubPeer(pid)


@pytest.fixture
def stub(monkeypatch):
    client = _StubClient()
    monkeypatch.setattr(hc, '_HONCHO_AVAILABLE', True)
    monkeypatch.setattr(hc, 'Honcho', lambda **kw: client)
    return client


def _ctx(**kw):
    kw.setdefault('refresh_seconds', 30.0)
    return HonchoContext(**kw)


# ── graceful degradation ───────────────────────────────────────

def test_disabled_by_flag():
    c = _ctx(enabled=False)
    assert c.enabled is False
    assert c.get_context('s1') is None
    assert c.add_message('assistant', 'hi', 's1') is False
    assert c.search('lava') == []


def test_disabled_when_sdk_missing(monkeypatch):
    monkeypatch.setattr(hc, '_HONCHO_AVAILABLE', False)
    c = _ctx()
    assert c.enabled is False
    assert c.get_context('s1') is None


def test_unreachable_server_returns_none_not_raises(stub):
    stub.raise_on_context = True
    c = _ctx()
    assert c.get_context('s1') is None
    assert c._fail_streak == 1


def test_unreachable_server_makes_add_message_false(stub):
    stub.raise_on_add = True
    c = _ctx()
    assert c.add_message('assistant', 'I mined 3 diamond ore.', 's1') is False


def test_failure_backs_off_instead_of_hammering(stub):
    stub.raise_on_context = True
    c = _ctx()
    c.get_context('s1')
    assert len(stub.context_calls) == 1
    # Still inside the backoff window — no second network attempt.
    c.get_context('s1')
    c.get_context('s1')
    assert len(stub.context_calls) == 1


def test_last_good_context_survives_a_transient_outage(stub):
    c = _ctx(refresh_seconds=0.0)
    first = c.get_context('s1')
    assert first is not None
    stub.raise_on_context = True
    assert c.get_context('s1') == first


def test_recovery_clears_the_failure_streak(stub):
    stub.raise_on_context = True
    c = _ctx(refresh_seconds=0.0)
    c.get_context('s1')
    assert c._fail_streak == 1
    stub.raise_on_context = False
    c._fail_until = 0.0                 # simulate the backoff elapsing
    assert c.get_context('s1') is not None
    assert c._fail_streak == 0


# ── the cadence cap ────────────────────────────────────────────

def test_context_is_cached_not_fetched_per_call(stub):
    c = _ctx(refresh_seconds=30.0)
    for _ in range(50):
        c.get_context('s1')
    assert len(stub.context_calls) == 1
    assert c.contexts_fetched == 1


def test_force_bypasses_the_cache(stub):
    c = _ctx(refresh_seconds=30.0)
    c.get_context('s1')
    c.get_context('s1', force=True)
    assert len(stub.context_calls) == 2


def test_cache_is_per_session(stub):
    c = _ctx(refresh_seconds=30.0)
    c.get_context('s1')
    c.get_context('s2')
    assert [sid for sid, _ in stub.context_calls] == ['s1', 's2']


def test_context_asks_for_the_bot_peer_and_no_summary(stub):
    c = _ctx()
    c.get_context('s1', search_query='where is my pickaxe')
    _, kwargs = stub.context_calls[0]
    assert kwargs['peer_target'] == BOT_PEER
    assert kwargs['summary'] is False       # [summary] is disabled server-side
    assert kwargs['search_query'] == 'where is my pickaxe'


# ── formatting ─────────────────────────────────────────────────

def test_format_includes_representation_and_recent_messages(stub):
    stub.messages = [
        types.SimpleNamespace(peer_id='scott', content='how deep are you?'),
        types.SimpleNamespace(peer_id='aksumael', content='y=11, four torches left.'),
    ]
    out = _ctx().get_context('s1')
    assert 'water bucket below y=20' in out
    assert 'scott: how deep are you?' in out
    assert 'aksumael: y=11, four torches left.' in out


def test_format_returns_none_when_nothing_derived_yet(stub):
    stub.representation = None
    stub.messages = []
    assert _ctx().get_context('s1') is None


# ── writes and peer discipline ─────────────────────────────────

def test_roles_map_onto_exactly_two_peers(stub):
    c = _ctx()
    c.add_message('assistant', 'I fell into lava.', 's1')
    c.add_message('scott', 'careful down there', 's1')
    c.add_message('user', 'bring a water bucket', 's1')
    c.add_message('bot', 'noted', 's1')
    assert [pid for pid, _ in stub.added] == [BOT_PEER, HUMAN_PEER, HUMAN_PEER, BOT_PEER]


def test_only_two_peers_are_ever_created(stub):
    """Peer discipline: a peer per entity would turn Honcho into the
    entity tracker the spike deliberately kept hand-rolled."""
    c = _ctx()
    c.get_context('s1')
    c.add_message('assistant', 'hello', 's1')
    c.add_message('scott', 'hi', 's2')
    assert all(peers == [BOT_PEER, HUMAN_PEER] for _, peers in stub.session_peers)


def test_unknown_role_is_dropped(stub):
    c = _ctx()
    assert c.add_message('creeper', 'sssss', 's1') is False
    assert stub.added == []


def test_empty_content_is_dropped(stub):
    c = _ctx()
    assert c.add_message('assistant', '   ', 's1') is False
    assert stub.added == []


def test_add_message_counts_successes(stub):
    c = _ctx()
    c.add_message('assistant', 'I mined 3 diamond ore.', 's1')
    assert c.messages_added == 1
    assert stub.added == [(BOT_PEER, 'I mined 3 diamond ore.')]


def test_get_honcho_respects_config_flag(monkeypatch):
    monkeypatch.setattr(config, 'HONCHO_CONTEXT_ENABLED', False, raising=False)
    monkeypatch.setattr(hc, '_ctx', None)
    c = hc.get_honcho()
    assert c.enabled is False
    assert hc.get_honcho() is c             # singleton
