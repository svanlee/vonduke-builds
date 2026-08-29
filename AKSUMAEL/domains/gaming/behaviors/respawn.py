# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Respawn Behavior                    ║
# ║  Detects death/respawn screen and clicks Respawn      ║
# ╚══════════════════════════════════════════════════════╝
#
# Triggered when: no HUD elements detected for N consecutive ticks
# (blank/death screen) OR Claude reports seeing a death/respawn screen
# in its observation.

import time

# HUD elements Minecraft renders as screen-space UI, unaffected by scene
# lighting — they stay visible whether the agent is in a lit field or a
# pitch-black cave, but vanish entirely on the death/respawn screen. Using
# their absence (instead of raw `len(objects) == 0`) as the death signal
# avoids false-triggering underground, where YOLO can legitimately detect
# zero *world* objects (no ore/mobs/blocks in view) for many consecutive
# ticks while still very much alive (2026-07-21: a false death trigger in a
# cave wiped an in-progress goal stack, including an active dig_up climb).
HUD_LABELS = frozenset({'hotbar', 'health_bar', 'hunger_bar', 'armor_bar', 'xp_bar'})


class RespawnBehavior:
    BLANK_TICKS_THRESHOLD = 5   # consecutive ticks with no HUD element before assuming death
    ZERO_HEALTH_TICKS_THRESHOLD = 3  # consecutive health_pct==0 reads required to corroborate
    RESPAWN_COOLDOWN = 10.0     # seconds between respawn attempts
    # How recently YOLO must have emitted a HUD box for its *absence* to mean
    # anything. "No hotbar detected" is only evidence of death if this model
    # detects hotbars at all — see _hud_signal_live below.
    HUD_SIGNAL_STALE_TICKS = 300

    def __init__(self, executor, goals=None):
        self._executor = executor
        self._goals     = goals   # optional GoalStack — forced to return_to_base on respawn
        self._blank_ticks = 0
        self._zero_health_ticks = 0
        self._last_respawn = 0.0
        # Set after a respawn click; blocks the blank+zero-health path until
        # the HUD is actually seen again. A real respawn puts the agent back
        # in the world, so the HUD returns within a frame or two and this
        # clears immediately. A false positive (GUI open) never recovers —
        # which is what let one bad read re-fire every RESPAWN_COOLDOWN
        # seconds for the whole run, blind-clicking screen centre and
        # wiping the goal stack each time (2026-08-08).
        self._awaiting_hud_recovery = False
        # Ticks since YOLO last emitted ANY HUD box, and whether it ever has.
        # The blank-HUD death signal is an *absence* read, and an absence is
        # only informative when the presence is reliably observable. In the
        # 2026-08-08 post-fix run YOLO emitted zero HUD labels across all 377
        # ticks of the session, so _blank_ticks accumulated permanently and
        # blank_screen was True on literally every tick — which silently
        # demoted the "two corroborating signals" design to a single-signal
        # detector riding on hud_reader's health_pct alone, and produced 6
        # more false deaths mid-chop with no GUI open. Tracking recency lets
        # the detector notice that YOLO isn't supplying this signal at all
        # and stop pretending its silence is evidence.
        self._tick = 0
        self._last_hud_label_tick = None

    def update(self, objects: list, last_observation: str = '', hud_unreliable: bool = False,
               health_pct: float = None) -> bool:
        """
        Call every tick. Returns True if respawn was attempted.
        objects: YOLO detected objects this tick
        last_observation: Claude's last observation string
        hud_unreliable: the HUD is known to be absent-or-unreadable for a
            reason that is NOT death — resets both absence counters, so the
            blank+zero-health path cannot fire (claude_sees_death is still
            honored). Set for two cases:
              * DIGCLIMB aiming straight down (2026-07-21) — that camera
                pitch reliably drops YOLO's hotbar/health/hunger detections
                for a few frames even though the HUD is a real screen-space
                overlay and is still on screen.
              * Any GUI screen the bot itself opened — inventory, chest,
                crafting, the Escape pause menu (2026-08-08). Minecraft
                hides the entire HUD behind those screens, so BOTH signals
                below collapse at once: YOLO sees no HUD boxes *and*
                hud_reader's fixed pixel ROIs sample UI background and
                return health 0.0. That is bit-for-bit what death looks
                like here, and it produced 70 false deaths in ~1900 ticks
                (8761 lifetime) — each one clicking blind at screen centre
                (inside the open inventory!) and wiping the goal stack to
                return_to_base. The two signals were never independent:
                they share the single root cause "HUD not on screen", so
                requiring both corroborate nothing on their own.
        health_pct: hud_reader's current health reading (0.0-1.0), if
            available. The blank-HUD signal alone is still just a YOLO
            absence read, which can false-trigger for reasons other than
            DIGCLIMB (motion blur, occlusion, a bad frame) — requiring it
            to be corroborated by health_pct==0 for several consecutive
            ticks (a single bad hud_reader sample isn't trusted either)
            makes the blank-screen path more conservative. The
            claude_sees_death fallback is untouched — it's a separate,
            already-reliable text signal (2026-07-21).
        """
        obs_lower = last_observation.lower()
        # Phrases that appear ONLY on the death screen. Bare 'respawn' and
        # bare 'score' used to be in here and are far too loose: after a
        # respawn the runtime injects "IMPORTANT: you just respawned. Return
        # to base" into the observation history (core/runtime.py), so the
        # model echoing that back re-triggered death detection off its own
        # recovery text — a self-sustaining loop (2026-08-08).
        death_keywords = ('you died', 'death screen', 'game over',
                          'respawn button', 'click respawn', 'score:')
        claude_sees_death = any(k in obs_lower for k in death_keywords)

        has_hud = any(o.get('label', '') in HUD_LABELS for o in objects)
        self._tick += 1
        if has_hud:
            self._last_hud_label_tick = self._tick
        # Is YOLO's HUD channel actually alive? Only if it has produced a HUD
        # box at some point AND did so recently. Never-seen => not live, which
        # is the case that bit us: a model that never boxes the HUD makes
        # `not has_hud` a constant, not a measurement.
        hud_signal_live = (self._last_hud_label_tick is not None
                           and (self._tick - self._last_hud_label_tick)
                               <= self.HUD_SIGNAL_STALE_TICKS)
        if hud_unreliable:
            # Neither absence signal means anything this tick — clear both,
            # so a GUI session can't bank up zero-health ticks and fire the
            # instant it closes and the HUD needs a frame to be re-detected.
            self._blank_ticks = 0
            self._zero_health_ticks = 0
        else:
            if not has_hud:
                self._blank_ticks += 1
            else:
                self._blank_ticks = 0

            if health_pct is not None and health_pct <= 0.0:
                self._zero_health_ticks += 1
            else:
                self._zero_health_ticks = 0

        # Clear the post-respawn latch as soon as the HUD is demonstrably back.
        # Checked against health_pct as well as YOLO: a positive health read
        # means hud_reader's pixel ROIs are sampling a real health bar again,
        # which proves recovery even if YOLO's HUD classes (which this model
        # emits only sporadically) never fire. Without that second path a
        # model that never boxes the HUD would latch shut permanently and
        # real deaths would stop being detected.
        if self._awaiting_hud_recovery and (
                has_hud or (isinstance(health_pct, (int, float)) and health_pct > 0.0)):
            self._awaiting_hud_recovery = False

        blank_screen = (self._blank_ticks >= self.BLANK_TICKS_THRESHOLD
                        and hud_signal_live)
        health_confirms_death = self._zero_health_ticks >= self.ZERO_HEALTH_TICKS_THRESHOLD

        # With hud_signal_live False the blank+health path can never fire and
        # claude_sees_death — the one signal that does not derive from "HUD not
        # on screen" — becomes the sole death trigger. That is the intended
        # degraded mode, not an outage: it is strictly better to miss a death
        # (the agent respawns on its own next tick anyway, and the goal stack
        # survives) than to blind-click screen centre and wipe the stack on a
        # live agent several times a minute.
        if ((blank_screen and health_confirms_death and not self._awaiting_hud_recovery)
                or claude_sees_death):
            now = time.time()
            if now - self._last_respawn > self.RESPAWN_COOLDOWN:
                self._last_respawn = now
                self._blank_ticks = 0
                self._zero_health_ticks = 0
                self._awaiting_hud_recovery = True
                print(f'[RESPAWN] death detected (blank={blank_screen}, health_confirms={health_confirms_death}, '
                      f'claude={claude_sees_death}, hud_signal_live={hud_signal_live}) — clicking respawn')
                self._executor.execute({
                    'key': None,
                    'click': [50.0, 50.0],   # Respawn button is center of death screen (~50% y); 60% was hitting "Title Screen"
                    'button': 'left',
                    'gamepad': {'lx': 0, 'ly': 0, 'rx': 0, 'ry': 0, 'lt': 0, 'rt': 0, 'buttons': 0},
                    'source': 'respawn',
                })
                if self._goals is not None:
                    # Dropped items are wherever we died — clear whatever was
                    # queued and head straight back to base/spawn to recover
                    # tools/inventory drops, rather than resuming the old goal.
                    self._goals.current = 'return_to_base'
                    self._goals.stack.clear()
                    self._goals.save()
                    print('[RESPAWN] goal forced to return_to_base')
                return True
        return False
