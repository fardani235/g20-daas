"""Volume computation over a DSM raster.

Given a polygon (GeoJSON, EPSG:4326) and a DSM GeoTIFF, mask the DSM to the
polygon, fit a best-fit base plane through the DSM elevations at the polygon's
boundary vertices, and integrate fill (above the plane) and cut (below) by a
per-pixel Riemann sum. Volumes are m3, area m2.
"""

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_geom


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


def compute_volume(path, polygon_4326):
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

        # Cell centres in the raster CRS. Affine: x = c + col*a + row*b,
        # y = f + col*d + row*e; +0.5 shifts from corner to centre.
        xs_c = transform.c + (cols + 0.5) * transform.a + (rows + 0.5) * transform.b
        ys_c = transform.f + (cols + 0.5) * transform.d + (rows + 0.5) * transform.e

        # Sample DSM elevation at each polygon boundary vertex for the base plane.
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
            base = float(band.mean())
            plane_z = np.full(rows.size, base)
            base_plane = "mean_fallback"
        else:
            evaluate, base_plane = _fit_base_plane(vx, vy, vz)
            plane_z = evaluate(xs_c, ys_c)

        dsm_z = band.data[rows, cols].astype(float)
        diff = dsm_z - plane_z
        fill = float(np.clip(diff, 0, None).sum() * cell_area)
        cut = float(np.clip(-diff, 0, None).sum() * cell_area)
        return {
            "volume": fill - cut,
            "fill": fill,
            "cut": cut,
            "area": float(rows.size * cell_area),
            "base_plane": base_plane,
        }
