"""Tiled inference with overlap blending.

Large orthophotos are processed in fixed-size tiles. Predictions of overlapping
tiles are blended with a 2-D Hann window (Pielawski & Wählby, PLOS ONE 2020,
"Introducing Hann windows for reducing edge-effects in patch-based image
segmentation"), which removes the seams and duplicated boundaries that
hard tile edges produce (see also Huang et al., IGARSS 2018, on tiling and
stitching segmentation output for remote sensing). Class probabilities are
accumulated per tile *row band* only, so memory stays flat however large the
raster: a band is finalised (arg-max) as soon as no later tile can touch it.
"""

from dataclasses import dataclass

import numpy as np
from rasterio.windows import Window

NODATA = 255


@dataclass(frozen=True)
class Tile:
    row: int
    col: int
    y0: int
    x0: int
    h: int
    w: int

    @property
    def window(self) -> Window:
        return Window(self.x0, self.y0, self.w, self.h)


def tile_layout(width: int, height: int, tile: int, overlap: int) -> tuple[list[list[Tile]], int, int]:
    """Row-major tiles covering the raster; the last row/column is shifted
    inward so every tile is full-size (when the raster is large enough).

    Returns (rows of tiles, effective tile width, effective tile height).
    """
    tw, th = min(tile, width), min(tile, height)
    overlap = max(0, min(overlap, tw - 1, th - 1)) if min(tw, th) > 1 else 0
    xs = _starts(width, tw, overlap)
    ys = _starts(height, th, overlap)
    rows = []
    for r, y0 in enumerate(ys):
        rows.append([Tile(r, c, y0, x0, th, tw) for c, x0 in enumerate(xs)])
    return rows, tw, th


def _starts(length: int, size: int, overlap: int) -> list[int]:
    step = max(1, size - overlap)
    starts = list(range(0, max(1, length - size + 1), step))
    if starts[-1] + size < length:
        starts.append(length - size)
    return starts


def hann2d(h: int, w: int, overlap: int) -> np.ndarray:
    """Blending weights: 1 in the tile core, tapering to ~0 over the overlap.

    With ``overlap == 0`` every weight is 1 (plain mosaicking). The taper is a
    Hann half-window over the overlap width on each side, which gives exactly
    the raised-cosine cross-fade between neighbours.
    """
    if overlap <= 0:
        return np.ones((h, w), dtype="float32")

    def ramp(n):
        x = np.ones(n, dtype="float32")
        k = min(overlap, n // 2)
        if k > 0:
            t = (np.arange(k) + 0.5) / k
            edge = 0.5 - 0.5 * np.cos(np.pi * t)
            x[:k] = edge
            x[n - k:] = edge[::-1]
        return x

    w2d = np.outer(ramp(h), ramp(w))
    return np.maximum(w2d, 1e-3).astype("float32")


class BandBlender:
    """Accumulates weighted class probabilities per tile row and emits labels.

    Usage: ``begin_row(y0, h)`` → ``add(tile, probs, weights)`` for every tile in
    the row → ``end_row()`` yields ``(y_start, labels_rows)`` for the rows that
    are now final; ``finish()`` yields the rest.
    """

    def __init__(self, n_classes: int, width: int, height: int, class_ids: list[int]):
        self.n = n_classes
        self.width = width
        self.height = height
        self.class_ids = np.asarray(class_ids, dtype="uint8")
        self._prev = None  # (y0, acc, wacc)
        self._cur = None

    def begin_row(self, y0: int, h: int):
        acc = np.zeros((self.n, h, self.width), dtype="float32")
        wacc = np.zeros((h, self.width), dtype="float32")
        out = None
        if self._prev is not None:
            py0, pacc, pw = self._prev
            ph = pacc.shape[1]
            n_final = min(y0 - py0, ph)
            if n_final > 0:
                out = (py0, self._finalize(pacc[:, :n_final], pw[:n_final]))
            carry = py0 + ph - y0
            if carry > 0:
                k = min(carry, h)
                acc[:, :k] += pacc[:, n_final:n_final + k]
                wacc[:k] += pw[n_final:n_final + k]
            self._prev = None
        self._cur = (y0, acc, wacc)
        return out

    def add(self, tile: Tile, probs: np.ndarray, weights: np.ndarray, valid: np.ndarray):
        y0, acc, wacc = self._cur
        r = tile.y0 - y0
        w = (weights * valid).astype("float32")
        acc[:, r:r + tile.h, tile.x0:tile.x0 + tile.w] += probs * w[None]
        wacc[r:r + tile.h, tile.x0:tile.x0 + tile.w] += w

    def end_row(self):
        self._prev = self._cur
        self._cur = None

    def finish(self):
        if self._prev is None:
            return None
        py0, pacc, pw = self._prev
        self._prev = None
        return py0, self._finalize(pacc, pw)

    def _finalize(self, acc: np.ndarray, wacc: np.ndarray) -> np.ndarray:
        labels = self.class_ids[np.argmax(acc, axis=0)]
        labels[wacc <= 0] = NODATA
        return labels


def pad_to(arr: np.ndarray, h: int, w: int) -> np.ndarray:
    """Reflect-pad the trailing two axes of ``arr`` up to (h, w)."""
    ph, pw = h - arr.shape[-2], w - arr.shape[-1]
    if ph <= 0 and pw <= 0:
        return arr
    pad = [(0, 0)] * (arr.ndim - 2) + [(0, max(0, ph)), (0, max(0, pw))]
    mode = "reflect" if arr.shape[-2] > 1 and arr.shape[-1] > 1 else "edge"
    try:
        return np.pad(arr, pad, mode=mode)
    except ValueError:
        return np.pad(arr, pad, mode="edge")
