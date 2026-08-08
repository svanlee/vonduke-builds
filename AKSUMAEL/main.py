from core.claude_bridge import start as start_claude_bridge
from core.runtime import run
from hardware.hardware_manager import start_manifest_writer

if __name__ == '__main__':
    # Daemon thread, disk-backed, never raises — started before run() so the
    # bridge is already answering while the bot boots (vision/UART probing is
    # the slowest part of startup and the most useful thing to watch).
    start_claude_bridge()
    # Also a daemon thread, and also deliberately ahead of run(): the first
    # sweep lands in data/hardware_manifest.json before anything claims a
    # /dev node, so the manifest records what was attached rather than what
    # survived the boot scramble.
    start_manifest_writer()
    # Voice (core/voice.py — Whisper STT, piper TTS, always-on VAD) is a
    # daemon thread too, but it starts inside run() next to the rest of the
    # thread pool: it needs the live GoalStack / InnerMonologue /
    # AttentionManager / MemoryContext, none of which exist yet out here.
    run()
