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
    def __init__(self, collector, executor, auto_trainer=None, capture_fn=None):
        self._collector = collector
        self._executor  = executor
        self._auto_trainer = auto_trainer
        self._capture   = capture_fn   # callable() -> fresh frame, or None
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

        try:
            look_keys = ['a', None, 'd']   # strafe left, center, strafe right
            for key in look_keys:
                if key:
                    self._executor.execute({'key': key, 'click': None,
                                            'gamepad': {'lx': 0, 'ly': 0, 'rx': 0, 'ry': 0, 'lt': 0, 'rt': 0, 'buttons': 0},
                                            'source': 'survey'})
                    time.sleep(0.25)

                # Grab a fresh frame for this angle if a capture_fn was
                # given — otherwise every saved frame would be the same
                # pre-movement snapshot passed into run(), defeating the
                # point of a multi-angle sweep.
                shot = frame
                if self._capture:
                    fresh = self._capture()
                    if fresh is not None:
                        shot = fresh

                if self._collector:
                    if self._collector.force_save(shot, objects):
                        saved += 1
                        if self._auto_trainer:
                            self._auto_trainer.on_survey_saved(1)

            print(f'[SURVEY] saved {saved} frames (uncertain → sweep complete)')
        finally:
            self._active = False

        if self._auto_trainer and self._auto_trainer.should_train():
            self._auto_trainer.start_training()

        return saved
