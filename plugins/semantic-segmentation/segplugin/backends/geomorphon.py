"""Geomorphon landform classification (Jasiewicz & Stepinski, 2013).

Jasiewicz, J. & Stepinski, T.F. (2013) "Geomorphons — a pattern recognition
approach to classification and mapping of landforms", Geomorphology 182,
147–156. For every cell the line of sight is followed in eight directions up
to a lookup distance L; in each direction the largest elevation angle to a
higher cell and the largest depression angle to a lower cell give a ternary
value (+1 higher, −1 lower, 0 level within the flatness threshold t). The
counts of + and − map to one of ten landforms through the published lookup
table — the same table GRASS GIS ``r.geomorphon`` and WhiteboxTools use.

The algorithm is deterministic and needs no training data, which makes it the
appropriate "model" when only a DTM (terrain) or DSM (surface) is available.
The lookup distance and flatness threshold are the two parameters of the
method; both are exposed in ground units so results do not depend on GSD.
"""

import math

import numpy as np

from ..errors import ParameterError
from .base import Backend, TileData

# Rows: number of "lower" (−) directions, columns: number of "higher" (+).
FL, PK, RI, SH, SP, SL, HL, FS, VL, PT = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
_FORMS = np.array([
    [FL, FL, FL, FS, FS, VL, VL, VL, PT],
    [FL, FL, FS, FS, FS, VL, VL, VL, FL],
    [FL, SH, SL, SL, HL, HL, VL, FL, FL],
    [SH, SH, SL, SL, SL, HL, FL, FL, FL],
    [SH, SH, SP, SL, SL, FL, FL, FL, FL],
    [RI, RI, SP, SP, FL, FL, FL, FL, FL],
    [RI, RI, RI, FL, FL, FL, FL, FL, FL],
    [RI, RI, FL, FL, FL, FL, FL, FL, FL],
    [PK, FL, FL, FL, FL, FL, FL, FL, FL],
], dtype="uint8")

_DIRECTIONS = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _shifted(z: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """``out[r, c] = z[r + dy, c + dx]`` with NaN beyond the array."""
    h, w = z.shape
    out = np.full_like(z, np.nan)
    ys = slice(max(0, dy), min(h, h + dy))
    xs = slice(max(0, dx), min(w, w + dx))
    yd = slice(max(0, -dy), min(h, h - dy))
    xd = slice(max(0, -dx), min(w, w - dx))
    out[yd, xd] = z[ys, xs]
    return out


def geomorphons(z: np.ndarray, pixel_size_m: float, search_m: float, skip_m: float,
                flatness_deg: float) -> np.ndarray:
    """Landform ids (1–10) per cell; 0 where the cell itself is nodata."""
    lookup = max(1, int(round(search_m / pixel_size_m)))
    skip = max(0, int(round(skip_m / pixel_size_m)))
    if skip >= lookup:
        raise ParameterError("geomorphon skip distance must be smaller than the search distance")
    t = math.radians(flatness_deg)

    plus = np.zeros(z.shape, dtype="uint8")
    minus = np.zeros(z.shape, dtype="uint8")
    for dy, dx in _DIRECTIONS:
        unit = math.sqrt(2.0) if dy and dx else 1.0
        hi = np.full(z.shape, -np.inf, dtype="float32")
        lo = np.full(z.shape, np.inf, dtype="float32")
        for k in range(skip + 1, lookup + 1):
            neighbour = _shifted(z, k * dy, k * dx)
            angle = np.arctan((neighbour - z) / (k * unit * pixel_size_m))
            np.fmax(hi, angle, out=hi)
            np.fmin(lo, angle, out=lo)
        seen = np.isfinite(hi) & np.isfinite(lo)
        with np.errstate(invalid="ignore"):
            delta = np.where(seen, hi + lo, 0.0)  # nadir angle − zenith angle
        plus += (delta > t).astype("uint8")
        minus += (delta < -t).astype("uint8")

    forms = _FORMS[minus, plus]
    forms[np.isnan(z)] = 0
    return forms


class GeomorphonBackend(Backend):
    needs = frozenset({"elevation"})
    default_tile = 512

    def __init__(self, card, params, progress):
        super().__init__(card, params, progress)
        self.search_m = float(params.geomorphon_search_m)
        self.skip_m = float(params.geomorphon_skip_m)
        self.flatness_deg = float(params.geomorphon_flatness_deg)
        if self.search_m <= 0:
            raise ParameterError("geomorphon_search_m must be positive")
        if not (0 < self.flatness_deg < 90):
            raise ParameterError("geomorphon_flatness_deg must be between 0 and 90 degrees")
        self._pixel_size_m = None

    def configure(self, pixel_size_m: float):
        self._pixel_size_m = pixel_size_m
        self.context = max(1, int(round(self.search_m / pixel_size_m)))
        if self.context > 400:
            raise ParameterError(
                f"geomorphon search distance {self.search_m:g} m is {self.context} pixels at "
                f"{pixel_size_m:.3g} m; set a coarser resolution_m (or a smaller search) to keep it under 400"
            )

    def predict(self, tile: TileData) -> tuple[np.ndarray, np.ndarray]:
        z = tile.elevation
        forms = geomorphons(z, tile.pixel_size_m, self.search_m, self.skip_m, self.flatness_deg)
        valid = ~np.isnan(z)
        return self.one_hot(forms, valid), valid

    def describe(self) -> dict:
        return {
            "backend": "geomorphon",
            "search_m": self.search_m,
            "skip_m": self.skip_m,
            "flatness_deg": self.flatness_deg,
            "lookup_pixels": self.context,
        }
