"""LAS/LAZ point clouds -> gridded heights and colours.

Reads with ``laspy`` (LAZ via the ``lazrs`` backend, both part of the sandbox
image) in fixed-size chunks so a cloud of any size fits in the sandbox's memory
limit, and bins the points onto the working grid: one height statistic per
node (``max`` = top surface like a DSM, ``mean``, ``min`` = a rough ground
estimate) and the mean RGB where the cloud carries colour (ODM's does).

A CRS is read from the LAS header (WKT or GeoTIFF keys); when the file has
none the caller supplies one (the task's CRS, or the other inputs').
"""

import struct

import numpy as np
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform

from .errors import InputError
from .grid import Grid
from .sources import PointCloudSource

try:
    import laspy
except ImportError as e:  # pragma: no cover
    raise ImportError("laspy is required to read point clouds") from e

CHUNK_POINTS = 2_000_000

# The sandbox caps address space and process count; the parallel lazrs backend
# would spawn one decompression thread per host core and fail. Single-threaded
# decompression of a chunk is still far faster than binning it.
_LAZ_BACKENDS = [b for b in (getattr(laspy.LazBackend, "Lazrs", None), getattr(laspy.LazBackend, "Laszip", None))
                 if b is not None and b.is_available()]


def _open(path):
    return laspy.open(path, laz_backend=_LAZ_BACKENDS or None)
_PROJECTED_CS_KEY = 3072
_GEOGRAPHIC_CS_KEY = 2048


def _crs_from_header(header) -> CRS | None:
    for vlr in list(header.vlrs) + list(getattr(header, "evlrs", []) or []):
        text = getattr(vlr, "string", None)
        if text and vlr.__class__.__name__.startswith("Wkt"):
            try:
                return CRS.from_wkt(text)
            except Exception:
                continue
        keys = getattr(vlr, "geo_keys", None)
        if keys:
            for key in keys:
                if key.id in (_PROJECTED_CS_KEY, _GEOGRAPHIC_CS_KEY) and key.tiff_tag_location == 0 \
                        and 1024 <= key.value_offset <= 32767:
                    try:
                        return CRS.from_epsg(int(key.value_offset))
                    except Exception:
                        continue
    return None


def describe(name: str, path: str) -> PointCloudSource:
    try:
        with _open(path) as reader:
            header = reader.header
            count = int(header.point_count)
            mins, maxs = header.mins, header.maxs
            has_rgb = "red" in header.point_format.dimension_names
            crs = _crs_from_header(header)
    except laspy.errors.LaspyException as e:
        raise InputError(f"{name}: cannot read point cloud ({e})") from e
    except struct.error as e:
        raise InputError(f"{name}: corrupt LAS/LAZ header ({e})") from e
    if count == 0:
        raise InputError(f"{name}: point cloud is empty")
    return PointCloudSource(
        name, path, count,
        (float(mins[0]), float(mins[1]), float(maxs[0]), float(maxs[1])),
        (float(mins[2]), float(maxs[2])), crs, bool(has_rgb),
    )


def rasterize(source: PointCloudSource, grid: Grid, *, stat: str = "max", want_rgb: bool = True,
              src_crs: CRS | None = None, progress=None):
    """Bin the cloud onto ``grid`` nodes.

    Returns ``(z, rgb)``: ``z`` is a float32 grid (NaN where no point fell),
    ``rgb`` a uint8 ``(3, H, W)`` grid (0 where no colour) or None.
    """
    crs = src_crs or source.crs
    if crs is None:
        raise InputError(f"{source.name}: point cloud has no CRS and none could be inferred")
    reproject = crs != grid.crs
    n = grid.width * grid.height
    if stat == "max":
        acc = np.full(n, -np.inf, dtype=np.float64)
    elif stat == "min":
        acc = np.full(n, np.inf, dtype=np.float64)
    else:
        acc = np.zeros(n, dtype=np.float64)
    counts = np.zeros(n, dtype=np.int64)
    colour = np.zeros((3, n), dtype=np.float64) if (want_rgb and source.has_rgb) else None
    inv_res = 1.0 / grid.res
    done = 0

    with _open(source.path) as reader:
        for points in reader.chunk_iterator(CHUNK_POINTS):
            xs = np.asarray(points.x, dtype=np.float64)
            ys = np.asarray(points.y, dtype=np.float64)
            zs = np.asarray(points.z, dtype=np.float64)
            if reproject:
                xs, ys = (np.asarray(v, dtype=np.float64) for v in warp_transform(crs, grid.crs, xs, ys))
            # Node (col, row) owns the half-open cell centred on it.
            cols = np.floor((xs - grid.x0) * inv_res + 0.5).astype(np.int64)
            rows = np.floor((grid.y0 - ys) * inv_res + 0.5).astype(np.int64)
            inside = (cols >= 0) & (cols < grid.width) & (rows >= 0) & (rows < grid.height) & np.isfinite(zs)
            if not inside.any():
                done += len(xs)
                continue
            idx = rows[inside] * grid.width + cols[inside]
            zi = zs[inside]
            if stat == "max":
                np.maximum.at(acc, idx, zi)
            elif stat == "min":
                np.minimum.at(acc, idx, zi)
            else:
                acc += np.bincount(idx, weights=zi, minlength=n)
            counts += np.bincount(idx, minlength=n)
            if colour is not None:
                for i, dim in enumerate(("red", "green", "blue")):
                    c = np.asarray(getattr(points, dim), dtype=np.float64)[inside]
                    colour[i] += np.bincount(idx, weights=c, minlength=n)
            done += len(xs)
            if progress and source.count:
                progress.log(f"points binned: {done:,} / {source.count:,}")

    has = counts > 0
    if not has.any():
        raise InputError(f"{source.name}: no points fall inside the reconstruction area")
    z = np.full(n, np.nan, dtype=np.float32)
    if stat in ("max", "min"):
        z[has] = acc[has]
    else:
        z[has] = acc[has] / counts[has]
    rgb = None
    if colour is not None:
        mean = np.zeros_like(colour)
        mean[:, has] = colour[:, has] / counts[has]
        # LAS colour is nominally 16-bit; many writers store 8-bit values.
        if mean.max() > 255:
            mean /= 257.0
        rgb = np.clip(np.rint(mean), 0, 255).astype(np.uint8).reshape(3, grid.height, grid.width)
    return z.reshape(grid.height, grid.width), rgb


def suggested_cell(source: PointCloudSource, extent_m: float, max_size: int) -> float:
    """Node spacing giving roughly two points per cell, bounded by the grid limit."""
    w, s, e, n = source.bounds
    area = max((e - w) * (n - s), 1e-6)
    density = source.count / area
    cell = np.sqrt(2.0 / density) if density > 0 else extent_m / (max_size - 1)
    return float(max(cell, extent_m / (max_size - 1), 0.02))
