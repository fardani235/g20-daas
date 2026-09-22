"""Label-map clean-up: majority smoothing, minimum region size, class filter.

Per-pixel classifiers leave speckle and ragged edges. A majority (mode) filter
smooths them without blurring class boundaries the way a mean would, and a
sieve (rasterio's implementation of GDAL's ``SieveFilter``) merges regions
below a minimum area into their largest neighbour, so the mask — and any
polygons derived from it — are not dominated by noise.
"""

import numpy as np
from rasterio import features

NODATA = 255

_STRIP_ROWS = 1024


def _box_count(mask: np.ndarray, radius: int) -> np.ndarray:
    """Number of True cells in the (2r+1)² window around each cell (edge-clipped)."""
    r = radius
    padded = np.pad(mask.astype("int32"), r, mode="constant")
    c = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)), mode="constant")
    h, w = mask.shape
    size = 2 * r + 1
    return (c[size:size + h, size:size + w] - c[:h, size:size + w]
            - c[size:size + h, :w] + c[:h, :w])


def majority_filter(labels: np.ndarray, radius: int, class_ids: list[int]) -> np.ndarray:
    """Mode filter over a square window; nodata cells never vote and stay nodata."""
    if radius <= 0:
        return labels
    out = labels.copy()
    h = labels.shape[0]
    for y0 in range(0, h, _STRIP_ROWS):
        y1 = min(h, y0 + _STRIP_ROWS)
        a, b = max(0, y0 - radius), min(h, y1 + radius)
        strip = labels[a:b]
        best = np.full(strip.shape, -1, dtype="int32")
        winner = np.full(strip.shape, NODATA, dtype="uint8")
        for cid in class_ids:
            present = strip == cid
            if not present.any():
                continue
            count = _box_count(present, radius)
            better = count > best
            best[better] = count[better]
            winner[better] = cid
        winner[strip == NODATA] = NODATA
        winner[best <= 0] = strip[best <= 0]
        out[y0:y1] = winner[y0 - a:y1 - a]
    return out


def sieve(labels: np.ndarray, min_pixels: int) -> np.ndarray:
    """Merge connected regions smaller than ``min_pixels`` into their neighbours."""
    if min_pixels <= 1:
        return labels
    valid = labels != NODATA
    if not valid.any():
        return labels
    out = features.sieve(labels, size=int(min_pixels), mask=valid, connectivity=8)
    out = np.asarray(out, dtype="uint8")
    out[~valid] = NODATA
    return out


def apply_class_filter(labels: np.ndarray, keep_ids: set[int], background_id: int | None) -> np.ndarray:
    """Cells whose class is not kept become background (or nodata if none)."""
    out = labels.copy()
    drop = (labels != NODATA) & ~np.isin(labels, list(keep_ids))
    out[drop] = NODATA if background_id is None else background_id
    return out
