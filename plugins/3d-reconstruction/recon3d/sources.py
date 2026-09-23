"""Input detection and description.

``detect`` looks at the files the sandbox handed over, opens each one just far
enough to describe it (CRS, bounds, resolution, bands / point count) and
reports what is usable. Nothing heavy is read here; the workflow decides what
to do with the description and the heightfield/texture modules do the reading.
"""

import os
import zipfile
from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds

from .errors import InputError

RASTER_INPUTS = ("dsm", "dtm", "orthophoto")


@dataclass
class RasterSource:
    name: str
    path: str
    crs: CRS
    bounds: tuple            # native CRS (west, south, east, north)
    res: float               # native ground sample distance (CRS units)
    width: int
    height: int
    count: int
    dtype: str
    nodata: object
    has_alpha: bool

    def bounds_in(self, crs: CRS):
        if self.crs == crs:
            return tuple(self.bounds)
        return transform_bounds(self.crs, crs, *self.bounds, densify_pts=21)

    def res_in(self, crs: CRS) -> float:
        """Approximate GSD in ``crs`` units (rescaled by the footprint ratio)."""
        if self.crs == crs:
            return self.res
        w, s, e, n = self.bounds_in(crs)
        native_extent = max(self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1])
        return self.res * max(e - w, n - s) / native_extent if native_extent else self.res


@dataclass
class PointCloudSource:
    name: str
    path: str
    count: int
    bounds: tuple            # (west, south, east, north) in the cloud's CRS
    z_range: tuple
    crs: object              # CRS or None when the file carries none
    has_rgb: bool
    error: str | None = None  # why it cannot be used (e.g. laspy missing)


@dataclass
class ModelSource:
    name: str
    path: str
    format: str              # "glb" | "gltf-zip"
    size_bytes: int


@dataclass
class Inputs:
    dsm: RasterSource | None = None
    dtm: RasterSource | None = None
    orthophoto: RasterSource | None = None
    point_cloud: PointCloudSource | None = None
    model: ModelSource | None = None
    notes: list = field(default_factory=list)

    def available(self) -> list:
        out = [n for n in ("dsm", "dtm", "orthophoto") if getattr(self, n) is not None]
        if self.point_cloud is not None and not self.point_cloud.error:
            out.append("point_cloud")
        if self.model is not None:
            out.append("model")
        return out

    def rasters(self) -> list:
        return [r for r in (self.dsm, self.dtm, self.orthophoto) if r is not None]


def open_raster(name: str, path: str) -> RasterSource:
    try:
        ds = rasterio.open(path)
    except Exception as e:
        raise InputError(f"{name}: cannot open raster ({e})") from e
    with ds:
        if ds.crs is None:
            raise InputError(f"{name}: raster has no coordinate reference system")
        if ds.width < 2 or ds.height < 2:
            raise InputError(f"{name}: raster is too small ({ds.width}x{ds.height})")
        res = float(max(abs(ds.transform.a), abs(ds.transform.e)))
        alpha = any(m == rasterio.enums.ColorInterp.alpha for m in ds.colorinterp) or ds.count in (2, 4)
        return RasterSource(
            name, path, ds.crs, tuple(ds.bounds), res, ds.width, ds.height, ds.count,
            str(ds.dtypes[0]), ds.nodata, bool(alpha),
        )


def describe_point_cloud(name: str, path: str) -> PointCloudSource:
    try:
        from . import pointcloud
    except ImportError as e:  # pragma: no cover - laspy is part of the sandbox image
        return PointCloudSource(name, path, 0, (0, 0, 0, 0), (0, 0), None, False, error=str(e))
    try:
        return pointcloud.describe(name, path)
    except InputError as e:
        return PointCloudSource(name, path, 0, (0, 0, 0, 0), (0, 0), None, False, error=str(e))


def describe_model(name: str, path: str) -> ModelSource:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == b"glTF":
        return ModelSource(name, path, "glb", size)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".gltf", ".glb"))]
        if names:
            return ModelSource(name, path, "gltf-zip", size)
        raise InputError(f"{name}: archive contains no .gltf or .glb file")
    raise InputError(f"{name}: not a GLB file or a zip of a glTF model")


def detect(inputs: dict) -> Inputs:
    """Describe every input the run received; unusable ones are reported, not fatal."""
    found = Inputs()
    for name, path in (inputs or {}).items():
        if not path or not os.path.isfile(path):
            found.notes.append(f"{name}: file missing")
            continue
        if name in RASTER_INPUTS:
            setattr(found, name, open_raster(name, path))
        elif name == "point_cloud":
            found.point_cloud = describe_point_cloud(name, path)
            if found.point_cloud.error:
                found.notes.append(f"point_cloud unusable: {found.point_cloud.error}")
        elif name == "model":
            found.model = describe_model(name, path)
        else:
            found.notes.append(f"{name}: unknown input, ignored")
    return found


# --------------------------------------------------------------------------
# Working CRS
# --------------------------------------------------------------------------

def utm_crs_for(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def working_crs(candidates: list, fallback_epsg: int | None = None) -> CRS:
    """A projected (metric) CRS for the mesh.

    ``candidates`` are CRS objects or ``(crs, bounds)`` pairs, in order of
    preference. The first projected one wins (the DSM's UTM zone for ODM
    outputs); geographic-only inputs are projected to the UTM zone of their
    centroid so the mesh is still in metres.
    """
    pairs = []
    for c in candidates:
        crs, bounds = (c if isinstance(c, tuple) else (c, None))
        if crs is not None:
            pairs.append((crs, bounds))
    for crs, _ in pairs:
        if crs.is_projected:
            return crs
    if fallback_epsg:
        try:
            crs = CRS.from_epsg(int(fallback_epsg))
            if crs.is_projected:
                return crs
        except Exception:
            pass
    for crs, bounds in pairs:
        if bounds is None:
            continue
        w, s, e, n = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
        return utm_crs_for((w + e) / 2, (s + n) / 2)
    raise InputError("no input carries a usable projected coordinate reference system")


def bounds_to_4326(crs: CRS, bounds) -> list:
    w, s, e, n = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    return [float(w), float(s), float(e), float(n)]


def intersection(*bounds_list):
    bl = [b for b in bounds_list if b is not None]
    if not bl:
        return None
    w = max(b[0] for b in bl)
    s = max(b[1] for b in bl)
    e = min(b[2] for b in bl)
    n = min(b[3] for b in bl)
    if e <= w or n <= s:
        return None
    return (w, s, e, n)


def union(*bounds_list):
    bl = [b for b in bounds_list if b is not None]
    if not bl:
        return None
    return (min(b[0] for b in bl), min(b[1] for b in bl), max(b[2] for b in bl), max(b[3] for b in bl))


def epsg_of(crs: CRS) -> int | None:
    try:
        code = crs.to_epsg()
    except Exception:
        return None
    return int(code) if code else None


def nan_nodata_read(vrt, band=1):
    """Read one band as float32 with no-data as NaN."""
    arr = vrt.read(band, masked=True)
    return np.ma.filled(arr.astype(np.float32), np.nan)
