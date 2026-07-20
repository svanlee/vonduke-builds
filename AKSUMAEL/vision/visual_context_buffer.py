# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Visual Context Buffer (Layer 2 scaffold)  ║
# ╚══════════════════════════════════════════════════════╝
#
# SCAFFOLD ONLY — no real implementation yet. This is the skeleton for
# ROCKET-1-style (CVPR '25, github.com/CraftJarvis/ROCKET-1) visual-temporal
# context prompting: ROCKET-1 conditions its policy on a short history of
# (frame, SAM-2 segmentation mask, target region) tuples rather than a
# single current-frame detection, so the model has continuity across
# occlusion and can reason about "the thing I was just looking at" instead
# of re-detecting from scratch every tick.
#
# TargetLock (vision/target_lock.py) is the lightweight precursor this
# buffer is meant to sit alongside: TargetLock tracks one bbox tick-to-tick
# for HUNT's immediate aim/attack loop; this buffer is a longer rolling
# window intended for a future policy/prompting layer, not for driving
# actions directly.
#
# Where SAM-2 slots in later: today `segmentation_hint` would just be the
# bbox from TargetLock.last_known_bbox (or None). Once wired up, each
# push() call would instead run the frame + target_bbox through SAM-2
# (https://github.com/facebookresearch/sam2) to get a pixel-level mask,
# and store that mask as segmentation_hint — giving a future ROCKET-1-style
# policy a precise "this is the object" region instead of a coarse box,
# plus mask-to-mask correspondence across frames for tracking through
# partial occlusion that IoU-on-boxes can't handle.

import collections


class VisualContextBuffer:
    """
    Circular buffer of the last `maxlen` (frame, segmentation_hint,
    target_bbox) tuples.

    Not wired into HUNT or any other state yet — this is groundwork for a
    future visual-temporal context prompting layer (see module docstring).
    """

    DEFAULT_MAXLEN = 16

    def __init__(self, maxlen: int = DEFAULT_MAXLEN):
        self._buf = collections.deque(maxlen=maxlen)

    def push(self, frame, segmentation_hint, target_bbox):
        """
        Record one tick's visual context.

        frame:              the tick's captured frame (BGR ndarray).
        segmentation_hint:   placeholder for a future SAM-2 mask; today this
                             should just be the current target bbox (or
                             None) — see module docstring for the SAM-2
                             upgrade path.
        target_bbox:         the locked target's bbox this tick, or None.
        """
        self._buf.append((frame, segmentation_hint, target_bbox))

    def recent(self, n: int = None):
        """Return the last `n` (frame, segmentation_hint, target_bbox)
        tuples, oldest first. `n=None` returns the whole buffer."""
        if n is None:
            return list(self._buf)
        return list(self._buf)[-n:]

    def clear(self):
        self._buf.clear()

    def __len__(self):
        return len(self._buf)
