"""The orthophoto: opening, sanity checks and tile reads.

Only one input matters for detection. It must be georeferenced (boxes are
reported in EPSG:4326) and is read as 8-bit RGB tiles; a fourth (alpha) band
or a nodata value marks the area outside the survey, which is skipped.
"""

import math

import numpy as np
import rasterio
from rasterio.windows import Window

from .errors import InputError


def crs_meters_per_unit(crs, bounds) -> float:
    """Metres per CRS unit; for geographic CRSs an approximation at the centre latitude."""
    if crs.is_projected:
        try:
            return float(crs.linear_units_factor[1])
        except Exception:
            return 1.0
    lat = math.radians((bounds.top + bounds.bottom) / 2.0)
    # Mean of the metres-per-degree of latitude and (cos-scaled) longitude.
    return (111_320.0 * math.cos(lat) + 110_574.0) / 2.0


class Orthophoto:
    """The selected orthophoto, opened and sanity-checked."""

    def __init__(self, path: str):
        self.path = path
        try:
            self.ds = rasterio.open(path)
        except Exception as e:  # rasterio raises a mix of exception types
            raise InputError(f"orthophoto is not a readable raster: {e}") from e
        if self.ds.crs is None:
            self.ds.close()
            raise InputError("orthophoto has no coordinate reference system; detections cannot be georeferenced")
        if self.ds.transform.is_identity or self.ds.width == 0 or self.ds.height == 0:
            self.ds.close()
            raise InputError("orthophoto has no georeferencing (identity transform)")
        if self.ds.count < 1:
            self.ds.close()
            raise InputError("orthophoto has no bands")
        self.meters_per_unit = crs_meters_per_unit(self.ds.crs, self.ds.bounds)

    @property
    def width(self) -> int:
        return self.ds.width

    @property
    def height(self) -> int:
        return self.ds.height

    @property
    def gsd_m(self) -> float:
        """Mean ground sample distance in metres per pixel."""
        gsd = (abs(self.ds.res[0]) + abs(self.ds.res[1])) / 2 or 1.0
        return gsd * self.meters_per_unit

    @property
    def is_8bit(self) -> bool:
        return self.ds.dtypes[0] == "uint8"

    def summary(self) -> dict:
        epsg = self.ds.crs.to_epsg()
        return {
            "width": self.width,
            "height": self.height,
            "bands": self.ds.count,
            "dtype": self.ds.dtypes[0],
            "epsg": int(epsg) if epsg is not None else None,
            "crs": self.ds.crs.to_string(),
            "gsd_m": round(self.gsd_m, 5),
        }

    def read_tile(self, window: Window) -> tuple[np.ndarray, float]:
        """``(h, w, 3)`` uint8 RGB image for ``window`` and the fraction of valid pixels.

        Grayscale rasters are repeated to three channels; non-8-bit data is
        clipped to 0-255 (detectors are trained on 8-bit imagery, so a 16-bit
        orthophoto should be converted before running).
        """
        bands = [1, 2, 3] if self.ds.count >= 3 else [1]
        array = self.ds.read(bands, window=window)
        mask = self.ds.dataset_mask(window=window)
        valid = float((mask > 0).mean()) if mask.size else 0.0
        hwc = np.transpose(array, (1, 2, 0))
        if hwc.shape[2] == 1:
            hwc = np.repeat(hwc, 3, axis=2)
        if hwc.dtype != np.uint8:
            hwc = np.clip(hwc, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(hwc), valid

    def close(self):
        self.ds.close()
