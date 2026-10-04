"""Overlapping windows with Gaussian-weighted stitching.

The model sees fixed-size windows; predictions near a window's edge are less
reliable (less context) and neighbouring windows disagree there, which shows
as seams and double boundaries in the stitched mask. Each window's output is
therefore weighted with a 2-D Gaussian centred on the window (sigma = side/8,
as in nnU-Net's sliding-window inference, Isensee et al., Nature Methods 2021)
and the overlapping windows are averaged with those weights, so the window
centre dominates and seams vanish.

Weighted planes are accumulated per *row band* only — a band is finalised and
emitted as soon as no later window can touch it — so the accumulator is a
few tile rows tall however large the raster.
"""

from dataclasses import dataclass

import numpy as np
from rasterio.windows import Window


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


def gaussian2d(h: int, w: int, sigma_scale: float = 1 / 8, floor: float = 1e-3) -> np.ndarray:
    """Separable Gaussian weights peaking at 1 in the tile centre.

    ``sigma = side * sigma_scale``; a small floor keeps every pixel's weight
    positive so a region covered by one tile only is still defined.
    """
    def ramp(n):
        x = np.arange(n, dtype="float64") - (n - 1) / 2.0
        sigma = max(n * sigma_scale, 1e-6)
        return np.exp(-0.5 * (x / sigma) ** 2)

    w2d = np.outer(ramp(h), ramp(w))
    return np.maximum(w2d, floor).astype("float32")


class PlaneBlender:
    """Accumulates weighted float planes per tile row and emits their weighted mean.

    Usage: ``begin_row(y0, h)`` → ``add(tile, planes, weights, valid)`` for every
    tile in the row → ``end_row()``; ``begin_row``/``finish`` return
    ``(y_start, planes (C, rows, W), covered (rows, W))`` for the rows that are
    now final, or None.
    """

    def __init__(self, n_planes: int, width: int, height: int):
        self.n = n_planes
        self.width = width
        self.height = height
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
                out = (py0,) + self._finalize(pacc[:, :n_final], pw[:n_final])
            carry = py0 + ph - y0
            if carry > 0:
                k = min(carry, h)
                acc[:, :k] += pacc[:, n_final:n_final + k]
                wacc[:k] += pw[n_final:n_final + k]
            self._prev = None
        self._cur = (y0, acc, wacc)
        return out

    def add(self, tile: Tile, planes: np.ndarray, weights: np.ndarray, valid: np.ndarray):
        y0, acc, wacc = self._cur
        r = tile.y0 - y0
        w = (weights * valid).astype("float32")
        acc[:, r:r + tile.h, tile.x0:tile.x0 + tile.w] += planes * w[None]
        wacc[r:r + tile.h, tile.x0:tile.x0 + tile.w] += w

    def end_row(self):
        self._prev = self._cur
        self._cur = None

    def finish(self):
        if self._prev is None:
            return None
        py0, pacc, pw = self._prev
        self._prev = None
        return (py0,) + self._finalize(pacc, pw)

    @staticmethod
    def _finalize(acc: np.ndarray, wacc: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        covered = wacc > 0
        safe = np.where(covered, wacc, 1.0)
        planes = acc / safe[None]
        return planes, covered


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
