"""Elevation sampling: heights of a DSM / DTM at a list of points.

Used by flight planning to turn a task's own surface or terrain model into
a terrain source for a new mission over the same site.

A mission planner asks for a coarse grid (tens of metres between points)
over a raster that may have 5 cm pixels. Reading the pixel under each point
would be slow (a block read per point, a range request each on S3) and
would answer the wrong question. What the caller needs is the height
*representative of the ground around each point*:

- for a surface model (``statistic="max"``) the highest thing nearby,
  because a route is cleared against tree tops, roofs and masts, not their
  average. A lone 4 m wide mast must not disappear between two grid points;
- for a terrain model (``statistic="mean"``) the mean ground height.

So the raster is resampled once, with that statistic, to cells half the
requested spacing wide, and each point takes the statistic of the 3 x 3
cells around it: a neighbourhood 1.5 x the spacing across, which overlaps
its neighbours, so nothing between grid points is left out.

The resampling reads real pixels (not a pre-averaged decimation), from the
finest overview level that keeps the read within ``MAX_SOURCE_PIXELS``.
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject
from rasterio.warp import transform as warp_transform

from app.utils import objectstore
from app.utils.volume import _to_meters_factor

STATISTICS = {"max": Resampling.max, "mean": Resampling.average, "min": Resampling.min}
MAX_POINTS = 20000
# Source pixels resampled for one request; above this a coarser overview is used.
MAX_SOURCE_PIXELS = 30_000_000
# Cells produced for one request, however small the spacing or large the area.
MAX_CELLS = 4_000_000


class ElevationSampleError(ValueError):
    """The request cannot be answered (bad input, raster without georeferencing)."""


def _ground_metres_per_unit(ds, lat: float) -> tuple[float, float]:
    """Metres per CRS unit along x and y (a geographic CRS is in degrees)."""
    if ds.crs is not None and ds.crs.is_geographic:
        return 111320.0 * max(0.01, math.cos(math.radians(lat))), 110574.0
    f = _to_meters_factor(ds)
    return f, f


def _validate(points, spacing_m, statistic):
    if statistic not in STATISTICS:
        raise ElevationSampleError(f"statistic must be one of: {', '.join(STATISTICS)}")
    if not isinstance(points, list) or not points:
        raise ElevationSampleError("points must be a non-empty list of [lng, lat] pairs")
    if len(points) > MAX_POINTS:
        raise ElevationSampleError(f"at most {MAX_POINTS} points per request")
    try:
        lngs = [float(p[0]) for p in points]
        lats = [float(p[1]) for p in points]
    except (TypeError, ValueError, IndexError):
        raise ElevationSampleError("points must be [lng, lat] pairs")
    if any(not (-180 <= x <= 180) for x in lngs) or any(not (-90 <= y <= 90) for y in lats):
        raise ElevationSampleError("points are outside the valid longitude / latitude range")
    try:
        spacing_m = float(spacing_m)
    except (TypeError, ValueError):
        raise ElevationSampleError("spacing_m must be a number")
    if not (spacing_m > 0):
        raise ElevationSampleError("spacing_m must be positive")
    return lngs, lats, spacing_m


def sample_points(path: str, points: list, spacing_m: float = 30.0, statistic: str = "max") -> dict:
    """Heights at ``points`` (``[lng, lat]`` pairs, EPSG:4326), in metres.

    Returns ``{"values": [float | None, ...], "resolution_m", "cell_m",
    "statistic", "covered"}``. ``None`` marks a point outside the raster or
    with only nodata around it. ``resolution_m`` is the raster's native pixel
    size, ``cell_m`` the width of the neighbourhood each value summarises.
    """
    lngs, lats, spacing_m = _validate(points, spacing_m, statistic)
    gdal_path = objectstore.resolve_read_path(path)
    mid_lat = sum(lats) / len(lats)

    with objectstore.open_env():
        with rasterio.open(gdal_path) as ds:
            if ds.crs is None:
                raise ElevationSampleError("the raster has no coordinate reference system")
            crs = ds.crs
            full_transform = ds.transform
            width, height = ds.width, ds.height
            overviews = ds.overviews(1)
            mx, my = _ground_metres_per_unit(ds, mid_lat)
            # Heights of a foot-based raster are in feet too; answer in metres.
            z_factor = 1.0 if crs.is_geographic else _to_meters_factor(ds)

        xs, ys = warp_transform("EPSG:4326", crs, lngs, lats)
        xs = np.asarray(xs, dtype="float64")
        ys = np.asarray(ys, dtype="float64")
        px_x, px_y = abs(full_transform.a), abs(full_transform.e)
        native = max(px_x * mx, px_y * my)
        left, top = full_transform.c, full_transform.f
        right, bottom = left + px_x * width, top - px_y * height

        inside = (xs >= left) & (xs < right) & (ys <= top) & (ys > bottom)
        values: list = [None] * len(points)
        spacing_m = max(spacing_m, native)
        out = {"values": values, "resolution_m": round(native, 4), "cell_m": round(1.5 * spacing_m, 3),
               "statistic": statistic, "covered": 0}
        if not inside.any():
            return out

        # Extent of the points on the raster, padded so every point has its
        # full neighbourhood, clipped to the raster.
        pad_x, pad_y = spacing_m / mx, spacing_m / my
        x0 = max(left, float(xs[inside].min()) - pad_x)
        x1 = min(right, float(xs[inside].max()) + pad_x)
        y1 = min(top, float(ys[inside].max()) + pad_y)
        y0 = max(bottom, float(ys[inside].min()) - pad_y)

        # Cells half the spacing wide; coarser only if the area is enormous.
        cell_x, cell_y = spacing_m / 2.0 / mx, spacing_m / 2.0 / my
        cells = ((x1 - x0) / cell_x) * ((y1 - y0) / cell_y)
        if cells > MAX_CELLS:
            grow = math.sqrt(cells / MAX_CELLS)
            cell_x, cell_y = cell_x * grow, cell_y * grow
            out["cell_m"] = round(1.5 * spacing_m * grow, 3)
        n_cols = max(1, int(math.ceil((x1 - x0) / cell_x)))
        n_rows = max(1, int(math.ceil((y1 - y0) / cell_y)))
        dst_transform = Affine(cell_x, 0.0, x0, 0.0, -cell_y, y1)

        # Finest overview that keeps the source read within budget.
        source_px = ((x1 - x0) / px_x) * ((y1 - y0) / px_y)
        level = None
        for i, factor in enumerate(overviews):
            if source_px <= MAX_SOURCE_PIXELS:
                break
            level = i
            source_px = ((x1 - x0) / (px_x * factor)) * ((y1 - y0) / (px_y * factor))

        grid = np.full((n_rows, n_cols), np.nan, dtype="float64")
        open_kwargs = {} if level is None else {"overview_level": level}
        with rasterio.open(gdal_path, **open_kwargs) as src:
            reproject(
                source=rasterio.band(src, 1), destination=grid,
                src_transform=src.transform, src_crs=crs, src_nodata=src.nodata,
                dst_transform=dst_transform, dst_crs=crs, dst_nodata=np.nan,
                resampling=STATISTICS[statistic],
            )
        grid[~np.isfinite(grid)] = np.nan

    reducer = {"max": np.nanmax, "mean": np.nanmean, "min": np.nanmin}[statistic]
    covered = 0
    with np.errstate(all="ignore"):
        for i in np.nonzero(inside)[0]:
            c = int((xs[i] - x0) / cell_x)
            r = int((y1 - ys[i]) / cell_y)
            block = grid[max(0, r - 1):min(n_rows, r + 2), max(0, c - 1):min(n_cols, c + 2)]
            if block.size == 0 or np.isnan(block).all():
                continue
            # The cell under the point itself must have data: a point on
            # nodata is unknown, whatever its neighbours say.
            if 0 <= r < n_rows and 0 <= c < n_cols and np.isnan(grid[r, c]):
                continue
            values[int(i)] = round(float(reducer(block)) * z_factor, 3)
            covered += 1
    out["covered"] = covered
    return out
