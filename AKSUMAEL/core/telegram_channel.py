# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Telegram Channel                   ║
# ║  Scott -> bot text in, monologue replies out.         ║
# ╚══════════════════════════════════════════════════════╝
#
# A python-telegram-bot long-poller running on its own event loop in a
# background daemon thread. The tick loop never awaits anything here:
# inbound messages land in a thread-safe queue that core/cognitive.py
# drains synchronously, and send_message() hands the coroutine to the
# poller's loop via run_coroutine_threadsafe and returns immediately.
# Same reasoning as InnerMonologue's generation thread — a blocking
# network call on the main loop freezes the display (2026-07-19).
#
# Every failure mode degrades to a no-op rather than raising: missing
# token, python-telegram-bot not installed, network down. The bot must
# keep playing Minecraft whether or not Telegram is reachable.

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from dataclasses import dataclass

import config

# Import is soft — python-telegram-bot is an optional dependency and the
# whole module no-ops without it (same pattern as sounddevice / pynput).
try:
    from telegram.ext import Application, MessageHandler, filters
    _PTB_AVAILABLE = True
except Exception:                                   # pragma: no cover
    Application = MessageHandler = filters = None
    _PTB_AVAILABLE = False

TOKEN_FILE = os.path.expanduser('~/.config/telegram/token')


@dataclass
class TelegramMessage:
    """One inbound text message from Scott."""
    text: str
    timestamp: float
    chat_id: int | None = None
    sender: str = 'scott'


def _read_token() -> str | None:
    """Env var wins over the file so a one-off run can override the
    persisted token without editing it."""
    env = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if env:
        return env
    path = getattr(config, 'TELEGRAM_TOKEN_FILE', TOKEN_FILE)
    try:
        if os.path.exists(path):
            token = open(path).read().strip()
            if token:
                return token
    except Exception as e:
        print(f'[TELEGRAM] token file unreadable ({path}): {e}')
    return None


class TelegramChannel:
    """Inbound queue + outbound send, both non-blocking.

    Construct once (core/cognitive.py owns the instance) and call
    start(). If no token is configured, `enabled` stays False and every
    method is a no-op that returns an empty/False result."""

    def __init__(self, token: str | None = None, autostart: bool = False,
                 enabled: bool = True):
        self.token   = token if token is not None else (_read_token() if enabled else None)
        self._queue: queue.Queue[TelegramMessage] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._last_chat_id: int | None = getattr(config, 'TELEGRAM_CHAT_ID', None)
        self._warned_no_chat = False
        self.messages_received = 0
        self.messages_sent     = 0

        if not enabled:
            self.enabled = False
            print('[TELEGRAM] disabled by config.TELEGRAM_CHANNEL_ENABLED')
        elif not self.token:
            self.enabled = False
            print('[TELEGRAM] no token (set TELEGRAM_BOT_TOKEN or write '
                  f'{TOKEN_FILE}) — channel disabled')
        elif not _PTB_AVAILABLE:
            self.enabled = False
            print('[TELEGRAM] python-telegram-bot not installed — channel disabled')
        else:
            self.enabled = True

        if self.enabled and autostart:
            self.start()

    # ── lifecycle ──────────────────────────────────────────────

    def start(self) -> bool:
        """Kick off the poller thread. Idempotent; returns False if the
        channel is disabled or already running."""
        if not self.enabled or self._thread is not None:
            return False
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name='telegram')
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0):
        """Signal the poller to unwind. Safe to call when not running."""
        if self._loop is None or self._stop_event is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        except Exception:
            return
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """Block until the poller is actually polling. Only used by tests
        and by callers that want to send before any message arrives."""
        return self._ready.wait(timeout) if self.enabled else False

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._run())
        except Exception as e:
            print(f'[TELEGRAM] poller died: {e}')
        finally:
            self._ready.clear()
            try:
                loop.close()
            finally:
                self._loop = None

    async def _run(self):
        # Built manually rather than via Application.run_polling(), which
        # installs SIGINT/SIGTERM handlers and therefore only works on the
        # main thread.
        self._stop_event = asyncio.Event()
        app = Application.builder().token(self.token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                        self._on_message))
        self._app = app
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        print('[TELEGRAM] polling')
        self._ready.set()
        try:
            await self._stop_event.wait()
        finally:
            self._ready.clear()
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            self._app = None
            print('[TELEGRAM] stopped')

    # ── inbound ────────────────────────────────────────────────

    async def _on_message(self, update, context):        # PTB handler
        msg = getattr(update, 'effective_message', None)
        text = (getattr(msg, 'text', None) or '').strip()
        if not text:
            return
        chat = getattr(update, 'effective_chat', None)
        chat_id = getattr(chat, 'id', None)
        allowed = getattr(config, 'TELEGRAM_ALLOWED_CHAT_IDS', None)
        if allowed and chat_id not in allowed:
            # A Telegram bot is reachable by anyone who knows its @name,
            # so an explicit allowlist is the only thing keeping strangers
            # out of the prompt. Log the id so it can be added by hand.
            print(f'[TELEGRAM] ignoring message from unlisted chat {chat_id}')
            return
        self._enqueue(text, chat_id=chat_id)

    def _enqueue(self, text: str, chat_id: int | None = None,
                 timestamp: float | None = None) -> TelegramMessage:
        """Queue an inbound message. Split out from the PTB handler so
        tests can drive the queue without a Telegram connection."""
        m = TelegramMessage(text=text,
                            timestamp=timestamp if timestamp is not None else time.time(),
                            chat_id=chat_id)
        if chat_id is not None:
            self._last_chat_id = chat_id
        self._queue.put(m)
        self.messages_received += 1
        print(f'[TELEGRAM] <- {text[:120]}')
        return m

    def has_pending(self) -> bool:
        """Cheap peek — lets the caller decide whether to act *before*
        draining, so a message is never dropped on a tick that turns out
        not to be able to handle it."""
        return not self._queue.empty()

    def get_pending_messages(self) -> list[TelegramMessage]:
        """Drain the inbound queue. Returns [] when disabled or empty."""
        out: list[TelegramMessage] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    # ── outbound ───────────────────────────────────────────────

    def send_message(self, text: str) -> bool:
        """Fire-and-forget reply to Scott. Returns True if the send was
        handed to the poller loop — not that Telegram accepted it."""
        text = (text or '').strip()
        if not self.enabled or not text:
            return False
        chat_id = self._last_chat_id
        if chat_id is None:
            if not self._warned_no_chat:
                print('[TELEGRAM] no chat_id yet — Scott must message the bot '
                      'once (or set config.TELEGRAM_CHAT_ID) before it can reply')
                self._warned_no_chat = True
            return False
        loop, app = self._loop, self._app
        if loop is None or app is None or not loop.is_running():
            print('[TELEGRAM] poller not running — dropping outbound message')
            return False
        try:
            asyncio.run_coroutine_threadsafe(
                self._send(app, chat_id, text), loop)
        except Exception as e:
            print(f'[TELEGRAM] send failed: {e}')
            return False
        self.messages_sent += 1
        return True

    async def _send(self, app, chat_id: int, text: str):
        try:
            # Telegram hard-caps a text message at 4096 characters.
            await app.bot.send_message(chat_id=chat_id, text=text[:4000])
            print(f'[TELEGRAM] -> {text[:120]}')
        except Exception as e:
            print(f'[TELEGRAM] send error: {e}')


_channel: TelegramChannel | None = None


def get_channel() -> TelegramChannel:
    """Process-wide singleton, started on first use. Returns a disabled
    channel (all methods no-op) when TELEGRAM_CHANNEL_ENABLED is off or
    no token is configured."""
    global _channel
    if _channel is None:
        on = bool(getattr(config, 'TELEGRAM_CHANNEL_ENABLED', False))
        _channel = TelegramChannel(enabled=on, autostart=on)
    return _channel
