"""Rule-based classes from height above ground, optionally split by greenness.

Thresholding the normalized DSM into ground / low / high objects is the
classical first step of building and vegetation extraction from photogrammetric
surface models (Weidner & Förstner 1995; Rottensteiner & Briese 2002) and the
baseline the ISPRS 2D semantic labelling benchmark compares learning methods
against (Rottensteiner et al. 2014). Where an orthophoto is available, the
Excess Green index ExG = 2g − r − b on chromatic coordinates (Woebbecke et al.
1995) separates vegetation from built structures — the standard RGB-only
stand-in for NDVI when no near-infrared band exists.
"""

import numpy as np

from ..errors import ParameterError
from .base import Backend, TileData

GROUND, LOW_OBJECT, HIGH_OBJECT, LOW_VEG, HIGH_VEG, LOW_STRUCT, HIGH_STRUCT = range(7)


def excess_green(rgb: np.ndarray) -> np.ndarray:
    """ExG on chromatic coordinates; ~0 for grey/soil, positive for vegetation."""
    total = rgb.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        r, g, b = (np.where(total > 0, rgb[i] / total, 1.0 / 3.0) for i in range(3))
    return (2 * g - r - b).astype("float32")


class HeightClassesBackend(Backend):
    needs = frozenset({"ndsm", "rgb"})
    default_tile = 1024

    def __init__(self, card, params, progress):
        super().__init__(card, params, progress)
        self.low_m = float(params.low_threshold_m)
        self.tall_m = float(params.tall_threshold_m)
        self.exg_threshold = float(params.vegetation_index_threshold)
        if not (0 <= self.low_m < self.tall_m):
            raise ParameterError("low_threshold_m must be >= 0 and smaller than tall_threshold_m")
        self.uses_rgb = None

    def predict(self, tile: TileData) -> tuple[np.ndarray, np.ndarray]:
        h = tile.ndsm
        valid = ~np.isnan(h)
        labels = np.full(h.shape, GROUND, dtype="uint8")
        low = valid & (h >= self.low_m) & (h < self.tall_m)
        high = valid & (h >= self.tall_m)
        use_rgb = tile.rgb is not None
        if self.uses_rgb is None:
            self.uses_rgb = use_rgb
        if use_rgb:
            veg = excess_green(tile.rgb) > self.exg_threshold
            rgb_valid = tile.rgb_valid if tile.rgb_valid is not None else np.ones(h.shape, bool)
            # Where the orthophoto has no data fall back to the height-only classes.
            labels[low & rgb_valid & veg] = LOW_VEG
            labels[low & rgb_valid & ~veg] = LOW_STRUCT
            labels[high & rgb_valid & veg] = HIGH_VEG
            labels[high & rgb_valid & ~veg] = HIGH_STRUCT
            labels[low & ~rgb_valid] = LOW_OBJECT
            labels[high & ~rgb_valid] = HIGH_OBJECT
        else:
            labels[low] = LOW_OBJECT
            labels[high] = HIGH_OBJECT
        return self.one_hot(labels, valid), valid

    def describe(self) -> dict:
        return {
            "backend": "height",
            "low_threshold_m": self.low_m,
            "tall_threshold_m": self.tall_m,
            "vegetation_index": "ExG" if self.uses_rgb else None,
            "vegetation_index_threshold": self.exg_threshold if self.uses_rgb else None,
        }
