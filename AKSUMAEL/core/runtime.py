# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Main Runtime Loop                  ║
# ╚══════════════════════════════════════════════════════╝

import time
import config

from vision.screen           import ScreenCapture
from vision.yolo             import YOLODetector
from core.vision_brain       import ask_vision
from core.world_model        import WorldModel
from core.cognitive          import CognitiveArchitecture
from memory.reward           import RewardSystem
from actions.executor        import ActionExecutor
from input.controller_router import ControllerRouter
from audio.tts               import TTSEngine
from audio.game_ear          import GameEar
from skills.skill_system     import SkillSystem, SkillReplayer
from ui.labeling             import LabelingUI

BANNER = """
                    ___
                   /   \\
                  ▔▔▔▔▔▔▔
              A k s ū m a e l
              ─────┬─────
                   │
              v 1 . 0 . 0
"""


def run():
    print(BANNER)
    print(f'  vision   : {config.VISION_PROVIDER} / capture card')
    print(f'  actions  : {config.ACTION_OUTPUT} → {config.PLATFORM_TARGET}')
    print(f'  blend    : {config.BLEND_MODE}')
    print(f'  tts      : {"on" if config.ENABLE_TTS else "off"}  '
          f'game_ear : {"on" if config.ENABLE_GAME_EAR else "off"}  '
          f'collect  : {"on" if getattr(config, "COLLECT_FRAMES", False) else "off"}')
    print()

    # ── Initialise all subsystems ──────────────────────────────
    cam       = ScreenCapture()
    yolo      = YOLODetector()
    world     = WorldModel()
    cognitive = CognitiveArchitecture()
    reward    = RewardSystem()
    executor  = ActionExecutor()
    router    = ControllerRouter()
    tts       = TTSEngine()
    ear       = GameEar()          # graceful if no audio device
    skills    = SkillSystem()
    replayer  = SkillReplayer(executor)
    ui        = LabelingUI(yolo, router, reward, skills=skills)

    # Optional frame collector for YOLO fine-tuning
    collector = None
    if getattr(config, 'COLLECT_FRAMES', False):
        try:
            from tools.yolo_finetune import FrameCollector
            collector = FrameCollector()
            print('[COLLECT] frame collection active')
        except Exception as e:
            print(f'[COLLECT] could not start collector: {e}')

    # Start background threads
    router.start()
    if ear.enabled:
        ear.start()

    tts.say_line('startup')

    tick = 0
    print('[AKSUMAEL] running — Ctrl+C or q in window to stop\n')

    try:
        while True:
            tick += 1
            t0 = time.time()

            # ── Joystick physical buttons ──────────────────────
            h = router.human_state
            _handle_joystick(h, ui, router, reward, tts)

            # ── Capture frame ──────────────────────────────────
            frame = cam.capture_small(width=640)
            if frame is None:
                tts.say_line('no_frame', priority=True)
                if ui.enabled:
                    ui.update(None, [])
                    if not ui.render():
                        break
                time.sleep(1)
                continue

            # ── YOLO detection ─────────────────────────────────
            objects = []
            if tick % config.YOLO_EVERY_N_TICKS == 0:
                objects = yolo.detect(frame)

            # ── Frame collection for fine-tuning ──────────────
            if collector:
                collector.maybe_save(frame, objects)

            # ── UI render + labeling input ─────────────────────
            if ui.enabled:
                ui.update(frame, objects)
                if not ui.render():
                    break
                ui_r = ui.consume_reward()
                if ui_r > 0:
                    reward.add_manual(+1.0)
                    tts.say_line('good_reward')
                elif ui_r < 0:
                    reward.add_manual(-1.0)
                    tts.say_line('bad_reward')

            if ui.paused:
                time.sleep(0.05)
                continue

            # ── Game audio events ──────────────────────────────
            if ear.enabled:
                audio_ev = ear.poll()
                if audio_ev:
                    reward.add_audio_reward(audio_ev['reward'])
                    if audio_ev.get('persona'):
                        tts.say_line(audio_ev['persona'])

            # ── Unknown object prompt ──────────────────────────
            if yolo.has_unknowns():
                unknown = yolo.pop_unknown()
                if unknown:
                    tts.say_line('unknown_object')
                    print(f'[YOLO] unknown at {unknown["box"]} '
                          f'conf={unknown["conf"]:.2f} '
                          f'— click in window to label')

            # ── Decision ──────────────────────────────────────
            action_dict = _idle()
            used_skill  = None
            src_tag     = 'idle'

            if replayer.is_active():
                name = replayer._current.name[:10] if replayer._current else '?'
                src_tag = f'REPLAY:{name}'

            else:
                # Try a learned skill first
                skill, match = skills.find_best(objects)
                if skill and match >= skills.MIN_MATCH_SCORE:
                    replayer.start(skill)
                    skills.mark_used(skill)
                    used_skill = skill
                    src_tag    = f'SK:{skill.name[:12]}'
                    action_dict.update({
                        'action':      f'skill:{skill.name}',
                        'confidence':  min(0.95, skill.avg_reward),
                        'observation': f'skill match {match:.2f} {skill.name}',
                    })

                # Fall back to Gemini
                elif tick % config.LLM_EVERY_N_TICKS == 0:
                    history = world.recent_summary(n=3)
                    if tick % (config.LLM_EVERY_N_TICKS * 10) == 0:
                        history = world.cross_session_summary() + '\n' + history
                    action_dict = ask_vision(frame, history, objects)
                    src_tag = 'LLM'
                    if tick % (config.LLM_EVERY_N_TICKS * 5) == 0:
                        tts.say_observation(
                            action_dict.get('observation', ''),
                            action_dict.get('confidence', 0))

            # ── Controller blend ───────────────────────────────
            if not replayer.is_active():
                router.update_aksumael(action_dict)
                final = router.resolve()
                executor.execute(final)
            else:
                router.update_aksumael(action_dict)
                final = action_dict
                final['source'] = src_tag

            # ── World model ────────────────────────────────────
            world.update({
                'objects': objects,
                'action':  final.get('key') or action_dict.get('action', 'wait'),
            })

            # ── Reward ────────────────────────────────────────
            r = reward.compute({'objects': objects}, action_dict)

            # ── Cognitive architecture ──────────────────────────
            cognitive.update(tick, objects, action_dict, r)

            # ── Skill mining ───────────────────────────────────
            if used_skill is None and not replayer.is_active():
                mined = skills.observe(objects, final, r)
                if mined:
                    tts.say_line('skill_learned')

            # ── Console log ────────────────────────────────────
            elapsed = round(time.time() - t0, 2)
            obs  = action_dict.get('observation', '')[:45]
            conf = action_dict.get('confidence', 0)
            ear_state = '🔊' if ear.enabled else '🔇'
            print(f'[{tick:04d}] {elapsed}s {ear_state} | '
                  f'yolo:{len(objects):2d} | {src_tag:<18} | '
                  f'conf:{conf:.2f} | r:{r:+.3f} | avg:{reward.average():+.3f} | '
                  f'{obs}')

            # ── Pace ──────────────────────────────────────────
            time.sleep(max(0, config.LOOP_INTERVAL_SEC - (time.time() - t0)))

    except KeyboardInterrupt:
        pass

    finally:
        print(f'\n[AKSUMAEL] stopped  ticks:{tick}  '
              f'avg_reward:{reward.average():.3f}  '
              f'skills:{len(skills.skills)}  '
              f'session:{world.session_num}')
        replayer.stop()
        tts.say_line('shutdown', priority=True)
        skills.save_all()
        world.save()
        time.sleep(1.5)
        executor.close()
        router.stop()
        if ear.enabled:
            ear.stop()
        tts.stop()
        ui.close()
        cam.release()


def _idle() -> dict:
    return {'observation': '', 'action': 'wait',
            'key': None, 'click': None,
            'gamepad': None, 'confidence': 0.0}


def _handle_joystick(h, ui, router, reward, tts):
    if h.buttons & 0x0001:
        reward.add_manual(+1.0)
        tts.say_line('good_reward')
    if h.buttons & 0x0002:
        reward.add_manual(-1.0)
        tts.say_line('bad_reward')
    if h.buttons & 0x0004:
        ui.paused = not ui.paused
        tts.say_line('pause' if ui.paused else 'resume', priority=True)
        time.sleep(0.3)
    if h.buttons & 0x0008:
        mode = router.cycle_blend_mode()
        tts.say_line(f'mode_{mode}', priority=True)
        time.sleep(0.3)
