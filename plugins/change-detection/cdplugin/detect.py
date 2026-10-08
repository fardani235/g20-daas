"""Separating real change from noise.

Three filters, each answering one way a cell can look changed without the
ground having moved:

1. **Registration wobble** — a building edge or a stockpile flank that sits
   one cell further east in the second survey produces a tall, thin "change".
   ``tolerant_difference`` therefore compares each cell with *everything
   within the registration tolerance* in the other survey: the cell only
   counts as raised if it is above the highest of those neighbours, and as
   lowered if it is below the lowest. By construction a pure shift of up to
   the tolerance yields zero. The test is run from both surveys' point of
   view so narrow features that appear *or* disappear are both kept.
2. **Height noise** — the tolerant difference must exceed the *level of
   detection*: ``k`` standard deviations of the measured uncertainty (noise
   on unchanged ground plus the systematic term), never less than a floor.
3. **Speckle** — what remains is grouped into connected regions and regions
   below a minimum area are dropped. No morphological opening is applied:
   it would erase trenches and walls narrower than three cells.

The tolerance also shaves the rim off every real change, so ``classify``
gives the rim back: cells within the tolerance radius (plus one) of an
established region belong to it when their plain difference reaches the
level of detection. What stays excluded is the part of a change that tapers
below that level — the last few centimetres of a stockpile's toe — so
volumes of changes with long, shallow edges are slightly conservative.

Volumes are *not* computed from the tolerant difference (it is deliberately
pessimistic) but from the plain, offset-corrected difference inside the
regions that passed.
"""

import numpy as np
from scipy import ndimage

# Class codes of the change raster.
NONE, FILL, CUT, VEGETATION, APPEARANCE = 0, 1, 2, 3, 4
CLASS_NAMES = {FILL: "fill", CUT: "cut", VEGETATION: "vegetation", APPEARANCE: "appearance"}

# An automatic level of detection never goes below this: photogrammetric
# surfaces are not trustworthy to a few centimetres however quiet they look.
MIN_AUTO_LOD_M = 0.05
MIN_REGION_CELLS = 4

_EIGHT = np.ones((3, 3), dtype=bool)


def erode_valid(valid: np.ndarray, cells: int) -> np.ndarray:
    """Shrink the valid area by ``cells`` (reconstruction edges are unreliable)."""
    if cells <= 0:
        return valid
    size = 2 * cells + 1
    return ndimage.minimum_filter(valid.astype("uint8"), size=size, mode="constant", cval=0).astype(bool)


def _envelope(surface: np.ndarray, radius: int) -> tuple[np.ndarray, np.ndarray]:
    """Lowest and highest value within ``radius`` cells (missing cells are ignored)."""
    size = 2 * radius + 1
    missing = ~np.isfinite(surface)
    lo = ndimage.minimum_filter(np.where(missing, np.float32(np.inf), surface), size=size, mode="nearest")
    hi = ndimage.maximum_filter(np.where(missing, np.float32(-np.inf), surface), size=size, mode="nearest")
    return lo, hi


def _one_sided(value: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """How far ``value`` lies outside ``[lo, hi]`` (signed; 0 inside)."""
    out = np.zeros(value.shape, dtype="float32")
    above = value > hi
    below = value < lo
    np.subtract(value, hi, out=out, where=above)
    np.subtract(value, lo, out=out, where=below)
    return out


def tolerant_difference(before: np.ndarray, after: np.ndarray, radius: int) -> np.ndarray:
    """``after − before`` made insensitive to horizontal shifts of up to ``radius`` cells.

    ``after`` must already carry the vertical correction. Cells missing in
    either surface come out as 0 (the caller masks them).
    """
    if radius <= 0:
        d = after - before
        return np.where(np.isfinite(d), d, np.float32(0)).astype("float32")
    lo, hi = _envelope(before, radius)
    d1 = _one_sided(after, lo, hi)             # after vs the neighbourhood of before
    lo, hi = _envelope(after, radius)
    d2 = -_one_sided(before, lo, hi)           # before vs the neighbourhood of after
    del lo, hi
    out = np.where(np.abs(d1) >= np.abs(d2), d1, d2)
    out[~np.isfinite(out)] = 0
    return out.astype("float32")


def level_of_detection(k: float, sigma_noise: float, sigma_sys: float, user_minimum: float) -> dict:
    """The smallest height change that counts, and how it was arrived at."""
    sigma_total = float(np.hypot(sigma_noise, sigma_sys))
    statistical = k * sigma_total
    floor = user_minimum if user_minimum > 0 else MIN_AUTO_LOD_M
    return {
        "level_of_detection_m": float(max(statistical, floor)),
        "statistical_m": float(statistical),
        "floor_m": float(floor),
        "floor_source": "min_height_change_m" if user_minimum > 0 else "automatic",
        "limited_by": "noise" if statistical >= floor else "floor",
        "sigma_total_m": sigma_total,
        "k": float(k),
    }


def established(mask: np.ndarray, min_cells: int) -> np.ndarray:
    """``mask`` without its connected patches smaller than ``min_cells``."""
    if min_cells <= 1 or not mask.any():
        return mask
    lab, n = ndimage.label(mask, structure=_EIGHT)
    keep = np.bincount(lab.ravel(), minlength=n + 1) >= min_cells
    keep[0] = False
    return keep[lab]


def restore_rim(strong: np.ndarray, candidate: np.ndarray, reach: int) -> np.ndarray:
    """``strong`` plus the ``candidate`` cells within ``reach`` cells of it."""
    if reach <= 0 or not strong.any():
        return strong
    return strong | (candidate & ndimage.binary_dilation(strong, structure=_EIGHT, iterations=reach))


def classify(tolerant: np.ndarray, dz: np.ndarray, valid: np.ndarray, lod: float,
             vegetation: np.ndarray | None = None, *, min_cells: int = 1, reach: int = 0) -> np.ndarray:
    """Per-cell class: fill, cut, or vegetation where the cell is flagged as plant cover.

    A change is *established* where the shift-tolerant difference reaches the
    level of detection over a connected patch of at least ``min_cells`` —
    speckle and displaced edges never get this far. The tolerance, however,
    also eats into the rim of every real change (by as many cells as its
    radius), which would bias every volume low by perimeter x radius x
    height. So the rim is given back: cells within ``reach`` of an
    established region whose *plain* difference reaches the same level
    belong to it. Further out the tolerant test stays in charge, so a
    displaced wall next to a real excavation is not swallowed.
    """
    classes = np.zeros(tolerant.shape, dtype="uint8")
    with np.errstate(invalid="ignore"):
        fill = restore_rim(established(valid & (tolerant >= lod), min_cells), valid & (dz >= lod), reach)
        cut = restore_rim(established(valid & (tolerant <= -lod), min_cells), valid & (dz <= -lod), reach)
    classes[fill] = FILL
    classes[cut] = CUT
    if vegetation is not None:
        classes[(classes != NONE) & vegetation] = VEGETATION
    return classes


def label_regions(classes: np.ndarray, min_cells: dict[int, int]) -> tuple[np.ndarray, list[dict]]:
    """Connected regions per class (8-connectivity), small ones removed.

    Returns an int32 label raster (0 = no region) and one record per kept
    region: ``{"id", "class", "cells"}``. ``min_cells`` maps a class code to
    its minimum region size.
    """
    labels = np.zeros(classes.shape, dtype="int32")
    regions = []
    next_id = 1
    for code in (FILL, CUT, VEGETATION, APPEARANCE):
        mask = classes == code
        if not mask.any():
            continue
        lab, n = ndimage.label(mask, structure=_EIGHT)
        sizes = np.bincount(lab.ravel(), minlength=n + 1)
        keep = sizes >= max(MIN_REGION_CELLS, int(min_cells.get(code, MIN_REGION_CELLS)))
        keep[0] = False
        kept = int(keep.sum())
        if not kept:
            continue
        remap = np.zeros(n + 1, dtype="int32")
        remap[keep] = np.arange(next_id, next_id + kept, dtype="int32")
        labels += remap[lab]
        regions.extend({"id": int(i), "class": code, "cells": int(c)}
                       for i, c in zip(remap[keep], sizes[keep]))
        next_id += kept
    return labels, regions
