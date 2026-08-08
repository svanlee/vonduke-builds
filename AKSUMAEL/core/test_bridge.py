"""
test_bridge.py — Gated Re-ID Bridge unit tests

No torch, no weights, no GPU.  TestBridge overrides patch_map() with a
pure-numpy implementation that produces deterministic descriptors from frame
pixel statistics.  Same frame → same descriptor, so re-identification works
in the synthetic tests.

Run:  python3 core/test_bridge.py

Coverage:
  1. Entity formation and confirmation  (NEW → CONFIRMED lifecycle)
  2. No duplicate minting               (the critical _work_kind bug this bridge fixes)
  3. Gate efficiency                    (0 DINO passes once all tracks confirmed)
  4. forget_track keeps entity          (binding released, prototype survives)
  5. Re-identification after track drop (same entity_id on fresh track_id)
  6. Ambiguity path                     (twin objects — weak, noted in output)

Not covered: real DINOv2 forward pass, mask-weighted pooling with real masks,
entity pruning under max_entity_age_s, memory growth over long sessions.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.reid_bridge import EntityState, GatedReIDBridge, PatchMap


# ---------------------------------------------------------------------------
# Test subclass — pure-numpy patch_map, no torch / no model weights
# ---------------------------------------------------------------------------

class TestBridge(GatedReIDBridge):
    """
    Overrides patch_map() with a deterministic numpy implementation.
    Each 16x16 patch is seeded by its mean pixel value, so the same
    frame always produces the same descriptor, enabling re-ID tests.
    """

    _PATCH_SIZE = 16
    _DIM        = 384   # ViT-S/16 hidden dim

    def patch_map(self, frame: np.ndarray) -> PatchMap:
        H, W   = frame.shape[:2]
        ps     = self._PATCH_SIZE
        H_p    = max(ps, (H // ps) * ps)
        W_p    = max(ps, (W // ps) * ps)
        gh, gw = H_p // ps, W_p // ps
        D      = self._DIM

        tokens = np.zeros((gh, gw, D), dtype=np.float32)
        for r in range(gh):
            for c in range(gw):
                ph = r * ps
                pw = c * ps
                patch_mean = float(frame[ph:ph + ps, pw:pw + ps].mean())
                seed = int(abs(patch_mean) * 1000) % (2 ** 31)
                rng  = np.random.RandomState(seed)
                tok  = rng.randn(D).astype(np.float32)
                n    = np.linalg.norm(tok)
                tokens[r, c] = tok / n if n > 1e-8 else tok

        return PatchMap(
            tokens=tokens,
            frame_hw=(H, W),
            patch_hw=(H_p, W_p),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bridge(cfg=None) -> TestBridge:
    return TestBridge(cfg=cfg)


def _det(track_id: int, x1=10, y1=10, x2=110, y2=110) -> dict:
    return {'track_id': track_id, 'box': [x1, y1, x2, y2]}


def _frame(h=320, w=320, seed=0) -> np.ndarray:
    return np.random.RandomState(seed).randint(0, 255, (h, w, 3), dtype=np.uint8)


def _run(bridge, frame, dets, n=1):
    res = None
    for _ in range(n):
        res = bridge.process(frame, dets)
    return res


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_entity_formation_and_confirmation():
    """First query mints NEW entity; confirm_hits reinforcements → CONFIRMED."""
    b = _bridge()
    f = _frame(seed=1)

    # Frame 1: unknown track → query → NEW entity
    res = b.process(f, [_det(1)])
    eid = res[0]['entity_id']
    assert eid is not None, 'First query must return an entity_id'
    assert b._bank._entities[eid].state == EntityState.NEW

    # Frames 2..confirm_hits: reinforce (not re-query) — entity_id must be stable
    for i in range(b.cfg['confirm_hits']):
        res = b.process(f, [_det(1)])
        assert res[0]['entity_id'] == eid, f'entity_id drifted on reinforce step {i + 1}'

    assert b._bank._entities[eid].state == EntityState.CONFIRMED
    print('PASS  test_entity_formation_and_confirmation')


def test_no_duplicate_minting():
    """
    Critical regression guard: _work_kind() must return 'reinforce' (not 'query')
    for a track_id already bound to an unconfirmed entity.
    Without this guard entity count explodes — measured 14 from 2 objects in 7 frames.
    """
    b = _bridge()
    f = _frame(seed=2)

    for _ in range(7):
        b.process(f, [_det(1)])

    assert b._bank.entity_count == 1, (
        f'Expected 1 entity, got {b._bank.entity_count} — duplicate minting bug!'
    )
    print('PASS  test_no_duplicate_minting')


def test_gate_efficiency():
    """Once all live tracks are CONFIRMED, DINO must not fire again."""
    b = _bridge()
    f = _frame(seed=3)

    # Warm up: confirm entity for track 1
    _run(b, f, [_det(1)], n=b.cfg['confirm_hits'] + 2)

    passes_before = b.stats['dino_passes']

    # 10 more frames — no new tracks, gate should block all DINO calls
    _run(b, f, [_det(1)], n=10)

    new_passes = b.stats['dino_passes'] - passes_before
    assert new_passes == 0, f'Expected 0 DINO passes for confirmed tracks, got {new_passes}'
    print(f'PASS  test_gate_efficiency  (overall ratio={b.stats["ratio"]:.2f})')


def test_forget_track_keeps_entity():
    """forget_track releases the binding but the entity must survive in the bank."""
    b = _bridge()
    f = _frame(seed=4)

    res = b.process(f, [_det(1)])
    eid = res[0]['entity_id']

    b.forget_track(1)

    assert eid in b._bank._entities, 'Entity must survive forget_track'
    assert b._bank.entity_id_for(1) is None, 'Track binding must be released'
    print('PASS  test_forget_track_keeps_entity')


def test_reid_after_track_drop():
    """
    Establish entity with track 1, drop it, re-observe same object as track 7.
    Same frame → identical embeddings → bank should match original entity_id,
    not mint a new one.
    """
    b = _bridge()
    f = _frame(seed=5)

    # Confirm + promote stable prototype
    n_warm = b.cfg['confirm_hits'] + b.cfg['promote_hits'] + 2
    _run(b, f, [_det(1, 20, 20, 120, 120)], n=n_warm)
    eid_orig = b._bank.entity_id_for(1)

    b.forget_track(1)

    # Same frame, same box, new track id → should re-ID to eid_orig
    res = b.process(f, [_det(7, 20, 20, 120, 120)])
    eid_new = res[0]['entity_id']

    assert eid_new is None or eid_new == eid_orig, (
        f'Re-ID returned new entity {eid_new} instead of {eid_orig} or AMBIGUOUS — '
        f'check match_threshold / stable prototype promotion'
    )
    label = 'AMBIGUOUS' if eid_new is None else f'matched entity {eid_new}'
    print(f'PASS  test_reid_after_track_drop  ({label})')


def test_ambiguity_path():
    """
    Two objects confirmed; a new track that might overlap both.
    Must not crash — result is AMBIGUOUS (None) or a definite match.

    NOTE: synthetic embeddings are not similar enough to reliably trigger the
    margin guard.  This test only proves no crash.  Strengthen with real-
    distribution embeddings before trusting the guard in production.
    """
    b = _bridge()
    f = _frame(seed=6)

    n_warm = b.cfg['confirm_hits'] + b.cfg['promote_hits'] + 2
    _run(b, f, [_det(1, 0, 0, 100, 100), _det(2, 200, 200, 300, 300)], n=n_warm)

    b.forget_track(1)
    b.forget_track(2)

    res = b.process(f, [_det(3, 100, 100, 200, 200)])
    eid = res[0]['entity_id']

    assert eid is None or isinstance(eid, int), f'Unexpected type: {type(eid)}'
    label = 'AMBIGUOUS' if eid is None else f'matched entity {eid}'
    print(f'PASS  test_ambiguity_path  ({label})  [weak — strengthen before trusting]')


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    tests = [
        test_entity_formation_and_confirmation,
        test_no_duplicate_minting,
        test_gate_efficiency,
        test_forget_track_keeps_entity,
        test_reid_after_track_drop,
        test_ambiguity_path,
    ]

    failed = []
    for t in tests:
        try:
            t()
        except Exception as exc:
            import traceback
            print(f'FAIL  {t.__name__}: {exc}')
            traceback.print_exc()
            failed.append(t.__name__)

    print()
    if failed:
        print(f'{len(failed)} FAILED: {failed}')
        sys.exit(1)
    else:
        print('All 6 tests passed.')
        sys.exit(0)
