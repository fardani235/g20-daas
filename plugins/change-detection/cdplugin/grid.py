"""The surveys' rasters and the common grid they are compared on.

Two processing runs never share a grid: resolution, extent and sometimes the
coordinate system differ. Everything is therefore resampled onto one *working
grid* before any pixel is compared:

* **CRS** — the first projected CRS among the inputs (the survey the run
  belongs to comes first, so the result lines up with that survey's layers);
  a local UTM zone when every input is geographic, because areas and volumes
  need metres.
* **Extent** — the intersection of the two surveys' footprints. Only ground
  both surveys cover can be compared.
* **Resolution** — the coarser of the two surveys' own resolutions (comparing
  finer than that would invent detail), coarsened further when the grid would
  not fit the sandbox's memory. Downsampling uses *average* resampling, which
  conserves volume: the mean height of a block times its area is the same
  whether it is computed from 5 cm or from 25 cm cells.

Reads go through a rasterio ``WarpedVRT`` fed from the overview level closest
to the working resolution (``WarpedVRT`` otherwise always warps from level 0),
strip by strip, mirroring the other plugins' ``grid.py``.
"""

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from .errors import InputError

# Pixel budget of the working grid. The heaviest stage is the photo comparison:
# two RGB images plus about ten float32 work planes of this size next to the
# difference, labels and masks — roughly 90 bytes per cell, ~750 MB at the cap
# (measured on a 43 ha survey), which leaves the sandbox's 2 GB address space
# room for GDAL, PROJ and the libraries themselves.
DEFAULT_MAX_PIXELS = 8_000_000
MIN_GRID_SIDE = 16
STRIP_ROWS = 1024

# Elevations outside this range are treated as "no data" even when the file
# declares none (ODM writes -9999; nothing on Earth is outside these bounds).
ELEVATION_RANGE = (-1000.0, 9000.0)
FALLBACK_NODATA = -9999.0


@dataclass
class Grid:
    crs: CRS
    transform: rasterio.Affine
    width: int
    height: int
    meters_per_unit: float  # 1.0 for projected metre CRSs
    native_resolution_m: float = 0.0  # the coarser survey's own resolution
    coarsened: bool = False           # resolution raised to fit the pixel budget

    @property
    def resolution(self) -> float:
        """Pixel size in CRS units (square pixels)."""
        return abs(self.transform.a)

    @property
    def resolution_m(self) -> float:
        return self.resolution * self.meters_per_unit

    @property
    def cell_area_m2(self) -> float:
        return self.resolution_m ** 2

    @property
    def bounds(self):
        return rasterio.transform.array_bounds(self.height, self.width, self.transform)

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def meters_to_cells(self, meters: float) -> int:
        return max(0, int(math.ceil(meters / self.resolution_m - 1e-9)))

    def summary(self) -> dict:
        epsg = self.crs.to_epsg() if self.crs else None
        return {
            "epsg": epsg,
            "crs": self.crs.to_string() if self.crs else None,
            "resolution_m": round(self.resolution_m, 4),
            "native_resolution_m": round(self.native_resolution_m, 4),
            "coarsened_to_fit_memory": self.coarsened,
            "width": self.width,
            "height": self.height,
            "pixels": self.pixels,
            "bounds": [round(v, 3) for v in self.bounds],
        }


class Raster:
    """One input raster, opened and sanity-checked. ``what`` names it in messages."""

    def __init__(self, path: str, what: str, min_bands: int = 1):
        self.path = path
        self.what = what
        try:
            self.ds = rasterio.open(path)
        except Exception as e:  # rasterio raises a mix of exception types
            raise InputError(f"The {what} is not a readable raster: {e}") from e
        if self.ds.crs is None:
            self.ds.close()
            raise InputError(f"The {what} has no coordinate reference system")
        if self.ds.transform.is_identity or self.ds.width == 0 or self.ds.height == 0:
            self.ds.close()
            raise InputError(f"The {what} has no georeferencing (identity transform)")
        if self.ds.count < min_bands:
            count = self.ds.count
            self.ds.close()
            raise InputError(f"The {what} must have at least {min_bands} bands; it has {count}")

    @property
    def crs(self) -> CRS:
        return self.ds.crs

    def meters_per_unit(self) -> float:
        return crs_meters_per_unit(self.ds.crs, self.ds.bounds)

    def resolution_m(self) -> float:
        return max(abs(self.ds.transform.a), abs(self.ds.transform.e)) * self.meters_per_unit()

    def bounds_in(self, crs: CRS) -> tuple[float, float, float, float]:
        b = self.ds.bounds
        if self.ds.crs == crs:
            return (b.left, b.bottom, b.right, b.top)
        return transform_bounds(self.ds.crs, crs, b.left, b.bottom, b.right, b.top, densify_pts=21)

    def describe(self) -> str:
        return (f"{self.ds.width}x{self.ds.height} px, {self.ds.count} band(s), {self.ds.dtypes[0]}, "
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


def working_crs(rasters: list[Raster]) -> CRS:
    """The first projected CRS among ``rasters``; a local UTM zone when all are geographic."""
    for r in rasters:
        if r.crs.is_projected:
            return r.crs
    first = rasters[0]
    b = first.ds.bounds
    west, south, east, north = transform_bounds(first.crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
    lon, lat = (west + east) / 2.0, (south + north) / 2.0
    zone = min(60, max(1, int((lon + 180.0) // 6.0) + 1))
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def build_grid(primary: Raster, reference: Raster, crs: CRS, resolution_m: float = 0.0, *,
               max_pixels: int = DEFAULT_MAX_PIXELS, warn=None) -> Grid:
    """The working grid: ``crs``, the overlap of the two rasters, the coarser resolution.

    Raises ``InputError`` when the surveys do not overlap. A grid over the
    pixel budget is coarsened (and flagged) rather than refused: averaging to
    a coarser cell does not bias areas or volumes.
    """
    pl, pb, pr, pt = primary.bounds_in(crs)
    rl, rb, rr, rt = reference.bounds_in(crs)
    left, bottom, right, top = max(pl, rl), max(pb, rb), min(pr, rr), min(pt, rt)
    if right <= left or top <= bottom:
        raise InputError(
            "The two surveys do not overlap, so there is nothing to compare. "
            "Pick two surveys of the same area."
        )

    probe = type("B", (), {"top": top, "bottom": bottom})
    mpu = crs_meters_per_unit(crs, probe)
    native_m = max(primary.resolution_m(), reference.resolution_m())
    res_m = float(resolution_m or 0.0)
    if res_m <= 0:
        res_m = native_m
    elif res_m < native_m:
        if warn:
            warn(f"requested resolution {res_m:.3g} m is finer than the coarser survey's "
                 f"({native_m:.3g} m); using {native_m:.3g} m")
        res_m = native_m

    coarsened = False
    width, height = _grid_size(left, bottom, right, top, res_m / mpu)
    if width * height > max_pixels:
        fitted = fit_resolution(left, bottom, right, top, mpu, res_m, max_pixels)
        if warn:
            warn(f"the overlap at {res_m:.3g} m would be {width}x{height} cells, above the "
                 f"{max_pixels / 1e6:.3g} megapixel budget; comparing at {fitted:.2f} m instead "
                 f"(averaged cells, volumes are unaffected)")
        res_m = fitted
        coarsened = True

    res = res_m / mpu
    # Snap the origin to whole cells so repeated runs produce the same grid.
    left = math.floor(left / res) * res
    top = math.ceil(top / res) * res
    width, height = _grid_size(left, bottom, right, top, res)
    if width < MIN_GRID_SIDE or height < MIN_GRID_SIDE:
        raise InputError(
            f"The two surveys overlap by only {width}x{height} cells at {res_m:.3g} m; "
            "that is too little to compare."
        )
    return Grid(crs=crs, transform=from_origin(left, top, res, res), width=width, height=height,
                meters_per_unit=mpu, native_resolution_m=native_m, coarsened=coarsened)


def _grid_size(left, bottom, right, top, res) -> tuple[int, int]:
    return (max(1, int(math.ceil((right - left) / res - 1e-9))),
            max(1, int(math.ceil((top - bottom) / res - 1e-9))))


def fit_resolution(left, bottom, right, top, mpu, res_m, max_pixels) -> float:
    """The finest round resolution (0.01 m steps) whose grid fits the budget."""
    w, h = _grid_size(left, bottom, right, top, res_m / mpu)
    fitted = math.ceil(res_m * math.sqrt(w * h / max_pixels) * 100) / 100
    for _ in range(1000):
        # One extra cell per axis covers the origin snap in build_grid.
        w, h = _grid_size(left, bottom, right, top, fitted / mpu)
        if (w + 1) * (h + 1) <= max_pixels:
            return fitted
        fitted = round(fitted + 0.01, 2)
    return fitted


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


class _Warped:
    """``raster`` warped onto ``grid`` (optionally shifted by whole or part cells).

    ``shift`` is ``(dx, dy)`` in grid cells: cell ``(row, col)`` of the result
    shows what the raster holds at ``(row + dy, col + dx)``. It is applied in
    the warp itself, so a horizontally corrected survey is resampled once.
    """

    def __init__(self, raster: Raster, grid: Grid, shift=(0.0, 0.0), nodata=None):
        src = raster.ds
        src_res_m = raster.resolution_m()
        level = overview_level_for(src, grid.resolution_m, src_res_m)
        self._ovr = None
        warp_src = src
        if level is not None:
            self._ovr = rasterio.open(raster.path, overview_level=level)
            warp_src = self._ovr
            src_res_m *= src.overviews(1)[level]
        self.overview_level = level
        # Average when shrinking (unbiased block means), bilinear otherwise.
        self.resampling = Resampling.average if grid.resolution_m > 1.5 * src_res_m else Resampling.bilinear
        t, dx, dy = grid.transform, float(shift[0]), float(shift[1])
        transform = rasterio.Affine(t.a, t.b, t.c + t.a * dx + t.b * dy, t.d, t.e, t.f + t.d * dx + t.e * dy)
        self.vrt = WarpedVRT(
            warp_src, crs=grid.crs, transform=transform, width=grid.width, height=grid.height,
            resampling=self.resampling, src_nodata=nodata, nodata=nodata,
        )
        self.grid = grid

    def strips(self):
        for y0 in range(0, self.grid.height, STRIP_ROWS):
            h = min(STRIP_ROWS, self.grid.height - y0)
            yield y0, h, Window(0, y0, self.grid.width, h)

    def close(self):
        self.vrt.close()
        if self._ovr is not None:
            self._ovr.close()


def read_surface(raster: Raster, grid: Grid, shift=(0.0, 0.0)) -> np.ndarray:
    """Band 1 of an elevation raster on the working grid: float32, NaN where there is no data."""
    nodata = raster.ds.nodata
    if nodata is None:
        # No declared nodata: assume ODM's so the sentinel is not averaged into
        # real heights at the edges; the range check below catches anything else.
        nodata = FALLBACK_NODATA
    out = np.full((grid.height, grid.width), np.nan, dtype="float32")
    warped = _Warped(raster, grid, shift, nodata=nodata)
    try:
        for y0, h, window in warped.strips():
            arr = warped.vrt.read(1, window=window, masked=True)
            data = np.ma.filled(arr.astype("float32"), np.nan)
            data[~np.isfinite(data)] = np.nan
            data[(data < ELEVATION_RANGE[0]) | (data > ELEVATION_RANGE[1])] = np.nan
            out[y0:y0 + h] = data
    finally:
        warped.close()
    return out


def read_rgb(raster: Raster, grid: Grid, shift=(0.0, 0.0)) -> tuple[np.ndarray, np.ndarray]:
    """An orthophoto on the working grid: (3, h, w) float32 in [0, 1] and a validity mask
    (alpha band, nodata and — for 3-band imagery without nodata — fully black pixels)."""
    src = raster.ds
    dtype = np.dtype(src.dtypes[0])
    rgb = np.zeros((3, grid.height, grid.width), dtype="float32")
    valid = np.zeros((grid.height, grid.width), dtype=bool)
    warped = _Warped(raster, grid, shift, nodata=src.nodata)
    try:
        for y0, h, window in warped.strips():
            arr = warped.vrt.read([1, 2, 3], window=window, masked=True)
            ok = ~np.ma.getmaskarray(arr).any(axis=0)
            data = np.ma.filled(arr, 0).astype("float32")
            if src.count >= 4:
                ok &= warped.vrt.read(4, window=window) > 0
            if np.issubdtype(dtype, np.integer):
                data /= float(np.iinfo(dtype).max) if dtype != np.uint8 else 255.0
            elif data.size and data.max() > 1.5:
                data /= 255.0
            np.clip(data, 0.0, 1.0, out=data)
            if src.count < 4 and src.nodata is None:
                ok &= data.sum(axis=0) > 0
            rgb[:, y0:y0 + h] = data
            valid[y0:y0 + h] = ok
    finally:
        warped.close()
    return rgb, valid
