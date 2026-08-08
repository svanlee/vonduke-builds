"""Tests for core/telegram_channel.py.

No test here touches the Telegram API. The poller is exercised only
through TelegramChannel._enqueue() (the seam the PTB handler calls into)
and through a stubbed Application, so the suite runs offline and with no
token present."""

import asyncio
import os
import threading
import time
import types

import pytest

import config
from core import telegram_channel as tc
from core.telegram_channel import TelegramChannel, TelegramMessage


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch, tmp_path):
    """The real rig may have a token in the env or in
    ~/.config/telegram/token. Neither must leak into these tests."""
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.setattr(config, 'TELEGRAM_TOKEN_FILE',
                        str(tmp_path / 'no-such-token'), raising=False)
    monkeypatch.setattr(config, 'TELEGRAM_ALLOWED_CHAT_IDS', None, raising=False)
    monkeypatch.setattr(config, 'TELEGRAM_CHAT_ID', None, raising=False)


# ── graceful degradation ───────────────────────────────────────

def test_disabled_when_no_token_anywhere():
    ch = TelegramChannel()
    assert ch.enabled is False
    assert ch.token is None


def test_disabled_channel_noops_every_method():
    ch = TelegramChannel()
    assert ch.get_pending_messages() == []
    assert ch.has_pending() is False
    assert ch.send_message('hello') is False
    assert ch.start() is False
    ch.stop()                       # must not raise with no thread running


def test_disabled_when_ptb_missing(monkeypatch):
    monkeypatch.setattr(tc, '_PTB_AVAILABLE', False)
    ch = TelegramChannel(token='123:ABC')
    assert ch.enabled is False
    assert ch.send_message('hi') is False


def test_disabled_by_flag_does_not_read_token(monkeypatch, tmp_path):
    token_file = tmp_path / 'token'
    token_file.write_text('123:SHOULD-NOT-BE-READ\n')
    monkeypatch.setattr(config, 'TELEGRAM_TOKEN_FILE', str(token_file), raising=False)
    ch = TelegramChannel(enabled=False)
    assert ch.enabled is False
    assert ch.token is None


def test_get_channel_respects_config_flag(monkeypatch):
    monkeypatch.setattr(config, 'TELEGRAM_CHANNEL_ENABLED', False, raising=False)
    monkeypatch.setattr(tc, '_channel', None)
    ch = tc.get_channel()
    assert ch.enabled is False
    assert tc.get_channel() is ch           # singleton


# ── token resolution ───────────────────────────────────────────

def test_token_from_env_wins(monkeypatch, tmp_path):
    token_file = tmp_path / 'token'
    token_file.write_text('file-token\n')
    monkeypatch.setattr(config, 'TELEGRAM_TOKEN_FILE', str(token_file), raising=False)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'env-token')
    assert tc._read_token() == 'env-token'


def test_token_from_file_is_stripped(monkeypatch, tmp_path):
    token_file = tmp_path / 'token'
    token_file.write_text('  123:ABC \n')
    monkeypatch.setattr(config, 'TELEGRAM_TOKEN_FILE', str(token_file), raising=False)
    assert tc._read_token() == '123:ABC'


def test_empty_token_file_reads_as_missing(monkeypatch, tmp_path):
    token_file = tmp_path / 'token'
    token_file.write_text('\n\n')
    monkeypatch.setattr(config, 'TELEGRAM_TOKEN_FILE', str(token_file), raising=False)
    assert tc._read_token() is None


# ── queueing and draining ──────────────────────────────────────

def test_enqueue_then_drain():
    ch = TelegramChannel(token='123:ABC')
    assert ch.has_pending() is False
    ch._enqueue('mine some iron', chat_id=42)
    ch._enqueue('then come home', chat_id=42)
    assert ch.has_pending() is True

    msgs = ch.get_pending_messages()
    assert [m.text for m in msgs] == ['mine some iron', 'then come home']
    assert all(isinstance(m, TelegramMessage) for m in msgs)
    assert all(m.chat_id == 42 for m in msgs)
    assert msgs[0].timestamp <= msgs[1].timestamp

    # Drained: a second call returns nothing, not the same messages again.
    assert ch.get_pending_messages() == []
    assert ch.has_pending() is False
    assert ch.messages_received == 2


def test_enqueue_learns_chat_id_for_replies():
    ch = TelegramChannel(token='123:ABC')
    assert ch._last_chat_id is None
    ch._enqueue('hi', chat_id=777)
    assert ch._last_chat_id == 777


def test_send_without_known_chat_id_is_a_noop():
    ch = TelegramChannel(token='123:ABC')
    assert ch.send_message('nobody to reply to') is False
    assert ch.messages_sent == 0


def test_drain_is_thread_safe():
    """The PTB handler enqueues from the poller thread while the tick loop
    drains from the main thread; nothing may be lost or duplicated."""
    ch = TelegramChannel(token='123:ABC')
    total = 200

    def producer():
        for i in range(total):
            ch._enqueue(f'msg-{i}', chat_id=1)

    drained = []
    t = threading.Thread(target=producer)
    t.start()
    while t.is_alive() or ch.has_pending():
        drained.extend(ch.get_pending_messages())
    t.join()
    drained.extend(ch.get_pending_messages())

    assert sorted(m.text for m in drained) == sorted(f'msg-{i}' for i in range(total))


# ── inbound handler (stubbed PTB update objects) ───────────────

def _fake_update(text, chat_id=5):
    return types.SimpleNamespace(
        effective_message=types.SimpleNamespace(text=text),
        effective_chat=types.SimpleNamespace(id=chat_id),
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_handler_enqueues_text():
    ch = TelegramChannel(token='123:ABC')
    _run(ch._on_message(_fake_update('dig down'), None))
    assert [m.text for m in ch.get_pending_messages()] == ['dig down']


def test_handler_ignores_blank_and_missing_text():
    ch = TelegramChannel(token='123:ABC')
    _run(ch._on_message(_fake_update('   '), None))
    _run(ch._on_message(_fake_update(None), None))
    assert ch.get_pending_messages() == []


def test_handler_respects_chat_allowlist(monkeypatch):
    monkeypatch.setattr(config, 'TELEGRAM_ALLOWED_CHAT_IDS', [999], raising=False)
    ch = TelegramChannel(token='123:ABC')
    _run(ch._on_message(_fake_update('let me in', chat_id=123), None))
    assert ch.get_pending_messages() == []
    _run(ch._on_message(_fake_update('it is me', chat_id=999), None))
    assert [m.text for m in ch.get_pending_messages()] == ['it is me']


# ── outbound, against a stub Application ───────────────────────

class _StubBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class _StubApp:
    def __init__(self):
        self.bot = _StubBot()


def _channel_with_live_loop(token='123:ABC'):
    """A channel whose _loop is a real running event loop and whose _app
    is a stub — enough for send_message()'s run_coroutine_threadsafe path
    without any network."""
    ch = TelegramChannel(token=token)
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def spin():
        asyncio.set_event_loop(loop)
        loop.call_soon(started.set)
        loop.run_forever()

    t = threading.Thread(target=spin, daemon=True)
    t.start()
    started.wait(timeout=5)
    ch._loop = loop
    ch._app = _StubApp()
    return ch, loop, t


def test_send_message_reaches_the_bot():
    ch, loop, t = _channel_with_live_loop()
    try:
        ch._enqueue('you there?', chat_id=31337)
        assert ch.send_message('I am mining at y=11.') is True
        deadline = time.time() + 5
        while not ch._app.bot.sent and time.time() < deadline:
            time.sleep(0.01)
        assert ch._app.bot.sent == [(31337, 'I am mining at y=11.')]
        assert ch.messages_sent == 1
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)


def test_send_message_truncates_to_telegram_limit():
    ch, loop, t = _channel_with_live_loop()
    try:
        ch._enqueue('go', chat_id=1)
        ch.send_message('x' * 9000)
        deadline = time.time() + 5
        while not ch._app.bot.sent and time.time() < deadline:
            time.sleep(0.01)
        assert len(ch._app.bot.sent[0][1]) == 4000
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)


def test_send_message_empty_is_a_noop():
    ch, loop, t = _channel_with_live_loop()
    try:
        ch._enqueue('go', chat_id=1)
        assert ch.send_message('') is False
        assert ch.send_message('   ') is False
        assert ch._app.bot.sent == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)


def test_send_message_when_poller_not_running():
    """Enabled with a token but never start()ed — must decline, not raise."""
    ch = TelegramChannel(token='123:ABC')
    ch._enqueue('hello', chat_id=9)
    assert ch.send_message('reply') is False
