"""Volume computation over a DSM raster.

Given a polygon (GeoJSON, EPSG:4326) and a DSM GeoTIFF, mask the DSM to the
polygon, build a base surface from the DSM elevations sampled at the polygon's
boundary vertices, and integrate fill (above the base) and cut (below) by a
per-pixel Riemann sum. Volumes are m3, area m2.

Base methods mirror WebODM's measure plugin (coreplugins/measure/volume.py):
"triangulate" (the default there and here) interpolates a TIN through the
boundary samples and so follows undulating ground; "plane" fits a single tilted
least-squares plane; "average"/"highest"/"lowest" use one flat level. Unlike
WebODM we report signed net volume plus separate fill and cut rather than a
single absolute number.
"""

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_geom
from scipy.interpolate import griddata

BASE_METHODS = ("triangulate", "plane", "average", "highest", "lowest")
DEFAULT_BASE_METHOD = "triangulate"

# Linear-unit names to metres, matching WebODM's app/geoutils.py UNIT_TO_M.
_UNIT_TO_M = {
    "m": 1.0,
    "metre": 1.0,
    "meter": 1.0,
    "metres": 1.0,
    "meters": 1.0,
    "ft": 0.3048,
    "foot": 0.3048,
    "feet": 0.3048,
    "us survey foot": 1200.0 / 3937.0,
    "us survey feet": 1200.0 / 3937.0,
    "usfeet": 1200.0 / 3937.0,
}


def _to_meters_factor(ds):
    """Linear units of ``ds`` expressed in metres.

    WebODM reads band units only, which GDAL leaves unset for most rasters --
    including genuinely foot-based CRSs like EPSG:2229 -- so its foot handling
    rarely fires. Prefer band units for parity, then fall back to the CRS's
    linear units so a foot-based DSM is still scaled correctly.
    """
    units = getattr(ds, "units", None) or ()
    if units:
        unit = units[0]
        if unit:
            factor = _UNIT_TO_M.get(str(unit).strip().lower())
            if factor is not None:
                return factor

    if ds.crs is not None:
        linear = getattr(ds.crs, "linear_units", None)
        if linear:
            factor = _UNIT_TO_M.get(str(linear).strip().lower())
            if factor is not None:
                return factor

    return 1.0


def _fit_base_plane(xs, ys, zs):
    """Return (evaluate(xq, yq) -> ndarray, label).

    Best-fit tilted plane z = a*(x-x0) + b*(y-y0) + c when there are >= 3
    non-collinear samples; otherwise a flat plane at the mean sample elevation,
    labelled "mean_fallback". Coordinates are centred on their mean to keep the
    least-squares fit well-conditioned for large UTM values.
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    zs = np.asarray(zs, dtype=float)
    if zs.size >= 3:
        x0, y0 = xs.mean(), ys.mean()
        A = np.column_stack([xs - x0, ys - y0, np.ones(zs.size)])
        if np.linalg.matrix_rank(A) >= 3:
            coef, *_ = np.linalg.lstsq(A, zs, rcond=None)

            def evaluate(xq, yq):
                return (
                    coef[0] * (np.asarray(xq, dtype=float) - x0)
                    + coef[1] * (np.asarray(yq, dtype=float) - y0)
                    + coef[2]
                )

            return evaluate, "best_fit"

    base = float(zs.mean()) if zs.size else 0.0

    def evaluate(xq, yq):
        return np.full(np.asarray(xq, dtype=float).shape, base)

    return evaluate, "mean_fallback"


def _empty():
    return {"volume": 0.0, "fill": 0.0, "cut": 0.0, "area": 0.0, "base_plane": "empty"}


def _flat_base(level, xs_c):
    return np.full(xs_c.shape, float(level))


def _triangulate_base(vx, vy, vz, xs_c, ys_c):
    """Linear TIN through the boundary samples, evaluated at cell centres.

    Cells outside the samples' convex hull come back NaN; fill them with the
    nearest sample so the whole polygon is still integrated (WebODM drops those
    cells silently via nansum, quietly shrinking the measured area).
    """
    pts = np.column_stack([np.asarray(vx, float), np.asarray(vy, float)])
    vz = np.asarray(vz, dtype=float)
    targets = (np.asarray(xs_c, float), np.asarray(ys_c, float))

    base = griddata(pts, vz, targets, method="linear")
    holes = np.isnan(base)
    if holes.any():
        nearest = griddata(pts, vz, targets, method="nearest")
        base = np.where(holes, nearest, base)
    return base


def _base_surface(base_method, vx, vy, vz, xs_c, ys_c):
    """Return (base elevations at cell centres, label)."""
    vz_arr = np.asarray(vz, dtype=float)

    if base_method == "triangulate":
        # A TIN needs 3 non-collinear samples; below that it degenerates to a
        # flat level, which is what "average" already does.
        if vz_arr.size >= 3:
            pts = np.unique(np.column_stack([vx, vy]), axis=0)
            if pts.shape[0] >= 3:
                base = _triangulate_base(vx, vy, vz_arr, xs_c, ys_c)
                if not np.isnan(base).any():
                    return base, "triangulate"
        return _flat_base(vz_arr.mean(), xs_c), "mean_fallback"

    if base_method == "plane":
        evaluate, label = _fit_base_plane(vx, vy, vz_arr)
        return evaluate(xs_c, ys_c), "plane" if label == "best_fit" else label

    if base_method == "average":
        return _flat_base(vz_arr.mean(), xs_c), "average"

    if base_method == "highest":
        return _flat_base(vz_arr.max(), xs_c), "highest"

    if base_method == "lowest":
        return _flat_base(vz_arr.min(), xs_c), "lowest"

    raise ValueError(f"Invalid base method {base_method}")


def compute_volume(path, polygon_4326, base_method=DEFAULT_BASE_METHOD):
    if base_method not in BASE_METHODS:
        raise ValueError(
            f"Invalid base method {base_method}; expected one of {', '.join(BASE_METHODS)}"
        )

    with rasterio.open(path) as ds:
        if ds.crs is None or not ds.crs.is_projected:
            raise ValueError("DSM is not in a projected CRS")

        geom = transform_geom("EPSG:4326", ds.crs, polygon_4326)

        try:
            data, transform = rio_mask(ds, [geom], crop=True, filled=False)
        except ValueError:
            # rasterio raises ValueError when the polygon does not overlap.
            return _empty()

        band = data[0]
        mask_arr = np.ma.getmaskarray(band)
        rows, cols = np.where(~mask_arr)
        if rows.size == 0:
            return _empty()

        cell_area = abs(transform.a * transform.e)
        # Elevations and horizontal distances share the raster's linear unit, so
        # volume scales by the cube of the to-metres factor and area by its square.
        to_meter = _to_meters_factor(ds)

        # Cell centres in the raster CRS. Affine: x = c + col*a + row*b,
        # y = f + col*d + row*e; +0.5 shifts from corner to centre.
        xs_c = transform.c + (cols + 0.5) * transform.a + (rows + 0.5) * transform.b
        ys_c = transform.f + (cols + 0.5) * transform.d + (rows + 0.5) * transform.e

        # Sample DSM elevation at each polygon boundary vertex for the base surface.
        # Boundary vertices legitimately land on polygon-edge pixels that crop=True
        # masks out even though they carry valid DSM data (filled=False preserves
        # band.data under the crop mask), so accept any pixel that is not nodata.
        inv = ~transform
        ring = geom["coordinates"][0]
        h, w = band.shape
        nodata = ds.nodata
        vx, vy, vz = [], [], []
        for px, py in ring:
            fcol, frow = inv * (px, py)
            c = int(np.floor(fcol))
            r = int(np.floor(frow))
            if 0 <= r < h and 0 <= c < w:
                val = float(band.data[r, c])
                if nodata is None or val != nodata:
                    vx.append(px)
                    vy.append(py)
                    vz.append(val)

        if not vz:
            plane_z = _flat_base(float(band.mean()), xs_c)
            base_plane = "mean_fallback"
        else:
            plane_z, base_plane = _base_surface(base_method, vx, vy, vz, xs_c, ys_c)

        dsm_z = band.data[rows, cols].astype(float)
        diff = dsm_z - plane_z
        volume_factor = cell_area * to_meter ** 3
        fill = float(np.clip(diff, 0, None).sum() * volume_factor)
        cut = float(np.clip(-diff, 0, None).sum() * volume_factor)
        return {
            "volume": fill - cut,
            "fill": fill,
            "cut": cut,
            "area": float(rows.size * cell_area * to_meter ** 2),
            "base_plane": base_plane,
        }
