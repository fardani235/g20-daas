import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from app.utils.volume import compute_volume, _fit_base_plane


def _write_dsm(tmp_path, elev):
    # UTM zone 15N, 1 m pixels, origin easting 500000 / northing 4500000.
    h, w = elev.shape
    transform = from_origin(500000, 4500000, 1.0, 1.0)
    path = str(tmp_path / "dsm.tif")
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs="EPSG:32615", transform=transform, nodata=-9999,
    ) as ds:
        ds.write(elev.astype("float32"), 1)
    return path, transform


def _utm_square_to_4326(transform, r0, r1, c0, c1):
    corners = [(r0, c0), (r0, c1), (r1, c1), (r1, c0), (r0, c0)]
    ring = []
    for r, c in corners:
        x = transform.c + c * transform.a
        y = transform.f + r * transform.e
        ring.append([x, y])
    geom_utm = {"type": "Polygon", "coordinates": [ring]}
    return transform_geom("EPSG:32615", "EPSG:4326", geom_utm)


def test_flat_surface_volume_near_zero(tmp_path):
    elev = np.full((100, 100), 10.0)
    path, tr = _write_dsm(tmp_path, elev)
    poly = _utm_square_to_4326(tr, 30, 70, 30, 70)
    res = compute_volume(path, poly)
    assert res["base_plane"] == "best_fit"
    assert abs(res["volume"]) < 1.0
    assert res["area"] > 1400


def test_block_volume(tmp_path):
    elev = np.full((100, 100), 10.0)
    elev[40:60, 40:60] = 15.0  # 20x20 m block, 5 m high => 2000 m3
    path, tr = _write_dsm(tmp_path, elev)
    poly = _utm_square_to_4326(tr, 30, 70, 30, 70)
    res = compute_volume(path, poly)
    assert abs(res["volume"] - 2000) < 100
    assert res["fill"] > res["cut"]


def test_polygon_outside_raster_is_empty(tmp_path):
    elev = np.full((50, 50), 10.0)
    path, tr = _write_dsm(tmp_path, elev)
    geom_utm = {"type": "Polygon", "coordinates": [[
        [400000, 4500000], [400010, 4500000],
        [400010, 4499990], [400000, 4499990], [400000, 4500000],
    ]]}
    poly = transform_geom("EPSG:32615", "EPSG:4326", geom_utm)
    res = compute_volume(path, poly)
    assert res == {"volume": 0.0, "fill": 0.0, "cut": 0.0, "area": 0.0, "base_plane": "empty"}


def test_fit_base_plane_collinear_falls_back():
    evaluate, label = _fit_base_plane([0, 1, 2], [0, 1, 2], [5, 5, 5])
    assert label == "mean_fallback"
    assert float(evaluate(0, 0)) == 5.0


def test_fit_base_plane_planar_best_fit():
    # z = 2x + 1
    evaluate, label = _fit_base_plane([0, 1, 0, 1], [0, 0, 1, 1], [1, 3, 1, 3])
    assert label == "best_fit"
    assert abs(float(evaluate(2, 0)) - 5.0) < 1e-6
