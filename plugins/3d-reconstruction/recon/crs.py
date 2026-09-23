"""Coordinate reference system helpers (rasterio/PROJ only, no pyproj dependency).

Everything the plugin does is in the task's projected CRS (ODM uses a UTM
zone). ``Georef`` is the one record passed around: EPSG (when known), WKT,
the origin the GLB coordinates are relative to and the absolute bounds.
"""

from dataclasses import dataclass, field

from rasterio.crs import CRS
from rasterio.warp import transform_bounds

from .errors import InputError


@dataclass
class Georef:
    crs: CRS | None
    origin: tuple[float, float, float]
    bounds: tuple[float, float, float, float]  # minx, miny, maxx, maxy in ``crs``
    z_range: tuple[float, float] = (0.0, 0.0)
    source: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def epsg(self) -> int | None:
        if self.crs is None:
            return None
        try:
            return self.crs.to_epsg()
        except Exception:
            return None

    def bounds_4326(self):
        if self.crs is None:
            return None
        minx, miny, maxx, maxy = transform_bounds(self.crs, "EPSG:4326", *self.bounds, densify_pts=21)
        return [float(minx), float(miny), float(maxx), float(maxy)]

    def to_extras(self) -> dict:
        out = {
            "epsg": self.epsg,
            "wkt": self.crs.to_wkt() if self.crs is not None else None,
            "origin": [float(v) for v in self.origin],
            "bounds": [float(v) for v in self.bounds],
            "z_range": [float(v) for v in self.z_range],
            "source": self.source,
        }
        out.update(self.extra)
        return out


def crs_from_epsg(epsg) -> CRS | None:
    try:
        return CRS.from_epsg(int(epsg)) if epsg else None
    except Exception:
        return None


def crs_from_wkt(wkt: str | None) -> CRS | None:
    if not wkt:
        return None
    try:
        return CRS.from_wkt(wkt)
    except Exception:
        try:
            return CRS.from_user_input(wkt)
        except Exception:
            return None


def require_projected(crs: CRS | None, what: str) -> CRS:
    if crs is None:
        raise InputError(f"{what} has no coordinate reference system; the task must be georeferenced")
    if crs.is_geographic:
        raise InputError(f"{what} is in geographic coordinates ({crs}); a projected (metric) CRS is required")
    return crs


def same_crs(a: CRS | None, b: CRS | None) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    ea, eb = a.to_epsg(), b.to_epsg()
    return ea is not None and ea == eb


def local_origin(bounds, zmin: float) -> tuple[float, float, float]:
    """A round origin near the centre of ``bounds`` (whole metres keep the numbers readable)."""
    minx, miny, maxx, maxy = bounds
    return (float(round((minx + maxx) / 2)), float(round((miny + maxy) / 2)), float(round(zmin)))
