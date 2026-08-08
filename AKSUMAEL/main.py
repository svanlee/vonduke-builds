from core.claude_bridge import start as start_claude_bridge
from core.runtime import run

if __name__ == '__main__':
    # Daemon thread, disk-backed, never raises — started before run() so the
    # bridge is already answering while the bot boots (vision/UART probing is
    # the slowest part of startup and the most useful thing to watch).
    start_claude_bridge()
    # Voice (core/voice.py — Whisper STT, piper TTS, always-on VAD) is a
    # daemon thread too, but it starts inside run() next to the rest of the
    # thread pool: it needs the live GoalStack / InnerMonologue /
    # AttentionManager / MemoryContext, none of which exist yet out here.
    run()
