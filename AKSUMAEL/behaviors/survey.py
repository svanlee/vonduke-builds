# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Survey Behavior                    ║
# ║  Curiosity-driven frame collection on uncertainty     ║
# ╚══════════════════════════════════════════════════════╝
#
# Survey behavior — triggered when AKSUMAEL is uncertain.
# Saves frames from multiple angles for later training.

import time
import config


class SurveyBehavior:
    def __init__(self, collector, executor):
        self._collector = collector
        self._executor  = executor
        self._last_survey = 0.0
        self._active = False

    def should_trigger(self, objects, llm_conf: float) -> bool:
        """Return True if we're uncertain enough to warrant a survey."""
        now = time.time()
        if now - self._last_survey < config.SURVEY_COOLDOWN_SEC:
            return False
        if self._active:
            return False

        # Condition 1: low average YOLO confidence
        if objects:
            avg_conf = sum(o.get('conf', 0) for o in objects) / len(objects)
            if avg_conf < config.SURVEY_CONF_THRESH:
                return True

        # Condition 2: unknown objects detected
        if config.SURVEY_UNKNOWN_TRIGGER:
            if any(o.get('unknown') or o.get('label') in ('unknown', '', None)
                   for o in objects):
                return True

        # Condition 3: Claude reports low confidence
        if llm_conf < config.SURVEY_LLM_CONF_THRESH:
            return True

        return False

    def run(self, frame, objects) -> int:
        """
        Execute a survey sweep: look left, save, center, save, look right, save.
        Returns number of frames saved.
        """
        self._active = True
        self._last_survey = time.time()
        saved = 0

        look_keys = ['a', None, 'd']   # strafe left, center, strafe right
        for key in look_keys:
            if key:
                self._executor.execute({'key': key, 'click': None,
                                        'gamepad': {'lx': 0, 'ly': 0, 'rx': 0, 'ry': 0, 'lt': 0, 'rt': 0, 'buttons': 0},
                                        'source': 'survey'})
                time.sleep(0.25)

            # Grab a fresh frame and save it
            # (frame passed in is the current one; for subsequent angles we save what we have)
            if self._collector:
                if self._collector.force_save(frame, objects):
                    saved += 1

        print(f'[SURVEY] saved {saved} frames (uncertain → sweep complete)')
        self._active = False
        return saved
