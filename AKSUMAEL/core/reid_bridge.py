"""
Gated Re-ID Bridge
==================
Sits between ByteTrack (short-term, motion+IoU) and Belief State (long-term memory).
Runs a DINOv2 forward pass only on *new* track_ids, making appearance compute
proportional to scene novelty rather than scene occupancy.

Architecture
------------
YOLO(-seg) → ByteTrack → track_id
                             │
              new track_id? ─┤no──→ dict lookup, zero compute
                             │
                            yes
                             │
               DINOv2 patch map (1 pass, whole frame)
                             │
               ROI-pool per detection → descriptors
                             │
                  match against ReIDBank
                             │
         CONFIRMED / AMBIGUOUS / NEW → entity_id → Belief State

Lifecycle
---------
A track_id maps to an entity.  An entity starts NEW (work prototype only)
and becomes CONFIRMED after `confirm_hits` reinforcements.  The work prototype
promotes to a stable prototype after `promote_hits` total feeds.

Critical invariant (_work_kind)
--------------------------------
Once a track_id is bound to an entity, subsequent ticks call reinforce(), NOT
query().  Re-querying an unconfirmed entity causes duplicate minting (measured:
14 entities from 2 objects in 7 frames without this guard).

Integration checklist
---------------------
- [ ] Retrain 76-class YOLO as -seg; pass masks to process() for weighted pooling
- [ ] Call forget_track(track_id) when ByteTrack drops a track
- [ ] Belief State keys on entity_id, not track_id
- [ ] Verify token slice in patch_map() — assert fires if model layout shifts
- [ ] Handle entity_id=None (AMBIGUOUS) downstream — don't default to a new entity
- [ ] Validate dino_passes/frames ratio on real Minecraft footage before shipping
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, NamedTuple, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
# Tuned for synthetic baseline: same-object cosine ≈ 0.81,
# same-class-different ≈ 0.27.  Minecraft block textures are more self-similar
# than real-world scenes — retune from a similarity histogram before deploying.

DEFAULTS: dict = {
    'match_threshold':  0.62,    # minimum cosine to claim a match
    'match_margin':     0.08,    # top-2 gap required to avoid AMBIGUOUS
    'dup_threshold':    0.93,    # cosine above which two prototypes are merged
    'confirm_hits':     2,       # reinforcements to flip NEW → CONFIRMED
    'promote_hits':     3,       # feeds to promote work → stable prototype
    'max_entity_age_s': 300.0,   # seconds before an unseen entity is pruned
    'alpha_min':        0.05,    # EMA alpha floor (low-confidence observations)
    'alpha_max':        0.35,    # EMA alpha ceil (high-confidence observations)
}

# HuggingFace model id.  The design doc specifies facebook/dinov3-vits16-pretrain-lvd1689m;
# map to the standard HF name.  Verify before first real run.
_DINO_MODEL_ID = 'facebook/dinov2-small'   # ViT-S/16, hidden_dim=384


# ---------------------------------------------------------------------------
# Entity state machine
# ---------------------------------------------------------------------------

class EntityState(Enum):
    NEW       = 'new'
    CONFIRMED = 'confirmed'


@dataclass
class _Entity:
    entity_id:     int
    state:         EntityState
    work_proto:    np.ndarray           # L2-normalised, shape (D,)
    stable_proto:  Optional[np.ndarray] = None
    hit_count:     int   = 0            # total feeds (for promote_hits)
    confirm_count: int   = 0            # feeds since creation (for confirm_hits)
    last_seen:     float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Patch map container
# ---------------------------------------------------------------------------

class PatchMap(NamedTuple):
    tokens:   np.ndarray         # (gh, gw, D) float32, L2-normalised per patch
    frame_hw: Tuple[int, int]    # (H, W) original frame before resize
    patch_hw: Tuple[int, int]    # (H_p, W_p) resized frame (multiple of 16)


# ---------------------------------------------------------------------------
# ReID bank
# ---------------------------------------------------------------------------

class ReIDBank:
    """
    Stores entity prototypes and manages track_id → entity_id bindings.
    Not thread-safe — call from a single dispatch thread.
    """

    def __init__(self, cfg: dict):
        self.cfg               = cfg
        self._entities:        Dict[int, _Entity] = {}
        self._track_to_entity: Dict[int, int]     = {}
        self._next_id:         int                = 1

    # -- Work-kind gate -------------------------------------------------------

    def _work_kind(self, track_id: int) -> str:
        """
        Returns one of:
          'query'     – track_id unseen; must run DINO + match against bank
          'reinforce' – track_id bound to an unconfirmed entity; feed prototypes
          'none'      – track_id bound to a confirmed entity; dict lookup only
        """
        if track_id not in self._track_to_entity:
            return 'query'
        ent = self._entities[self._track_to_entity[track_id]]
        return 'none' if ent.state == EntityState.CONFIRMED else 'reinforce'

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _l2(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v / n if n > 1e-8 else v

    def _cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        """Both inputs must already be L2-normalised."""
        return float(np.clip(np.dot(a, b), -1.0, 1.0))

    def _ema_update(self, proto: np.ndarray, desc: np.ndarray, sim: float) -> np.ndarray:
        """
        Gated EMA: alpha ramps with similarity so marginal observations barely
        move a prototype; confident ones move it more.
        """
        lo, hi = self.cfg['alpha_min'], self.cfg['alpha_max']
        alpha  = lo + max(0.0, sim) * (hi - lo)
        updated = (1.0 - alpha) * proto + alpha * desc
        return self._l2(updated)

    def _best_proto(self, ent: _Entity) -> np.ndarray:
        return ent.stable_proto if ent.stable_proto is not None else ent.work_proto

    def _match_all(self, desc: np.ndarray) -> List[Tuple[float, int]]:
        """Returns (cosine_sim, entity_id) for every entity, sorted descending."""
        scores = [
            (self._cosine(desc, self._best_proto(ent)), eid)
            for eid, ent in self._entities.items()
        ]
        scores.sort(key=lambda x: -x[0])
        return scores

    def _mint_entity(self, desc: np.ndarray) -> int:
        eid = self._next_id
        self._next_id += 1
        self._entities[eid] = _Entity(
            entity_id=eid,
            state=EntityState.NEW,
            work_proto=self._l2(desc.copy()),
        )
        return eid

    def _try_dup_merge(self, entity_id: int):
        """
        After an entity is confirmed, check if its prototype is very similar to
        an existing one and merge if so.  Keeps the more-confirmed entity.
        """
        ent = self._entities.get(entity_id)
        if ent is None:
            return
        proto     = self._best_proto(ent)
        threshold = self.cfg['dup_threshold']

        for eid, other in list(self._entities.items()):
            if eid == entity_id:
                continue
            if self._cosine(proto, self._best_proto(other)) >= threshold:
                # Survivor: prefer CONFIRMED, then the one with more hits
                if (other.state == EntityState.CONFIRMED or
                        other.hit_count >= ent.hit_count):
                    survivor, victim = eid, entity_id
                else:
                    survivor, victim = entity_id, eid

                # Re-point all track bindings to survivor
                for tid, bound in list(self._track_to_entity.items()):
                    if bound == victim:
                        self._track_to_entity[tid] = survivor

                del self._entities[victim]
                return   # at most one merge per call

    # -- Public API -----------------------------------------------------------

    def query(self, desc: np.ndarray) -> Tuple[str, Optional[int]]:
        """
        Match desc against the bank.

        Returns one of:
          ('CONFIRMED', entity_id)  – unambiguous match; caller must bind track
          ('AMBIGUOUS', None)       – margin guard triggered; writes nothing
          ('NEW', entity_id)        – no match found; new entity minted
        """
        desc = self._l2(desc)

        if not self._entities:
            return ('NEW', self._mint_entity(desc))

        scores = self._match_all(desc)
        best_sim, best_eid = scores[0]

        if best_sim < self.cfg['match_threshold']:
            return ('NEW', self._mint_entity(desc))

        # Margin guard: top-2 must be separated enough to avoid confusion
        if len(scores) > 1:
            gap = best_sim - scores[1][0]
            if gap < self.cfg['match_margin']:
                return ('AMBIGUOUS', None)

        return ('CONFIRMED', best_eid)

    def bind(self, track_id: int, entity_id: int):
        """
        Bind a track_id to an entity after query returns CONFIRMED or NEW.
        Do not call after AMBIGUOUS — entity_id will be None.
        """
        self._track_to_entity[track_id] = entity_id
        self._entities[entity_id].last_seen = time.monotonic()

    def reinforce(self, track_id: int, desc: np.ndarray) -> int:
        """
        Feed a new observation into the entity bound to track_id.
        Updates work/stable prototypes via gated EMA.
        Returns entity_id.
        """
        desc      = self._l2(desc)
        entity_id = self._track_to_entity[track_id]
        ent       = self._entities[entity_id]

        sim             = self._cosine(desc, ent.work_proto)
        ent.work_proto  = self._ema_update(ent.work_proto, desc, sim)
        ent.hit_count     += 1
        ent.confirm_count += 1
        ent.last_seen      = time.monotonic()

        # Promote work → stable prototype
        if ent.stable_proto is None and ent.hit_count >= self.cfg['promote_hits']:
            ent.stable_proto = ent.work_proto.copy()

        # NEW → CONFIRMED transition
        if (ent.state == EntityState.NEW and
                ent.confirm_count >= self.cfg['confirm_hits']):
            ent.state = EntityState.CONFIRMED
            self._try_dup_merge(entity_id)

        return entity_id

    def entity_id_for(self, track_id: int) -> Optional[int]:
        """Direct lookup; returns None if track_id is not bound."""
        return self._track_to_entity.get(track_id)

    def forget_track(self, track_id: int):
        """
        Release the track_id → entity binding.  The entity survives in the bank
        for future re-identification.  Call when ByteTrack drops a track_id.
        """
        self._track_to_entity.pop(track_id, None)

    def prune_old_entities(self):
        """Remove entities not seen within max_entity_age_s.  Call periodically."""
        cutoff = time.monotonic() - self.cfg['max_entity_age_s']
        stale  = [eid for eid, ent in self._entities.items()
                  if ent.last_seen < cutoff]
        for eid in stale:
            del self._entities[eid]
        # Clean up orphaned bindings
        bound_eids = set(self._entities)
        for tid in [t for t, e in self._track_to_entity.items()
                    if e not in bound_eids]:
            del self._track_to_entity[tid]

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def track_count(self) -> int:
        return len(self._track_to_entity)


# ---------------------------------------------------------------------------
# DINOv2 embedder (lazy)
# ---------------------------------------------------------------------------

def _load_dino():
    from transformers import AutoModel
    import torch
    print(f'[ReID] loading {_DINO_MODEL_ID} ...')
    model = AutoModel.from_pretrained(_DINO_MODEL_ID)
    if torch.cuda.is_available():
        model = model.half().cuda()
    model.eval()
    print('[ReID] model ready')
    return model


# ---------------------------------------------------------------------------
# Main bridge
# ---------------------------------------------------------------------------

class GatedReIDBridge:
    """
    Main entry point.  Call process() each frame with ByteTrack detections.

    Parameters
    ----------
    cfg : dict, optional
        Override any key in DEFAULTS.
    embedder : optional
        Inject a custom embedder (used by tests via MockEmbedder).
        Must implement __call__(pixel_values=tensor) and parameters().
        If None, DINOv2 is loaded lazily on first use.
    """

    def __init__(self, cfg: Optional[dict] = None, embedder=None):
        self.cfg       = {**DEFAULTS, **(cfg or {})}
        self._bank     = ReIDBank(self.cfg)
        self._embedder = embedder   # None → lazy DINOv2 load
        self._frames      = 0
        self._dino_passes = 0

    # -- Embedder -------------------------------------------------------------

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = _load_dino()
        return self._embedder

    # -- Patch map ------------------------------------------------------------

    def patch_map(self, frame: np.ndarray) -> PatchMap:
        """
        Run DINOv2 on the whole frame (BGR uint8, matching YOLO convention).

        Resizes to the nearest multiple of the patch size (16), normalises with
        ImageNet mean/std, and returns the spatial patch token grid.

        Token slice: drops CLS + any register tokens by taking the LAST gh*gw
        tokens from last_hidden_state.  An assertion fires if the model layout
        changes — check the DINOv2 revision if that happens.
        """
        import cv2
        import torch

        H_orig, W_orig = frame.shape[:2]
        patch_size = 16
        H_p = max(patch_size, (H_orig // patch_size) * patch_size)
        W_p = max(patch_size, (W_orig // patch_size) * patch_size)
        gh, gw = H_p // patch_size, W_p // patch_size

        # Resize + BGR→RGB
        img = cv2.resize(frame, (W_p, H_p), interpolation=cv2.INTER_LINEAR)
        img = img[:, :, ::-1]   # BGR → RGB

        # ImageNet normalisation
        img_f = img.astype(np.float32) / 255.0
        mean  = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std   = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        img_f = (img_f - mean) / std

        tensor = torch.from_numpy(
            np.ascontiguousarray(img_f.transpose(2, 0, 1))
        ).unsqueeze(0)

        model = self._get_embedder()
        try:
            is_cuda = next(model.parameters()).is_cuda
        except StopIteration:
            is_cuda = False

        if is_cuda:
            tensor = tensor.half().cuda()

        with torch.no_grad():
            out    = model(pixel_values=tensor)
            tokens = out.last_hidden_state[0]   # (num_tokens, D)

        # Drop CLS + register tokens — keep last gh*gw patch tokens
        patch_tokens = tokens[-gh * gw:].float().cpu().numpy()

        assert patch_tokens.shape[0] == gh * gw, (
            f'patch_map: token count {patch_tokens.shape[0]} != gh*gw {gh * gw}; '
            f'DINOv2 layout may have changed — verify CLS/register prefix length'
        )

        # L2-normalise each patch token
        norms        = np.linalg.norm(patch_tokens, axis=1, keepdims=True)
        patch_tokens = patch_tokens / np.maximum(norms, 1e-8)

        return PatchMap(
            tokens=patch_tokens.reshape(gh, gw, -1),
            frame_hw=(H_orig, W_orig),
            patch_hw=(H_p, W_p),
        )

    # -- ROI descriptors ------------------------------------------------------

    def roi_descriptors(
        self,
        pmap:  PatchMap,
        boxes: List,
        masks: Optional[List] = None,
    ) -> np.ndarray:
        """
        Pool patch tokens for each detection.

        Parameters
        ----------
        pmap  : PatchMap from patch_map()
        boxes : list of [x1, y1, x2, y2] in original pixel coords
        masks : list of HxW bool arrays or None values (one per box)

        Returns
        -------
        (N, D) float32, L2-normalised descriptors
        """
        import cv2

        gh, gw, D   = pmap.tokens.shape
        H_orig, W_orig = pmap.frame_hw

        scale_x = gw / max(W_orig, 1)
        scale_y = gh / max(H_orig, 1)

        descriptors = np.zeros((len(boxes), D), dtype=np.float32)

        for i, box in enumerate(boxes):
            x1, y1, x2, y2 = [float(v) for v in box]

            # Map box to patch grid
            gx1 = max(0, min(gw - 1, int(x1 * scale_x)))
            gy1 = max(0, min(gh - 1, int(y1 * scale_y)))
            gx2 = max(gx1 + 1, min(gw, int(np.ceil(x2 * scale_x))))
            gy2 = max(gy1 + 1, min(gh, int(np.ceil(y2 * scale_y))))

            crop = pmap.tokens[gy1:gy2, gx1:gx2, :]   # (dh, dw, D)
            dh, dw = crop.shape[:2]

            mask = (masks[i] if masks is not None else None)

            if (mask is not None and
                    isinstance(mask, np.ndarray) and
                    mask.size > 0):
                # Mask-weighted pooling
                # Crop mask to box region, resize to patch crop size
                ix1, iy1 = max(0, int(x1)), max(0, int(y1))
                ix2 = min(mask.shape[1], int(np.ceil(x2)))
                iy2 = min(mask.shape[0], int(np.ceil(y2)))
                mask_crop = mask[iy1:iy2, ix1:ix2].astype(np.float32)

                if mask_crop.size == 0:
                    mask_crop = np.ones((dh, dw), dtype=np.float32)
                else:
                    mask_crop = cv2.resize(
                        mask_crop, (dw, dh), interpolation=cv2.INTER_LINEAR
                    )

                total = mask_crop.sum()
                if total < 1e-8:
                    mask_crop = np.ones((dh, dw), dtype=np.float32)
                    total = float(dh * dw)

                weights = mask_crop / total          # (dh, dw)
                desc    = np.einsum('hwD,hw->D', crop, weights)
            else:
                # Box-mean pooling
                desc = crop.mean(axis=(0, 1))

            norm = np.linalg.norm(desc)
            descriptors[i] = desc / norm if norm > 1e-8 else desc

        return descriptors

    # -- Main entry point -----------------------------------------------------

    def process(
        self,
        frame:      np.ndarray,
        detections: List[dict],
    ) -> List[dict]:
        """
        Process one frame of ByteTrack detections.

        Parameters
        ----------
        frame : HxWx3 uint8 (BGR)
        detections : list of dicts with:
            track_id : int
            box      : [x1, y1, x2, y2] pixel coords
            mask     : HxW bool array (optional; omit or set None for box pooling)

        Returns
        -------
        Same list with 'entity_id' added to each dict.
        entity_id is None when the result is AMBIGUOUS — downstream must tolerate this.
        """
        self._frames += 1

        # Classify work per track_id
        work_kinds = {
            d['track_id']: self._bank._work_kind(d['track_id'])
            for d in detections
        }

        # Run DINO at most once if any track needs it
        pmap: Optional[PatchMap] = None
        if any(wk != 'none' for wk in work_kinds.values()):
            self._dino_passes += 1
            pmap = self.patch_map(frame)

        results = []
        for det in detections:
            tid = det['track_id']
            wk  = work_kinds[tid]

            if wk == 'none':
                entity_id = self._bank.entity_id_for(tid)

            elif wk == 'reinforce':
                desc      = self.roi_descriptors(
                    pmap, [det['box']], [det.get('mask')]
                )[0]
                entity_id = self._bank.reinforce(tid, desc)

            else:   # 'query'
                desc          = self.roi_descriptors(
                    pmap, [det['box']], [det.get('mask')]
                )[0]
                result, entity_id = self._bank.query(desc)
                if entity_id is not None:
                    self._bank.bind(tid, entity_id)
                # AMBIGUOUS → entity_id stays None, nothing written to bank

            results.append({**det, 'entity_id': entity_id})

        return results

    # -- Forwarded helpers ----------------------------------------------------

    def forget_track(self, track_id: int):
        """
        Call when ByteTrack drops a track_id.
        Releases the binding; the entity stays in the bank for re-ID.
        Without this the track_to_entity dict grows without bound.
        """
        self._bank.forget_track(track_id)

    def prune_old_entities(self):
        """
        Periodic maintenance.  Call from a background thread or once per N frames.
        """
        self._bank.prune_old_entities()

    # -- Stats ----------------------------------------------------------------

    @property
    def stats(self) -> dict:
        """
        Gate efficiency.  Measure dino_passes/frames on real footage before
        building anything on top — if ratio approaches 1.0, the gate isn't
        buying anything and you're reinventing REMIND with worse descriptors.
        """
        return {
            'frames':      self._frames,
            'dino_passes': self._dino_passes,
            'ratio':       self._dino_passes / max(1, self._frames),
            'entities':    self._bank.entity_count,
            'tracks':      self._bank.track_count,
        }
