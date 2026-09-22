"""Input rasters and the common processing grid.

Task rasters need not share a grid: ODM's orthophoto, DSM and DTM usually have
the same CRS but can differ in resolution and extent, and a user may run the
plugin on rasters from other sources. Everything is therefore resampled on the
fly onto one *processing grid* (CRS, transform, size) through rasterio
``WarpedVRT``s, so tiles read from any input line up pixel for pixel.

The grid takes the CRS of the finest input, covers the intersection of the
required inputs (optional inputs fall back to nodata where they have no data)
and uses the requested resolution — or the model's recommended one, coarsened
further if the pixel budget would otherwise be exceeded.
"""

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from .errors import InputError

DATASET_LABELS = {"orthophoto": "Orthophoto", "dsm": "DSM", "dtm": "DTM"}

# Pixel budget for one run: 60 megapixels keeps the label raster, the rolling
# blend buffers and the elevation reads comfortably inside the sandbox's 2 GB.
DEFAULT_MAX_PIXELS = 60_000_000


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

    def profile(self, **overrides) -> dict:
        p = {
            "driver": "GTiff", "crs": self.crs, "transform": self.transform,
            "width": self.width, "height": self.height, "count": 1,
            "tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "deflate",
        }
        p.update(overrides)
        return p

    def summary(self) -> dict:
        epsg = self.crs.to_epsg() if self.crs else None
        return {
            "epsg": epsg,
            "crs": self.crs.to_string() if self.crs else None,
            "resolution_m": round(self.resolution_m, 4),
            "width": self.width,
            "height": self.height,
            "bounds": [round(v, 3) for v in self.bounds],
        }


class InputRaster:
    """One selected task dataset, opened and sanity-checked."""

    def __init__(self, dataset: str, path: str):
        self.dataset = dataset
        self.path = path
        try:
            self.ds = rasterio.open(path)
        except Exception as e:  # rasterio raises a mix of exception types
            raise InputError(f"{DATASET_LABELS.get(dataset, dataset)} is not a readable raster: {e}") from e
        label = DATASET_LABELS.get(dataset, dataset)
        if self.ds.crs is None:
            raise InputError(f"{label} has no coordinate reference system")
        if self.ds.transform.is_identity or self.ds.width == 0 or self.ds.height == 0:
            raise InputError(f"{label} has no georeferencing (identity transform)")
        if dataset == "orthophoto" and self.ds.count < 3:
            raise InputError(f"{label} must have at least 3 bands (RGB); it has {self.ds.count}")
        if dataset in ("dsm", "dtm") and self.ds.count != 1:
            raise InputError(f"{label} must be a single-band elevation raster; it has {self.ds.count} bands")

    @property
    def label(self) -> str:
        return DATASET_LABELS.get(self.dataset, self.dataset)

    def meters_per_unit(self) -> float:
        return crs_meters_per_unit(self.ds.crs, self.ds.bounds)

    def resolution_m(self) -> float:
        return max(abs(self.ds.transform.a), abs(self.ds.transform.e)) * self.meters_per_unit()

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
    # Mean of the metres-per-degree of latitude and (cos-scaled) longitude.
    return (111_320.0 * math.cos(lat) + 110_574.0) / 2.0


def open_inputs(paths: dict[str, str]) -> dict[str, InputRaster]:
    """Open every selected input; unknown dataset names are ignored."""
    inputs = {}
    for dataset in ("orthophoto", "dsm", "dtm"):
        path = paths.get(dataset)
        if path:
            inputs[dataset] = InputRaster(dataset, path)
    if not inputs:
        raise InputError("No input datasets were selected (need an orthophoto, DSM or DTM)")
    return inputs


def _intersection(bounds_list):
    left = max(b[0] for b in bounds_list)
    bottom = max(b[1] for b in bounds_list)
    right = min(b[2] for b in bounds_list)
    top = min(b[3] for b in bounds_list)
    if right <= left or top <= bottom:
        return None
    return left, bottom, right, top


def build_grid(
    inputs: dict[str, InputRaster],
    primary: list[str],
    resolution_m: float | None,
    *,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    warn=None,
) -> Grid:
    """The processing grid for ``inputs``.

    ``primary`` names the datasets the model actually reads for its core
    prediction (the extent is their intersection); the finest of them sets the
    CRS. ``resolution_m`` of ``None`` keeps the finest native resolution.
    """
    primary_rasters = [inputs[d] for d in primary if d in inputs]
    if not primary_rasters:
        primary_rasters = list(inputs.values())
    ref = min(primary_rasters, key=lambda r: r.resolution_m())
    crs = ref.ds.crs
    mpu = ref.meters_per_unit()

    bounds_list = []
    for r in primary_rasters:
        if r.ds.crs == crs:
            bounds_list.append(tuple(r.ds.bounds))
        else:
            bounds_list.append(transform_bounds(r.ds.crs, crs, *r.ds.bounds, densify_pts=21))
    extent = _intersection(bounds_list)
    if extent is None:
        names = ", ".join(r.label for r in primary_rasters)
        raise InputError(f"Selected inputs do not overlap ({names}); check their extents and CRSs")

    native_m = ref.resolution_m()
    res_m = float(resolution_m) if resolution_m and resolution_m > 0 else native_m
    if res_m < native_m:
        if warn:
            warn(f"requested resolution {res_m:.3f} m is finer than the finest input "
                 f"({native_m:.3f} m); using the native resolution")
        res_m = native_m
    res = res_m / mpu

    left, bottom, right, top = extent
    width = max(1, int(math.ceil((right - left) / res)))
    height = max(1, int(math.ceil((top - bottom) / res)))
    if width * height > max_pixels:
        factor = math.sqrt(width * height / max_pixels)
        res *= factor
        width = max(1, int(math.ceil((right - left) / res)))
        height = max(1, int(math.ceil((top - bottom) / res)))
        if warn:
            warn(f"processing resolution coarsened to {res * mpu:.3f} m to stay within "
                 f"{max_pixels / 1e6:.0f} megapixels")

    transform = from_origin(left, top, res, res)
    return Grid(crs=crs, transform=transform, width=width, height=height, meters_per_unit=mpu)


def overview_level_for(src, target_res_m: float, src_res_m: float) -> int | None:
    """The coarsest overview of ``src`` that is still finer than (or equal to)
    the target resolution, or None to read the full-resolution data.

    Warping straight from a 5 cm orthophoto onto a 30 cm grid reads (and
    resamples) 36× more pixels than needed; reading from a matching overview
    is what makes coarse processing fast regardless of the GDAL version.
    """
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


class AlignedReader:
    """Reads any input raster on the processing grid, window by window.

    Elevation is returned as float32 with NaN for nodata; imagery as float32
    in [0, 1] plus a validity mask (alpha band and/or nodata). Sources on a
    different grid or CRS are warped lazily through a ``WarpedVRT``, reading
    from the overview level closest to the processing resolution.
    """

    def __init__(self, raster: InputRaster, grid: Grid, resampling: Resampling):
        self.raster = raster
        self.grid = grid
        src = raster.ds
        self.nodata = src.nodata
        self._ovr = None
        same_grid = (
            src.crs == grid.crs
            and src.width == grid.width and src.height == grid.height
            and _close_transform(src.transform, grid.transform)
        )
        if same_grid:
            self.vrt = None
            self.src = src
        else:
            level = overview_level_for(src, grid.resolution_m, raster.resolution_m())
            warp_src = src
            if level is not None:
                self._ovr = rasterio.open(raster.path, overview_level=level)
                warp_src = self._ovr
            self.vrt = WarpedVRT(
                warp_src, crs=grid.crs, transform=grid.transform,
                width=grid.width, height=grid.height, resampling=resampling,
                src_nodata=src.nodata,
                nodata=src.nodata if src.nodata is not None else (
                    np.nan if np.issubdtype(np.dtype(src.dtypes[0]), np.floating) else None),
            )
            self.src = self.vrt

    def close(self):
        if self.vrt is not None:
            self.vrt.close()
        if self._ovr is not None:
            self._ovr.close()

    def read_elevation(self, window: Window) -> np.ndarray:
        arr = self.src.read(1, window=window, masked=True).astype("float32")
        data = np.ma.filled(arr, np.nan)
        # Common ODM sentinel values also become nodata even when not declared.
        data[(data <= -9998) | ~np.isfinite(data)] = np.nan
        return data

    def read_rgb(self, window: Window) -> tuple[np.ndarray, np.ndarray]:
        """(3, h, w) float32 in [0, 1] and (h, w) validity mask."""
        count = self.src.count
        bands = [1, 2, 3]
        arr = self.src.read(bands, window=window, masked=True)
        valid = ~np.ma.getmaskarray(arr).any(axis=0)
        data = np.ma.filled(arr, 0).astype("float32")
        if count >= 4:
            alpha = self.src.read(4, window=window)
            valid &= alpha > 0
        dtype = np.dtype(self.raster.ds.dtypes[0])
        if np.issubdtype(dtype, np.integer):
            scale = float(np.iinfo(dtype).max) if dtype != np.uint8 else 255.0
            data /= scale
        else:
            # Float imagery: assume already [0, 1] unless clearly 0-255.
            if data.max() > 1.5:
                data /= 255.0
        np.clip(data, 0.0, 1.0, out=data)
        # Fully black pixels outside the footprint of an orthophoto without
        # alpha are treated as nodata too.
        if count < 4 and self.nodata is None:
            valid &= data.sum(axis=0) > 0
        return data, valid


def _close_transform(a, b, tol=1e-6) -> bool:
    return all(abs(x - y) <= tol * max(1.0, abs(y)) for x, y in zip(a[:6], b[:6]))
