# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Game Launcher                             ║
# ║                                                       ║
# ║  Generic "get into the game" behavior. Works for any  ║
# ║  game AKSUMAEL is plugged into via KB2040.            ║
# ║                                                       ║
# ║  Sequences defined in data/skills/launch_sequences.json ║
# ║  Add a new game there — no Python changes needed.    ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time

SEQUENCES_FILE = 'data/skills/launch_sequences.json'

# YOLO labels that indicate the game is NOT running / needs launching
NOT_IN_GAME_LABELS = frozenset({
    'minecraft_title',   # Minecraft title screen
    'windows_desktop',   # Windows desktop visible
    'game_launcher',     # generic launcher screen
    'black_screen',      # nothing on capture card
    'loading_screen',    # loading — wait, don't act yet
})

# YOLO labels that mean we're definitely in-game — no launch needed
IN_GAME_LABELS = frozenset({
    'hotbar', 'health_bar', 'hunger_bar', 'xp_bar',
    'crosshair', 'armor_bar',
})


def _load_sequences() -> dict:
    if os.path.exists(SEQUENCES_FILE):
        try:
            return json.load(open(SEQUENCES_FILE))
        except Exception as e:
            print(f'[LAUNCH] could not load sequences: {e}')
    return {}


class GameLauncher:
    """
    Detects non-running game states and executes the configured launch
    sequence to get AKSUMAEL into gameplay.

    Call `should_trigger(objects)` each tick. If True, call `run()`.
    """

    def __init__(self, executor, game: str = 'minecraft'):
        self.executor  = executor
        self.game      = game
        self.sequences = _load_sequences()
        self._last_run = -999   # tick of last launch attempt
        self._cooldown = 200    # ticks between attempts (don't spam)
        self._launched = False  # True once we've successfully entered the game

    def reload_sequences(self):
        """Hot-reload sequences from disk — useful during development."""
        self.sequences = _load_sequences()

    def is_in_game(self, objects: list) -> bool:
        """True if YOLO confirms we're inside the game (HUD visible)."""
        labels = {o.get('label', '') for o in objects}
        return bool(labels & IN_GAME_LABELS)

    def should_trigger(self, objects: list, tick: int) -> bool:
        """Return True if we should attempt a launch."""
        if self._launched and self.is_in_game(objects):
            return False
        if (tick - self._last_run) < self._cooldown:
            return False
        # Not in game and we haven't tried recently — trigger
        return not self.is_in_game(objects)

    def run(self, tick: int):
        """Execute the launch sequence for the configured game."""
        seq = self.sequences.get(self.game)
        if not seq:
            print(f'[LAUNCH] no sequence defined for game: {self.game!r}')
            print(f'[LAUNCH] add it to {SEQUENCES_FILE}')
            return

        print(f'[LAUNCH] starting launch sequence for {self.game!r} '
              f'({len(seq["steps"])} steps)')
        self._last_run = tick

        for i, step in enumerate(seq['steps']):
            step_type = step.get('type', 'key')

            if step_type == 'key':
                self.executor.execute({'key': step['key']})
                print(f'[LAUNCH] step {i+1}: key={step["key"]}')

            elif step_type == 'click':
                self.executor.execute({'click': step.get('button', 'left')})
                print(f'[LAUNCH] step {i+1}: click={step.get("button","left")}')

            elif step_type == 'look':
                self.executor.execute({
                    'look': {'dx': step.get('dx', 0), 'dy': step.get('dy', 0)}
                })
                print(f'[LAUNCH] step {i+1}: look dx={step.get("dx",0)} '
                      f'dy={step.get("dy",0)}')

            elif step_type == 'wait':
                wait_s = step.get('ms', 500) / 1000.0
                print(f'[LAUNCH] step {i+1}: wait {step.get("ms",500)}ms')
                time.sleep(wait_s)
                continue   # no executor call needed

            elif step_type == 'say':
                print(f'[LAUNCH] >>> {step.get("message", "")}')

            # Per-step delay (default 200ms between inputs)
            wait_ms = step.get('wait_ms', 200)
            if wait_ms > 0:
                time.sleep(wait_ms / 1000.0)

        print(f'[LAUNCH] sequence complete — waiting for game to load...')
        self._launched = True
