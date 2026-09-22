"""Height above ground (normalized DSM).

The nDSM — DSM minus DTM — is the standard elevation feature for land-cover
classification of aerial data: the ISPRS 2D semantic labelling benchmark
(Vaihingen/Potsdam) distributes it alongside the imagery, and multimodal
networks such as FuseNet/V-FuseNet (Audebert, Le Saux & Lefèvre, ISPRS J.
2018) consume exactly this channel. With only a DSM, the ground is estimated by
a morphological opening (grey erosion then dilation) of the surface — the
classic building/vegetation extraction filter of Weidner & Förstner (1995),
also the first stage of the progressive filters of Zhang et al. (2003) — with
the window given in ground metres, so the estimate is independent of GSD.
"""

import numpy as np
from rasterio.enums import Resampling
from rasterio.windows import Window

from .grid import AlignedReader, Grid, InputRaster


def _shift(a: np.ndarray, k: int, axis: int, fill=np.nan) -> np.ndarray:
    """``out[i] = a[i + k]`` with edge padding (or ``fill``) beyond the array."""
    if k == 0:
        return a
    out = np.empty_like(a)
    n = a.shape[axis]
    src = [slice(None)] * a.ndim
    dst = [slice(None)] * a.ndim
    if k > 0:
        k = min(k, n)
        src[axis] = slice(k, n)
        dst[axis] = slice(0, n - k)
        out[tuple(dst)] = a[tuple(src)]
        dst[axis] = slice(n - k, n)
        edge = [slice(None)] * a.ndim
        edge[axis] = slice(n - 1, n)
        out[tuple(dst)] = a[tuple(edge)] if fill is None else fill
    else:
        k = min(-k, n)
        src[axis] = slice(0, n - k)
        dst[axis] = slice(k, n)
        out[tuple(dst)] = a[tuple(src)]
        dst[axis] = slice(0, k)
        edge = [slice(None)] * a.ndim
        edge[axis] = slice(0, 1)
        out[tuple(dst)] = a[tuple(edge)] if fill is None else fill
    return out


def _running(a: np.ndarray, size: int, axis: int, op) -> np.ndarray:
    """Centred running ``op`` (np.fmin / np.fmax) over ``size`` cells along ``axis``.

    Windows are clipped at the array edges (equivalent to edge replication for
    min/max). Uses doubling — a window of 2k is ``op`` of two k-windows offset
    by k — so any size costs O(log size) whole-array passes.
    """
    if size <= 1:
        return a
    half = size // 2
    # Left edge-pad by `half` so the centred window at index i is the forward
    # window at padded index i; NaN fill on the right makes clipped windows
    # ignore cells beyond the end (fmin/fmax skip NaN).
    pad = [(0, 0)] * a.ndim
    pad[axis] = (half, 0)
    padded = np.pad(a, pad, mode="edge")
    result = None
    current = padded
    remaining, offset, power = size, 0, 1
    while remaining > 0:
        if remaining & 1:
            shifted = _shift(current, offset, axis, fill=np.nan)
            result = shifted if result is None else op(result, shifted)
            offset += power
        remaining >>= 1
        if remaining:
            current = op(current, _shift(current, power, axis, fill=np.nan))
            power *= 2
    index = [slice(None)] * a.ndim
    index[axis] = slice(0, a.shape[axis])
    return result[tuple(index)]


def morphological_opening(a: np.ndarray, size: int) -> np.ndarray:
    """Grey opening with a ``size``×``size`` square window, ignoring NaNs."""
    if size <= 1:
        return a.copy()
    eroded = _running(_running(a, size, 0, np.fmin), size, 1, np.fmin)
    return _running(_running(eroded, size, 0, np.fmax), size, 1, np.fmax)


def estimate_ground(dsm: np.ndarray, window_px: int) -> np.ndarray:
    """Approximate terrain under a DSM by morphological opening (see module doc).

    Objects narrower than the window are removed; the result is never above
    the surface. NaN (nodata) cells stay NaN.
    """
    ground = morphological_opening(dsm, max(1, int(window_px)))
    ground = np.fmin(ground, dsm)
    ground[np.isnan(dsm)] = np.nan
    return ground


class HeightProvider:
    """Serves the nDSM for any processing-grid window.

    - ``dsm`` + ``dtm``: exact nDSM read from both rasters.
    - ``dsm`` only: DSM minus a coarse morphological ground estimate
      (``ground_window_m``, computed once on a ≤ ``coarse_m`` grid).
    - anything else: no height (``available`` is False).
    """

    def __init__(self, inputs: dict[str, InputRaster], grid: Grid, *,
                 ground_window_m: float = 40.0, coarse_m: float = 1.0, warn=None):
        self.grid = grid
        self.mode = None
        self.dsm = self.dtm = None
        self._ground = None
        self.ground_window_m = ground_window_m
        if "dsm" in inputs and "dtm" in inputs:
            self.mode = "dsm-dtm"
            self.dsm = AlignedReader(inputs["dsm"], grid, Resampling.bilinear)
            self.dtm = AlignedReader(inputs["dtm"], grid, Resampling.bilinear)
        elif "dsm" in inputs:
            self.mode = "dsm-estimated-ground"
            self.dsm = AlignedReader(inputs["dsm"], grid, Resampling.bilinear)
            self._prepare_ground(coarse_m)
            if warn:
                warn("no DTM selected: ground estimated from the DSM by morphological opening "
                     f"({ground_window_m:g} m window); heights on steep terrain are approximate")

    @property
    def available(self) -> bool:
        return self.mode is not None

    def _prepare_ground(self, coarse_m: float):
        step = max(1, int(round(coarse_m / self.grid.resolution_m)))
        self._step = step
        h = max(1, -(-self.grid.height // step))
        w = max(1, -(-self.grid.width // step))
        arr = self.dsm.src.read(1, out_shape=(h, w), resampling=Resampling.average, masked=True)
        coarse = np.ma.filled(arr.astype("float32"), np.nan)
        coarse[coarse <= -9998] = np.nan
        window_px = max(1, int(round(self.ground_window_m / (self.grid.resolution_m * step))))
        self._ground = estimate_ground(coarse, window_px)

    def ndsm(self, window: Window) -> np.ndarray:
        """(h, w) float32 height above ground, NaN where unknown."""
        if self.mode is None:
            raise RuntimeError("no height inputs")
        dsm = self.dsm.read_elevation(window)
        if self.mode == "dsm-dtm":
            dtm = self.dtm.read_elevation(window)
            return dsm - dtm
        r0, c0 = int(window.row_off), int(window.col_off)
        rows = (np.arange(r0, r0 + dsm.shape[0]) // self._step).clip(0, self._ground.shape[0] - 1)
        cols = (np.arange(c0, c0 + dsm.shape[1]) // self._step).clip(0, self._ground.shape[1] - 1)
        ground = self._ground[np.ix_(rows, cols)]
        return dsm - ground

    def close(self):
        for r in (self.dsm, self.dtm):
            if r is not None:
                r.close()
