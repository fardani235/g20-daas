"""Elevation grids: planning, reading rasters onto a grid, filling holes.

The terrain workflows build a *heightfield* — one elevation per grid cell —
and triangulate it (``mesh.py``). The grid is planned from the triangle
budget: ``2 * cells`` triangles come out of a full grid, so the cell count is
capped at roughly 0.65 x ``max_triangles`` and the quadric decimation then
spends the budget where the surface actually bends.

Rasters are read through ``WarpedVRT`` so DSM, DTM and orthophoto can be in
different grids (or even CRSs) and still land cell-for-cell on the same
heightfield; reading at the grid's resolution lets GDAL pull from the
matching overview level instead of full-resolution data.
"""

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.fill import fillnodata
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from .crs import require_projected, same_crs
from .errors import InputError

# Never let a heightfield exceed this many cells whatever the parameters
# say: 3M cells is ~6M triangles before decimation, the most the sandbox's
# 2 GB address space handles comfortably.
MAX_CELLS = 3_000_000
MIN_CELLS_PER_SIDE = 2


@dataclass(frozen=True)
class Grid:
    crs: CRS
    minx: float
    maxy: float
    res: float
    width: int
    height: int

    @property
    def transform(self):
        return from_origin(self.minx, self.maxy, self.res, self.res)

    @property
    def maxx(self) -> float:
        return self.minx + self.width * self.res

    @property
    def miny(self) -> float:
        return self.maxy - self.height * self.res

    @property
    def bounds(self):
        return (self.minx, self.miny, self.maxx, self.maxy)

    @property
    def cells(self) -> int:
        return self.width * self.height

    def cell_centers(self):
        """1-D arrays of the X (per column) and Y (per row) coordinates of cell centres."""
        xs = self.minx + (np.arange(self.width, dtype=np.float64) + 0.5) * self.res
        ys = self.maxy - (np.arange(self.height, dtype=np.float64) + 0.5) * self.res
        return xs, ys

    def texture_grid(self, max_side: int, native_res: float | None = None) -> "Grid":
        """A grid over the same bounds for a texture image whose longest side is ``max_side`` px.

        ``native_res`` (m/px of the source image) prevents upsampling beyond
        what the source actually holds. Cells are kept square by using one
        resolution and letting the bounds snap to whole texels on the far edges.
        """
        w, h = self.maxx - self.minx, self.maxy - self.miny
        res = max(w, h) / max_side
        if native_res:
            res = max(res, native_res)
        res = min(res, min(w, h))  # at least one texel each way
        width = max(1, int(math.ceil(w / res)))
        height = max(1, int(math.ceil(h / res)))
        return Grid(self.crs, self.minx, self.maxy, res, width, height)


def raster_info(path: str) -> dict:
    """CRS, bounds, resolution and nodata of a raster (raises InputError if unreadable)."""
    try:
        with rasterio.open(path) as ds:
            return {
                "crs": ds.crs, "bounds": tuple(ds.bounds), "res": float(max(abs(ds.res[0]), abs(ds.res[1]))),
                "width": ds.width, "height": ds.height, "count": ds.count, "nodata": ds.nodata,
                "dtype": ds.dtypes[0],
            }
    except rasterio.errors.RasterioIOError as e:
        raise InputError(f"cannot read raster {path}: {e}") from e


def auto_resolution(bounds, native_res: float, max_triangles: int, coverage: float = 1.0) -> float:
    """Cell size so the *covered* part of the grid yields ~1.5 x ``max_triangles`` triangles.

    ``coverage`` is the share of the bounding box that actually holds data
    (flight areas are rarely rectangular); the result is never finer than
    ``native_res``.
    """
    minx, miny, maxx, maxy = bounds
    area = max(maxx - minx, 1e-9) * max(maxy - miny, 1e-9) * min(max(coverage, 0.1), 1.0)
    # 2 triangles per cell, ~1.3x over budget so the decimation has room to work
    # without the collapse step (the memory peak) growing out of proportion.
    target_cells = max(1000.0, 0.65 * max_triangles)
    res = math.sqrt(area / target_cells)
    return max(res, native_res)


def valid_fraction(path: str, sample: int = 512) -> float:
    """Share of cells with data, estimated from a coarse (overview) read."""
    with rasterio.open(path) as ds:
        h = max(1, min(sample, ds.height))
        w = max(1, min(sample, ds.width))
        data = ds.read(1, out_shape=(h, w), masked=True, resampling=Resampling.nearest)
        valid = ~np.ma.getmaskarray(data)
        if data.dtype.kind == "f":
            valid &= np.isfinite(np.ma.getdata(data))
        return float(valid.mean())


def plan_grid(crs: CRS, bounds, res: float, *, max_cells: int = MAX_CELLS) -> tuple[Grid, bool]:
    """Snap ``bounds`` to whole cells of ``res``; coarsen if the grid would exceed ``max_cells``.

    Returns the grid and whether it had to be coarsened.
    """
    minx, miny, maxx, maxy = bounds
    if not (maxx > minx and maxy > miny):
        raise InputError("input extent is empty")
    coarsened = False
    while True:
        width = max(MIN_CELLS_PER_SIDE, int(math.ceil((maxx - minx) / res)))
        height = max(MIN_CELLS_PER_SIDE, int(math.ceil((maxy - miny) / res)))
        if width * height <= max_cells:
            break
        res *= math.sqrt(width * height / max_cells) * 1.01
        coarsened = True
    return Grid(crs, float(minx), float(maxy), float(res), width, height), coarsened


def bounds_in(crs_from: CRS, bounds, crs_to: CRS):
    if same_crs(crs_from, crs_to):
        return tuple(float(v) for v in bounds)
    return tuple(float(v) for v in transform_bounds(crs_from, crs_to, *bounds, densify_pts=21))


def intersect_bounds(a, b):
    minx, miny = max(a[0], b[0]), max(a[1], b[1])
    maxx, maxy = min(a[2], b[2]), min(a[3], b[3])
    if maxx <= minx or maxy <= miny:
        return None
    return (minx, miny, maxx, maxy)


def overview_level_for(src, target_res: float, tolerance: float = 1.001) -> int | None:
    """Coarsest overview of ``src`` whose resolution is <= ``target_res * tolerance``; None = level 0.

    The warper itself always reads level 0, so warping a 5 cm DSM onto a 1 m
    grid would pull the whole 700 MB raster through memory; opening the
    matching overview first makes the read proportional to the output.
    ``tolerance`` > 1 accepts a slightly coarser overview (fine for textures,
    where a little softness costs nothing but a 4x larger read does).
    """
    src_res = float(max(abs(src.res[0]), abs(src.res[1])))
    if src_res <= 0 or target_res * tolerance < src_res * 2.0:
        return None
    try:
        factors = src.overviews(1)
    except Exception:
        return None
    best = None
    for level, factor in enumerate(factors):
        if factor * src_res <= target_res * tolerance and (best is None or factor > factors[best]):
            best = level
    return best


def read_on_grid(path: str, grid: Grid, *, bands=None, resampling=Resampling.average,
                 dtype=np.float32) -> np.ma.MaskedArray:
    """Read ``bands`` of ``path`` resampled onto ``grid`` as a masked array (mask = nodata)."""
    with rasterio.open(path) as src:
        require_projected(src.crs, path)
        level = overview_level_for(src, grid.res)
        ovr = rasterio.open(path, overview_level=level) if level is not None else None
        try:
            with WarpedVRT(ovr or src, crs=grid.crs, transform=grid.transform, width=grid.width,
                           height=grid.height, resampling=resampling, src_nodata=src.nodata,
                           nodata=src.nodata if src.nodata is not None else None) as vrt:
                indexes = list(bands) if bands else None
                data = vrt.read(indexes=indexes, masked=True, out_dtype=dtype)
                if indexes and len(indexes) == 1:
                    data = data[0]
                elif not indexes and src.count == 1:
                    data = data[0]
                # Alpha band as validity when the raster ships one (ODM orthophotos).
                if not indexes and src.count == 4 and src.colorinterp and src.colorinterp[3].name == "alpha":
                    alpha = data[3]
                    data = data[:3]
                    data.mask = np.broadcast_to(np.ma.getmaskarray(data) | (alpha.filled(0) == 0), data.shape).copy()
        finally:
            if ovr is not None:
                ovr.close()
    if data.dtype.kind == "f":
        finite = np.isfinite(np.ma.getdata(data))
        data.mask = np.ma.getmaskarray(data) | ~finite
    return data


def fill_holes(z: np.ma.MaskedArray, *, donors: list[np.ma.MaskedArray] = (), max_search_px: int = 100,
               max_hole_cells: int | None = None, smoothing_iterations: int = 0) -> tuple[np.ma.MaskedArray, dict]:
    """Fill masked cells: first from ``donors`` (e.g. the DTM under a DSM), then by interpolation.

    Donors fill any hole (they are real measurements). Interpolation is limited
    to gaps within ``max_search_px`` of data and, when ``max_hole_cells`` is
    given, to connected holes of at most that many cells. Returns the filled
    array (still masked where nothing could fill) and a report of how many
    cells each step recovered.
    """
    z = z.copy()
    report = {"holes": int(np.ma.getmaskarray(z).sum()), "from_donors": 0, "interpolated": 0}
    if report["holes"] == 0:
        return z, report

    for donor in donors:
        if donor is None or donor.shape != z.shape:
            continue
        take = np.ma.getmaskarray(z) & ~np.ma.getmaskarray(donor)
        if take.any():
            data = np.ma.getdata(z).copy()
            data[take] = np.ma.getdata(donor)[take]
            z = np.ma.MaskedArray(data, mask=np.ma.getmaskarray(z) & ~take)
            report["from_donors"] += int(take.sum())

    mask = np.ma.getmaskarray(z)
    if mask.any() and max_search_px > 0 and (~mask).any():
        # Only gaps *inside* the surveyed area are interpolated: hole regions
        # bigger than ``max_hole_cells`` (the area outside the flight polygon,
        # water bodies) are left open rather than bridged with invented terrain.
        small = mask & ~_large_hole_regions(mask, max_hole_cells or 0)
        data = np.ma.getdata(z).astype(np.float32).copy()
        data[mask] = np.nan
        filled = fillnodata(data, mask=(~mask).astype(np.uint8), max_search_distance=float(max_search_px),
                            smoothing_iterations=int(smoothing_iterations))
        # Cells farther than the search distance stay NaN (GDAL leaves them untouched).
        recovered = small & np.isfinite(filled)
        still = mask & ~recovered
        report["interpolated"] += int(recovered.sum())
        z = np.ma.MaskedArray(np.where(still, 0, filled).astype(np.float32), mask=still)
    return z, report


def _large_hole_regions(mask: np.ndarray, max_cells: int) -> np.ndarray:
    """True where a masked cell belongs to a connected hole larger than ``max_cells``."""
    if max_cells <= 0:
        return np.zeros_like(mask)
    from rasterio.features import sieve

    holes = mask.astype(np.uint8)
    # Sieving removes hole regions up to max_cells, replacing them with the surrounding value (0).
    sieved = sieve(holes, size=int(max_cells) + 1, connectivity=8)
    return (sieved == 1) & mask


def slope_stats(z: np.ndarray, res: float) -> dict:
    """Quick roughness statistics used to describe the surface in the run metadata."""
    if z.shape[0] < 2 or z.shape[1] < 2:
        return {}
    gy, gx = np.gradient(z, res)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    return {"mean_slope_deg": round(float(np.nanmean(slope)), 2), "max_slope_deg": round(float(np.nanmax(slope)), 2)}
