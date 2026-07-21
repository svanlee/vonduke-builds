# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Game Launcher                             ║
# ║                                                       ║
# ║  Generic "get into the game" behavior. Works for any  ║
# ║  game AKSUMAEL is plugged into via KB2040.            ║
# ║                                                       ║
# ║  Sequences defined in data/config/launch_sequences.json ║
# ║  Add a new game there — no Python changes needed.    ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time

SEQUENCES_FILE = 'data/config/launch_sequences.json'

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

    # Consecutive not-in-game ticks required before trusting the read enough
    # to run a full ESC+Tab+Enter launch sequence. A single missed-HUD frame
    # (motion blur, brief occlusion, a YOLO false negative) must not be
    # enough — mid-gameplay that sequence can back out of the game or mash
    # inputs into whatever menu it opens (2026-07-21).
    NOT_IN_GAME_TICKS_THRESHOLD = 10

    def __init__(self, executor, game: str = 'minecraft'):
        self.executor  = executor
        self.game      = game
        self.sequences = _load_sequences()
        self._last_run = -999   # tick of last launch attempt
        self._cooldown = 200    # ticks between attempts (don't spam)
        self._not_in_game_ticks = 0

    def reload_sequences(self):
        """Hot-reload sequences from disk — useful during development."""
        self.sequences = _load_sequences()

    def is_in_game(self, objects: list) -> bool:
        """True if YOLO confirms we're inside the game (HUD visible)."""
        labels = {o.get('label', '') for o in objects}
        return bool(labels & IN_GAME_LABELS)

    def is_in_menu(self, objects: list) -> bool:
        """True if YOLO positively confirms a title/desktop/launcher/black/
        loading screen. Absence of IN_GAME_LABELS alone isn't proof of this
        — a dark scene or a bad YOLO frame can show neither set of labels
        while still very much in-game, and firing the launch sequence there
        mid-gameplay can back out of the game or mash inputs into whatever
        menu it opens (2026-07-21)."""
        labels = {o.get('label', '') for o in objects}
        return bool(labels & NOT_IN_GAME_LABELS)

    def should_trigger(self, objects: list, tick: int) -> bool:
        """Return True if we should attempt a launch."""
        if self.is_in_game(objects):
            self._not_in_game_ticks = 0
            return False
        if not self.is_in_menu(objects):
            # Neither confirmed in-game nor confirmed menu — inconclusive
            # frame. Don't count it (and don't reset either; a genuine
            # menu will keep confirming on the ticks around it).
            return False
        self._not_in_game_ticks += 1
        if (tick - self._last_run) < self._cooldown:
            return False
        return self._not_in_game_ticks >= self.NOT_IN_GAME_TICKS_THRESHOLD

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
                x_pct, y_pct = step.get('x', 50.0), step.get('y', 50.0)
                button = step.get('button', 'left')
                self.executor.execute({'click': [x_pct, y_pct], 'button': button})
                print(f'[LAUNCH] step {i+1}: click=({x_pct},{y_pct}) button={button}')

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
        self._not_in_game_ticks = 0
