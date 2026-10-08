"""What the orthophotos add — deliberately the secondary signal.

Two photo maps of the same ground differ for reasons that have nothing to do
with construction: sun angle, shadows, clouds, season, exposure. Nothing here
is allowed to change a volume; the photos are used for two things only:

``vegetation_mask``
    Green plant cover, from the Excess Green index on brightness-normalised
    colour (``2g − r − b`` with ``r + g + b = 1``). Trees and grass move the
    *surface* model between two dates without any earth being moved, so
    height change on vegetated cells is reported as its own class and kept
    out of cut and fill. RGB greenness finds green vegetation only — not dry
    grass, not bare branches.

``appearance_change``
    Cells whose height did not change but that look different: new paving,
    markings, materials. Two illumination-tolerant cues, each compared with
    how much it varies on ground the elevation says is unchanged:

    * *colour*, as chromaticity (the brightness is divided out, so a shadow
      or a darker exposure moves it far less than a change of material);
    * *structure*, as the local correlation of brightness patterns in a
      7 x 7 window (unaffected by a uniform gain or offset in brightness).

    A cell is flagged when either cue is far outside its normal range. The
    thresholds calibrate themselves: when the two photo sets disagree
    everywhere (different season, low sun), "normal" becomes wide and little
    is flagged; if more than a large share of the site is still flagged the
    pair is declared not comparable and the class is dropped.
"""

import numpy as np
from scipy import ndimage

from .align import nmad, subsample

EXG_THRESHOLD = 0.10
MIN_BRIGHTNESS = 0.08

STRUCTURE_WINDOW = 7
# Stabiliser of the structure term (SSIM's C2 for data in [0, 1]).
STRUCTURE_C = 0.03 ** 2
# A cue must be this many robust standard deviations above its median on
# unchanged ground, and above an absolute floor, to flag a cell.
APPEARANCE_Z = 6.0
COLOUR_FLOOR = 0.05
STRUCTURE_FLOOR = 0.6
# Above this share of the compared area the photos are considered not comparable.
MAX_APPEARANCE_SHARE = 0.35

_EIGHT = np.ones((3, 3), dtype=bool)


def chromaticity(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Brightness-normalised red and green, and the brightness itself (mean of RGB)."""
    total = rgb.sum(axis=0)
    safe = np.maximum(total, 1e-6)
    return rgb[0] / safe, rgb[1] / safe, total / 3.0


def vegetation_mask(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Cells covered by green vegetation (Excess Green above ``EXG_THRESHOLD``)."""
    r, g, brightness = chromaticity(rgb)
    b = 1.0 - r - g
    exg = 2.0 * g - r - b
    return valid & (brightness > MIN_BRIGHTNESS) & (exg > EXG_THRESHOLD)


def _threshold(values: np.ndarray, floor: float) -> float:
    sample = subsample(values, 200_000)
    if sample.size < 100:
        return float("inf")
    med = float(np.median(sample))
    return max(floor, med + APPEARANCE_Z * max(nmad(sample, med), 1e-4))


def appearance_change(rgb_before: np.ndarray, valid_before: np.ndarray,
                      rgb_after: np.ndarray, valid_after: np.ndarray,
                      reference: np.ndarray) -> tuple[np.ndarray, dict]:
    """Cells that look different between the two orthophotos.

    ``reference`` marks the cells to calibrate on (valid in both photos and,
    when elevation is available, unchanged in height and not vegetated).
    Returns the flagged mask and the diagnostics that explain it.
    """
    valid = valid_before & valid_after
    rb, gb, lb = chromaticity(rgb_before)
    ra, ga, la = chromaticity(rgb_after)

    # Colour: remove the global white-balance difference, smooth lightly, take the distance.
    ref = reference & valid
    if ref.sum() < 100:
        ref = valid
    dr = ra - rb
    dg = ga - gb
    dr -= float(np.median(subsample(dr[ref], 200_000))) if ref.any() else 0.0
    dg -= float(np.median(subsample(dg[ref], 200_000))) if ref.any() else 0.0
    dr = ndimage.uniform_filter(dr, 3)
    dg = ndimage.uniform_filter(dg, 3)
    colour = np.hypot(dr, dg)
    del dr, dg, rb, gb, ra, ga

    # Structure: 1 − local correlation of brightness (contrast-stabilised).
    w = STRUCTURE_WINDOW
    mb, ma = ndimage.uniform_filter(lb, w), ndimage.uniform_filter(la, w)
    vb = ndimage.uniform_filter(lb * lb, w) - mb * mb
    va = ndimage.uniform_filter(la * la, w) - ma * ma
    cov = ndimage.uniform_filter(lb * la, w) - mb * ma
    structure = 1.0 - (2.0 * cov + STRUCTURE_C) / (np.maximum(vb, 0) + np.maximum(va, 0) + STRUCTURE_C)
    del mb, ma, vb, va, cov, lb, la

    # Only where the whole window is real image in both photos.
    interior = ndimage.minimum_filter(valid.astype("uint8"), size=w, mode="constant", cval=0).astype(bool)
    ref &= interior
    colour_thr = _threshold(colour[ref], COLOUR_FLOOR)
    structure_thr = _threshold(structure[ref], STRUCTURE_FLOOR)
    flagged = interior & ((colour > colour_thr) | (structure > structure_thr))
    # Thin responses are shadow edges and seams, not surfaces that changed.
    flagged = ndimage.binary_opening(flagged, structure=_EIGHT)
    info = {
        "colour_threshold": None if not np.isfinite(colour_thr) else round(colour_thr, 4),
        "structure_threshold": None if not np.isfinite(structure_thr) else round(structure_thr, 4),
        "calibration_cells": int(ref.sum()),
    }
    return flagged, info
