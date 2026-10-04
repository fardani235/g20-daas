"""The orthophoto and the processing grid.

The model is run on a *processing grid* at a fixed ground resolution (the
card's recommended one unless the run sets ``resolution_m``), in the
orthophoto's CRS, covering its extent. Reads are warped onto that grid lazily
through a rasterio ``WarpedVRT`` fed from the overview level closest to the
target resolution, so a 5 cm orthophoto processed at 25 cm reads 1/25 of its
pixels. This mirrors ``plugins/semantic-segmentation/segplugin/grid.py``.

Unlike the segmentation plugin the grid is **not** coarsened silently when it
would exceed the pixel budget: instance separation needs several full-size
planes in memory at once, so the run stops with the resolution to use instead.
"""

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from .errors import InputError

# Pixel budget for one run. Per grid pixel the plugin holds the blended
# building probability (uint8), the signed distance (float32), the thresholded
# mask (bool) and the int32 label plane: ~10 bytes/px, i.e. ~500 MB at the cap,
# which with the ~450 MB ONNX session stays inside the sandbox's 2 GB address
# space with headroom for the per-component watershed crops.
DEFAULT_MAX_PIXELS = 50_000_000


@dataclass
class Grid:
    crs: rasterio.crs.CRS
    transform: rasterio.Affine
    width: int
    height: int
    meters_per_unit: float  # 1.0 for projected metre CRSs

    @property
    def resolution(self) -> float:
        """Pixel size in CRS units (square pixels)."""
        return abs(self.transform.a)

    @property
    def resolution_m(self) -> float:
        return self.resolution * self.meters_per_unit

    @property
    def pixel_area_m2(self) -> float:
        return self.resolution_m ** 2

    @property
    def bounds(self):
        return rasterio.transform.array_bounds(self.height, self.width, self.transform)

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def meters_to_pixels(self, meters: float) -> int:
        return max(0, int(round(meters / self.resolution_m)))

    def summary(self) -> dict:
        epsg = self.crs.to_epsg() if self.crs else None
        return {
            "epsg": epsg,
            "crs": self.crs.to_string() if self.crs else None,
            "resolution_m": round(self.resolution_m, 4),
            "width": self.width,
            "height": self.height,
            "pixels": self.pixels,
            "bounds": [round(v, 3) for v in self.bounds],
        }


class Orthophoto:
    """The selected orthophoto, opened and sanity-checked."""

    def __init__(self, path: str):
        self.path = path
        try:
            self.ds = rasterio.open(path)
        except Exception as e:  # rasterio raises a mix of exception types
            raise InputError(f"Orthophoto is not a readable raster: {e}") from e
        if self.ds.crs is None:
            raise InputError("Orthophoto has no coordinate reference system")
        if self.ds.transform.is_identity or self.ds.width == 0 or self.ds.height == 0:
            raise InputError("Orthophoto has no georeferencing (identity transform)")
        if self.ds.count < 3:
            raise InputError(f"Orthophoto must have at least 3 bands (RGB); it has {self.ds.count}")

    def meters_per_unit(self) -> float:
        return crs_meters_per_unit(self.ds.crs, self.ds.bounds)

    def resolution_m(self) -> float:
        return max(abs(self.ds.transform.a), abs(self.ds.transform.e)) * self.meters_per_unit()

    def describe(self) -> str:
        return (f"{self.ds.width}x{self.ds.height} px, {self.ds.count} bands, {self.ds.dtypes[0]}, "
                f"{self.resolution_m():.3g} m, {self.ds.crs.to_string()}")

    def close(self):
        self.ds.close()


def crs_meters_per_unit(crs, bounds) -> float:
    """Metres per CRS unit; for geographic CRSs an approximation at the centre latitude."""
    if crs.is_projected:
        try:
            return float(crs.linear_units_factor[1])
        except Exception:
            return 1.0
    lat = math.radians((bounds.top + bounds.bottom) / 2.0)
    return (111_320.0 * math.cos(lat) + 110_574.0) / 2.0


def open_orthophoto(paths: dict[str, str]) -> Orthophoto:
    path = (paths or {}).get("orthophoto")
    if not path:
        raise InputError("No orthophoto was selected; this plugin needs the task's orthophoto")
    return Orthophoto(path)


def build_grid(ortho: Orthophoto, resolution_m: float, *, max_pixels: int = DEFAULT_MAX_PIXELS, warn=None) -> Grid:
    """The processing grid: the orthophoto's CRS and extent at ``resolution_m``.

    Raises ``InputError`` (with the resolution to use instead) when the grid
    would exceed ``max_pixels``.
    """
    crs = ortho.ds.crs
    mpu = ortho.meters_per_unit()
    native_m = ortho.resolution_m()
    res_m = float(resolution_m)
    if res_m <= 0:
        raise InputError("resolution_m must be positive")
    if res_m < native_m:
        if warn:
            warn(f"requested resolution {res_m:.3f} m is finer than the orthophoto's "
                 f"({native_m:.3f} m); using the native resolution")
        res_m = native_m
    res = res_m / mpu

    left, bottom, right, top = ortho.ds.bounds
    width, height = _grid_size(left, bottom, right, top, res)
    pixels = width * height
    if pixels > max_pixels:
        suggested = suggest_resolution(left, bottom, right, top, mpu, res_m, pixels, max_pixels)
        raise InputError(
            f"The processing grid at {res_m:.3g} m would be {width}x{height} px ({pixels / 1e6:.3g} megapixels), "
            f"above the {max_pixels / 1e6:.3g} megapixel limit of the plugin sandbox. "
            f"Use a coarser resolution: set resolution_m to {suggested:.2f} or more."
        )
    transform = from_origin(left, top, res, res)
    return Grid(crs=crs, transform=transform, width=width, height=height, meters_per_unit=mpu)


def _grid_size(left, bottom, right, top, res) -> tuple[int, int]:
    return (max(1, int(math.ceil((right - left) / res))), max(1, int(math.ceil((top - bottom) / res))))


def suggest_resolution(left, bottom, right, top, mpu, res_m, pixels, max_pixels) -> float:
    """The coarsest-looking round resolution (0.01 m steps) that fits the budget.

    The sqrt estimate is checked against the real grid size (which rounds up
    per axis) and nudged until it fits, so re-running at the suggestion works.
    """
    suggested = math.ceil(res_m * math.sqrt(pixels / max_pixels) * 100) / 100
    for _ in range(1000):
        w, h = _grid_size(left, bottom, right, top, suggested / mpu)
        if w * h <= max_pixels:
            return suggested
        suggested = round(suggested + 0.01, 2)
    return suggested


def overview_level_for(src, target_res_m: float, src_res_m: float) -> int | None:
    """The coarsest overview of ``src`` still finer than (or equal to) the target
    resolution, or None to read the full-resolution data."""
    if src_res_m <= 0 or target_res_m <= src_res_m * 1.999:
        return None
    try:
        factors = src.overviews(1)
    except Exception:
        return None
    best = None
    for level, factor in enumerate(factors):
        if factor * src_res_m <= target_res_m * 1.001:
            best = level if best is None or factor > factors[best] else best
    return best


class RgbReader:
    """Reads the orthophoto on the processing grid, window by window.

    Returns float32 RGB in [0, 1] plus a validity mask (alpha band, nodata and
    — for 3-band imagery without nodata — fully black pixels).
    """

    def __init__(self, ortho: Orthophoto, grid: Grid):
        self.ortho = ortho
        self.grid = grid
        src = ortho.ds
        self.nodata = src.nodata
        self._ovr = None
        same_grid = (
            src.crs == grid.crs and src.width == grid.width and src.height == grid.height
            and _close_transform(src.transform, grid.transform)
        )
        if same_grid:
            self.vrt = None
            self.src = src
        else:
            level = overview_level_for(src, grid.resolution_m, ortho.resolution_m())
            warp_src = src
            if level is not None:
                self._ovr = rasterio.open(ortho.path, overview_level=level)
                warp_src = self._ovr
            self.vrt = WarpedVRT(
                warp_src, crs=grid.crs, transform=grid.transform,
                width=grid.width, height=grid.height, resampling=Resampling.bilinear,
                src_nodata=src.nodata, nodata=src.nodata,
            )
            self.src = self.vrt
        self.overview_level = level if not same_grid else None

    def close(self):
        if self.vrt is not None:
            self.vrt.close()
        if self._ovr is not None:
            self._ovr.close()

    def read(self, window: Window) -> tuple[np.ndarray, np.ndarray]:
        """(3, h, w) float32 in [0, 1] and (h, w) validity mask."""
        count = self.src.count
        arr = self.src.read([1, 2, 3], window=window, masked=True)
        valid = ~np.ma.getmaskarray(arr).any(axis=0)
        data = np.ma.filled(arr, 0).astype("float32")
        if count >= 4:
            alpha = self.src.read(4, window=window)
            valid &= alpha > 0
        dtype = np.dtype(self.ortho.ds.dtypes[0])
        if np.issubdtype(dtype, np.integer):
            scale = float(np.iinfo(dtype).max) if dtype != np.uint8 else 255.0
            data /= scale
        elif data.size and data.max() > 1.5:
            data /= 255.0
        np.clip(data, 0.0, 1.0, out=data)
        if count < 4 and self.nodata is None:
            valid &= data.sum(axis=0) > 0
        return data, valid


def _close_transform(a, b, tol=1e-6) -> bool:
    return all(abs(x - y) <= tol * max(1.0, abs(y)) for x, y in zip(a[:6], b[:6]))
