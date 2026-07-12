# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Environment Scanner                       ║
# ║                                                       ║
# ║  6-stage pipeline:                                    ║
# ║    1. Sweep  — fast horizontal rotation, log raw dets ║
# ║    2. Zoom   — aim toward each threat                 ║
# ║    3. Picture — save frame for logging                ║
# ║    4. Identify — LLM quick-read on zoomed frame       ║
# ║    5. Pathfinder — safest bearing to skill target     ║
# ║    6. Memory — store results in world_memory          ║
# ╚══════════════════════════════════════════════════════╝

import cv2
import json
import os
import time

import config

# Objects that warrant threat assessment
DANGER_LABELS = frozenset({
    'zombie', 'skeleton', 'creeper', 'spider', 'enderman',
    'lava', 'fire', 'cave_spider', 'blaze', 'ghast',
    'witch', 'pillager', 'vindicator',
})

# Sweep positions are multiples of LOOK_SCAN_STEP (px), relative to whatever
# direction is currently "center" (0) when the sweep starts.

# Forward sweep: ~270° centered on the current heading. Used while moving
# forward — there's no need to check behind if we're walking away from it.
SWEEP_POSITIONS_FORWARD = [-8, -6, -4, -2, 0, 2, 4, 6, 8]

# Full sweep: ~360° all the way around. Used when standing still or stuck,
# since a threat could be approaching from any direction.
SWEEP_POSITIONS_FULL = [-11, -8, -5, -2, 0, 2, 5, 8, 11]


class EnvironmentScanner:
    """
    Full scan pipeline. Designed to run once every SCAN_COOLDOWN_TICKS
    during EXPLORE state. Blocks for ~3-5 seconds total.
    """

    def __init__(self, executor, aim_ctrl, pipeline, ask_vision_fn):
        self.executor   = executor
        self.aim_ctrl   = aim_ctrl
        self.pipeline   = pipeline
        self.ask_vision = ask_vision_fn
        os.makedirs(config.SCAN_LOG_DIR, exist_ok=True)

    # ──────────────────────────────────────────────────────────
    # Stage 1: Sweep
    # ──────────────────────────────────────────────────────────

    def sweep(self, full_sweep: bool = False) -> list:
        """
        Rotate through sweep positions in large steps.
        Collect YOLO detections at each position.

        full_sweep=False (default): ~270° arc centered on the current
        heading — used while moving forward, since what's behind is
        already receding.
        full_sweep=True: ~360° all the way around — used when standing
        still or stuck, since a threat could be approaching from any side.

        Returns list of raw threat hits: [{bearing, label, box, conf, frame}]
        """
        positions = SWEEP_POSITIONS_FULL if full_sweep else SWEEP_POSITIONS_FORWARD
        threats = []
        current_bearing = 0  # track cumulative displacement

        for bearing_mult in positions:
            # Move from current position to target bearing
            target_dx = bearing_mult * config.LOOK_SCAN_STEP
            delta_dx  = target_dx - current_bearing

            if delta_dx != 0:
                self.executor.execute({
                    'look': {'dx': delta_dx, 'dy': 0},
                    'source': 'scan_sweep',
                })
                current_bearing = target_dx
                time.sleep(0.25)   # let the frame settle

            frame   = self.pipeline.latest_small_frame
            objects = self.pipeline.latest_objects or []

            for obj in objects:
                label = obj.get('label', '')
                if label in DANGER_LABELS:
                    threats.append({
                        'bearing':  bearing_mult,
                        'label':    label,
                        'box':      obj.get('box'),
                        'conf':     round(obj.get('conf', 0.0), 2),
                        'frame':    frame,
                    })
                    print(f'[SCAN] threat at bearing {bearing_mult:+d}: {label} '
                          f'(conf={obj.get("conf", 0):.2f})')

        # Return to center
        if current_bearing != 0:
            self.executor.execute({
                'look': {'dx': -current_bearing, 'dy': 0},
                'source': 'scan_return',
            })
            time.sleep(0.2)

        print(f'[SCAN] sweep complete — {len(threats)} threat(s) detected')
        return threats

    # ──────────────────────────────────────────────────────────
    # Stages 2-4: Zoom → Picture → Identify
    # ──────────────────────────────────────────────────────────

    def zoom_identify(self, threat: dict) -> dict:
        """
        Zoom toward a single threat, snap a picture, and run a quick LLM
        identification pass on it.
        Returns the threat dict extended with 'identified' and 'img_path'.
        """
        label = threat['label']

        # Stage 2: Zoom — rotate to threat bearing + fine aim at bbox
        bearing_dx = threat['bearing'] * config.LOOK_SCAN_STEP
        self.executor.execute({
            'look': {'dx': bearing_dx, 'dy': 0},
            'source': 'scan_zoom',
        })
        time.sleep(0.2)

        if threat.get('box'):
            self.aim_ctrl.aim_until(
                threat['box'],
                self.executor,
                max_ticks=6,
            )

        # Stage 3: Picture — grab and save the zoomed frame
        time.sleep(0.15)
        frame   = self.pipeline.latest_small_frame
        objects = self.pipeline.latest_objects or []
        ts      = int(time.time() * 1000)
        img_path = os.path.join(config.SCAN_LOG_DIR,
                                f'threat_{ts}_{label}.jpg')
        if frame is not None:
            cv2.imwrite(img_path, frame)

        # Stage 4: Identify — lightweight LLM read
        history = (
            f'[SCAN] I zoomed in on a possible {label}. '
            f'Assess: is it an active threat? How close is it? '
            f'Respond JSON only:\n'
            f'{{"threat": true/false, "type": "<mob/lava/fire>", '
            f'"distance": "<close/medium/far>", "priority": <1-3>, '
            f'"action": "<retreat/avoid/ignore>"}}'
        )
        identified = {}
        try:
            result = self.ask_vision(frame, history, objects)
            if isinstance(result, dict):
                identified = result
        except Exception as e:
            print(f'[SCAN] identify failed for {label}: {e}')
            identified = {'threat': True, 'type': label,
                          'distance': 'unknown', 'priority': 2,
                          'action': 'avoid'}

        # Return to center after zoom
        self.executor.execute({
            'look': {'dx': -bearing_dx, 'dy': 0},
            'source': 'scan_recenter',
        })
        time.sleep(0.15)

        return {**threat, 'identified': identified, 'img_path': img_path}

    # ──────────────────────────────────────────────────────────
    # Stage 5: Pathfinder
    # ──────────────────────────────────────────────────────────

    def build_path(self, identified_threats: list, target_bearing: int = 0) -> dict:
        """
        Given identified threats and a desired target bearing (-2 to +2),
        return the safest movement directive.

        Returns dict: {action, bearing, look_dx, key, reason}
        """
        # Build a set of bearings that are too dangerous to approach
        blocked = set()
        for t in identified_threats:
            ident = t.get('identified', {})
            if ident.get('threat') and ident.get('action') in ('retreat', 'avoid'):
                b = t['bearing']
                priority = ident.get('priority', 2)
                # Higher priority = wider avoidance arc
                arc = 2 if priority >= 3 else 1
                for offset in range(-arc, arc + 1):
                    blocked.add(b + offset)

        # All candidate bearings (-2 to +2)
        candidates = list(range(-2, 3))
        safe = [b for b in candidates if b not in blocked]

        if not safe:
            # Nowhere safe — retreat backward
            print('[PATHFINDER] all bearings blocked — retreating')
            return {
                'action':   'retreat',
                'bearing':  0,
                'look_dx':  0,
                'key':      's',
                'reason':   'all bearings blocked',
            }

        # Pick safe bearing closest to target
        best = min(safe, key=lambda b: abs(b - target_bearing))
        look_dx = best * (config.LOOK_SCAN_STEP // 2)  # half-step for smoother turn

        blocked_str = ','.join(str(b) for b in sorted(blocked)) or 'none'
        print(f'[PATHFINDER] target={target_bearing:+d}  blocked=[{blocked_str}]  '
              f'→ best bearing={best:+d}')

        return {
            'action':   'approach',
            'bearing':  best,
            'look_dx':  look_dx,
            'key':      'w',
            'reason':   f'safe path to bearing {best}',
        }

    # ──────────────────────────────────────────────────────────
    # Full pipeline
    # ──────────────────────────────────────────────────────────

    def run(self, world_mem, target_bearing: int = 0, full_sweep: bool = False) -> dict:
        """
        Run all 6 stages and store results in world_memory.
        full_sweep: True for a ~360° sweep (standing still / stuck),
        False for a ~270° forward-centered sweep (moving forward).
        Returns {'threats': [...], 'path': {...}}
        """
        print(f'[SCAN] === environment scan starting ({"360" if full_sweep else "270"}) ===')
        t0 = time.time()

        # 1. Sweep
        raw_threats = self.sweep(full_sweep=full_sweep)

        # 2-4. Zoom + Picture + Identify (up to SCAN_MAX_THREATS)
        to_identify = raw_threats[:config.SCAN_MAX_THREATS]
        identified  = []
        for threat in to_identify:
            ident = self.zoom_identify(threat)
            identified.append(ident)

        # 5. Pathfinder
        path = self.build_path(identified, target_bearing)

        # 6. Memory — store simple summary in world_memory
        if world_mem is not None:
            world_mem.record_scan(identified, path)

        elapsed = round(time.time() - t0, 1)
        print(f'[SCAN] === done in {elapsed}s  threats={len(identified)}  '
              f'path={path["action"]} bearing={path["bearing"]:+d} ===')

        return {'threats': identified, 'path': path}
