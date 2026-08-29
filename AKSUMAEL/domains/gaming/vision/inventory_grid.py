# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inventory Grid Locator             ║
# ║  Finds the Minecraft GUI panel on a captured frame    ║
# ║  and fits the 36 player slot boxes to it.             ║
# ╚══════════════════════════════════════════════════════╝
#
# Why this is fitted rather than hardcoded
# ----------------------------------------
# behaviors/inventory_reader.py used to carry fixed pixel constants
# (_PANEL_X=783, _PANEL_Y=374, 36px slot pitch) derived from "1920×1080 at
# GUI scale 2". The capture card does not hand us that: on the real feed the
# slot pitch measures ~25.4px, i.e. an effective GUI scale of ~1.41, because
# the HP machine's output is rescaled somewhere between the game and
# /dev/video*. Every crop taken at the hardcoded coordinates therefore landed
# on panel background or the world behind it, which is what produced the
# "all 36 slot crops appear empty" reads in data/live.log.
#
# So nothing here assumes a scale. The panel is found by colour, then the
# slot lattice is fitted to the actual separator lines, which makes the
# reader survive a GUI-scale change, a resolution change, or a differently
# positioned window without recalibration.
#
# Layout facts this relies on (vanilla, all versions since 1.6):
#   • The GUI panel background is #c6c6c6, slot interiors #8b8b8b.
#   • Slot pitch is 18 gui units; the icon inside a slot is 16 gui units,
#     inset by 1 unit from the slot's top-left border line.
#   • On EVERY container screen — the survival inventory, a chest, a
#     crafting table — the player's own 36 slots sit at the same offsets
#     from the panel's BOTTOM edge: the 3×9 main grid's first border line
#     at bottom-83, the hotbar's at bottom-25. That invariant is what lets
#     one locator serve both `E` and an opened chest.

import cv2
import numpy as np


# ── Vanilla GUI geometry, in gui units ────────────────────────────────
_SLOT_PITCH_GUI = 18
_ICON_GUI       = 16
# Border lines of the player block, as offsets from the main grid's first
# line: three main rows (0/18/36), the line closing the third row (54), and
# the hotbar's own line (58).
_ROW_LINE_GUI   = (0, 18, 36, 54, 58)
_HOTBAR_LINE_GUI = 58
# Distance from the panel's bottom edge up to the main grid's first line.
_GRID_FROM_BOTTOM_GUI = 83

_PANEL_BG   = 198   # #c6c6c6
_PANEL_TOL  = 30

# A panel small enough to be an item sprite or big enough to be the whole
# screen is not a panel. Bounds are in pixels on the captured frame.
_MIN_PANEL_W = 120
_MIN_PANEL_H = 100
# Survival inventory is 176×166 gui, a 3-row chest 176×168 — both ≈1.05.
# A 6-row double chest is 176×222 ≈ 0.79. Accept that whole band.
_MIN_ASPECT = 0.70
_MAX_ASPECT = 1.45

# How far the fitted pitch may drift from the estimate taken off the panel
# bounding box (which includes the drop shadow, so it runs a few percent
# large) before the fit is rejected as a lock onto the wrong structure.
_PITCH_SEARCH = 0.18
_PITCH_STEP   = 0.05

# The panel colour test alone is not enough: a whitewashed wall — or a webcam
# frame of a room, which is what the survey dataset turned out to contain —
# is a large pale blob of the right size and aspect, and the lattice fit will
# happily "find" a grid in flat paint. What a real GUI has that paint does not
# is contrast between the separator lines (#c6c6c6) and the slot interiors
# (#8b8b8b), a ~59-level step.
#
# Measured over 400 gameplay frames the two populations do not come close to
# touching: 21 genuine inventory screens scored 119.9–137.1, and every
# non-GUI detection scored ≤3.4 (one outlier at 18.2). 40 sits in the void.
_MIN_LATTICE_CONTRAST = 40.0


class InvGrid:
    """A located player-inventory grid: where each of the 36 slots is.

    Slot indices match the rest of the codebase: 0-26 are the 3×9 main
    grid (top-left first, row-major), 27-35 the hotbar left to right.
    """

    __slots__ = ('x0', 'y0', 'scale_x', 'scale_y', 'panel', 'contrast')

    def __init__(self, x0: float, y0: float, scale_x: float, scale_y: float,
                 panel: tuple, contrast: float = 0.0):
        self.x0      = x0        # x of the main grid's first vertical border line
        self.y0      = y0        # y of the main grid's first horizontal border line
        self.scale_x = scale_x   # pixels per gui unit, horizontally
        self.scale_y = scale_y   # pixels per gui unit, vertically
        self.panel   = panel     # (x, y, w, h) of the detected panel
        self.contrast = contrast  # separator-vs-interior step; see the constant

    @property
    def pitch(self) -> float:
        """Slot pitch in pixels — handy for logging/debugging."""
        return _SLOT_PITCH_GUI * self.scale_x

    def slot_box(self, slot_idx: int, pad_gui: float = 0.0) -> tuple:
        """Pixel (x, y, w, h) of slot `slot_idx`'s icon, grown by `pad_gui`.

        The padding is in gui units so it scales with the GUI: asking for
        2 gui units of margin gives the sprite matcher room to slide the
        template around and absorb a sub-pixel lattice error.
        """
        if slot_idx >= 27:
            col  = slot_idx - 27
            gy   = _HOTBAR_LINE_GUI
        else:
            row, col = divmod(slot_idx, 9)
            gy   = row * _SLOT_PITCH_GUI
        gx = col * _SLOT_PITCH_GUI
        # +1 gui unit: the icon is inset by one unit from the border line.
        x = self.x0 + (gx + 1 - pad_gui) * self.scale_x
        y = self.y0 + (gy + 1 - pad_gui) * self.scale_y
        w = (_ICON_GUI + 2 * pad_gui) * self.scale_x
        h = (_ICON_GUI + 2 * pad_gui) * self.scale_y
        return x, y, w, h

    def crop(self, frame, slot_idx: int, pad_gui: float = 0.0,
             out_px: int | None = None):
        """Return slot `slot_idx` as a BGR crop, or None if it falls off-frame.

        `out_px` resamples the crop to a fixed square size. Everything
        downstream works in that canonical space so a template captured at
        one GUI scale still matches a crop taken at another.

        The resampling goes through warpAffine rather than a slice plus
        resize because the slot boxes land on fractional pixels. Rounding to
        a slice first would sample a padded crop on a different sub-pixel
        phase than an unpadded one, and a template then scores against its
        own source frame in the 0.86 range instead of ~0.99 — enough to make
        the match threshold a coin toss. An affine sample anchored on the
        exact box keeps every crop of a given slot phase-identical.
        """
        x, y, w, h = self.slot_box(slot_idx, pad_gui)
        fh, fw = frame.shape[:2]
        if w <= 1 or h <= 1:
            return None
        # Reject only slots genuinely off the frame; warpAffine replicates
        # the border for the sub-pixel fringe at the very edge.
        if x < -1 or y < -1 or x + w > fw + 1 or y + h > fh + 1:
            return None
        if out_px is None:
            out_px = int(round(w))
        kx, ky = out_px / w, out_px / h
        m = np.float32([[kx, 0.0, -x * kx],
                        [0.0, ky, -y * ky]])
        return cv2.warpAffine(frame, m, (out_px, out_px),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)


def _panel_candidates(frame) -> list:
    """Bounding boxes of blobs the colour of a GUI panel, largest first."""
    b, g, r = (frame[:, :, i].astype(np.int16) for i in range(3))
    mask = ((np.abs(b - _PANEL_BG) < _PANEL_TOL) &
            (np.abs(g - _PANEL_BG) < _PANEL_TOL) &
            (np.abs(r - _PANEL_BG) < _PANEL_TOL)).astype(np.uint8) * 255
    # Close over the slot grid so the panel reads as one blob rather than a
    # lattice of separator lines.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < _MIN_PANEL_W or h < _MIN_PANEL_H:
            continue
        if not (_MIN_ASPECT < w / float(h) < _MAX_ASPECT):
            continue
        # A real panel's own colour only covers its border and the separator
        # lattice — the slot interiors are the darker #8b8b8b — so the fill
        # ratio sits around 0.4, not near 1. The test is only here to reject
        # a coincidental gray region (fog, a stone wall) that survives the
        # close as a sparse skeleton.
        if area < 0.25 * w * h:
            continue
        out.append((x, y, w, h, area))
    out.sort(key=lambda c: -c[4])
    return out


def _fit_lattice(profile, deltas_gui, scale_est: float, origin_lo: float,
                 origin_hi: float) -> tuple:
    """Fit evenly-spaced bright separator lines to a 1-D brightness profile.

    Returns (origin, scale, score) for the (origin, pixels-per-gui-unit)
    pair whose predicted line positions land on the brightest pixels.
    `deltas_gui` are the line offsets in gui units from the first line.
    """
    best = (-1.0, 0.0, scale_est)
    lo = scale_est * (1.0 - _PITCH_SEARCH)
    hi = scale_est * (1.0 + _PITCH_SEARCH)
    n = len(profile)
    # Step the scale in units of pitch so the search resolution is ~0.05px
    # of slot pitch regardless of how big the GUI is.
    step = _PITCH_STEP / _SLOT_PITCH_GUI
    for scale in np.arange(lo, hi + step, step):
        for origin in np.arange(origin_lo, origin_hi, 0.25):
            total = 0.0
            hits = 0
            for d in deltas_gui:
                i = int(round(origin + d * scale))
                if 0 <= i < n:
                    total += profile[i]
                    hits += 1
            if hits < len(deltas_gui):
                continue          # a partly off-profile fit is not a fit
            if total > best[0]:
                best = (total, origin, scale)
    return best[1], best[2], best[0]


def locate_grid(frame, panel_hint: tuple | None = None) -> InvGrid | None:
    """Locate the player's 36 inventory slots on `frame`.

    Returns None when no GUI panel is on screen — which is also the
    reader's answer to "is the inventory actually open?", and a far more
    honest one than the old "were all 36 crops low-variance?" test, which
    said "not open" for any dark scene (see data/live.log).
    """
    if frame is None or frame.size == 0:
        return None

    candidates = [panel_hint] if panel_hint else _panel_candidates(frame)
    if not candidates:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)

    for cand in candidates[:3]:
        px, py, pw, ph = cand[:4]
        scale_est = pw / 176.0
        if scale_est <= 0.3:
            continue

        # Band containing only the player's own four rows — measured from
        # the panel's bottom so a chest's extra rows above are excluded and
        # cannot capture the fit.
        slack = 8 * scale_est
        by0 = int(py + ph - (_GRID_FROM_BOTTOM_GUI + 2) * scale_est - slack)
        by1 = int(py + ph - 4 * scale_est + slack)
        bx0, bx1 = int(px - slack), int(px + pw + slack)
        by0, bx0 = max(0, by0), max(0, bx0)
        by1 = min(frame.shape[0], by1)
        bx1 = min(frame.shape[1], bx1)
        if by1 - by0 < 40 or bx1 - bx0 < 80:
            continue
        band = gray[by0:by1, bx0:bx1]

        # A separator line is bright along its whole length; an item sprite
        # is bright only where it happens to be. Taking a low percentile
        # down each column/row keeps the lines and discards the sprites.
        col_prof = np.percentile(band, 35, axis=0)
        row_prof = np.percentile(band, 35, axis=1)

        col_deltas = [_SLOT_PITCH_GUI * c for c in range(10)]
        ox, sx, _ = _fit_lattice(col_prof, col_deltas, scale_est,
                                 0.0, 14 * scale_est)
        oy, sy, _ = _fit_lattice(row_prof, _ROW_LINE_GUI, scale_est,
                                 0.0, 16 * scale_est)

        # x and y come off the same GUI, so a pitch disagreement means one
        # of the two fits locked onto something that is not the lattice.
        if not sx or not sy or abs(sx - sy) > 0.15 * max(sx, sy):
            continue

        # Does the thing we fitted actually look like slots? Separator lines
        # must stand above the slot centres between them.
        pitch = _SLOT_PITCH_GUI * sx
        lines = [col_prof[i] for c in range(10)
                 if 0 <= (i := int(round(ox + pitch * c))) < len(col_prof)]
        mids  = [col_prof[i] for c in range(9)
                 if 0 <= (i := int(round(ox + pitch * c + pitch / 2))) < len(col_prof)]
        if not lines or not mids:
            continue
        contrast = float(np.mean(lines) - np.mean(mids))
        if contrast < _MIN_LATTICE_CONTRAST:
            continue

        return InvGrid(x0=bx0 + ox, y0=by0 + oy,
                       scale_x=sx, scale_y=sy,
                       panel=(px, py, pw, ph), contrast=contrast)

    return None
