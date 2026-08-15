"""Base-method and unit-scaling behaviour of compute_volume.

Reference behaviour is WebODM's coreplugins/measure/volume.py calc_volume:
its default base method is "triangulate" (a TIN through the polygon's boundary
vertex elevations), with plane/average/highest/lowest as alternatives.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from app.utils.volume import compute_volume


def _write_dsm(path, elev, crs="EPSG:32615", band_units=None):
    h, w = elev.shape
    transform = from_origin(500000, 4500000, 1.0, 1.0)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs=crs, transform=transform, nodata=-9999,
    ) as ds:
        ds.write(elev.astype("float32"), 1)
        if band_units:
            ds.units = (band_units,)
    return str(path), transform


def _ring(transform, verts, crs="EPSG:32615"):
    """Polygon through (row, col) pixel CENTRES -> GeoJSON in EPSG:4326."""
    ring = [
        [transform.c + (c + 0.5) * transform.a, transform.f + (r + 0.5) * transform.e]
        for r, c in verts
    ]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return transform_geom(crs, "EPSG:4326", {"type": "Polygon", "coordinates": [ring]})


def _square(transform, r0, r1, c0, c1, crs="EPSG:32615"):
    return _ring(transform, [(r0, c0), (r0, c1), (r1, c1), (r1, c0)], crs=crs)


def _block_dsm(tmp_path, name="b.tif"):
    """Flat ground at 10 m with a 20x20 m, 5 m high block => 2000 m3."""
    elev = np.full((100, 100), 10.0)
    elev[40:60, 40:60] = 15.0
    return _write_dsm(tmp_path / name, elev)


# --- default method ---------------------------------------------------------

def test_triangulate_is_the_default_method(tmp_path):
    path, tr = _block_dsm(tmp_path)
    res = compute_volume(path, _square(tr, 30, 70, 30, 70))
    assert res["base_plane"] == "triangulate"


def test_triangulate_measures_block_over_flat_boundary(tmp_path):
    # All boundary vertices sit on flat 10 m ground, so the TIN is a flat
    # 10 m base and the block's 2000 m3 is recovered.
    path, tr = _block_dsm(tmp_path)
    res = compute_volume(path, _square(tr, 30, 70, 30, 70), base_method="triangulate")
    assert abs(res["volume"] - 2000) < 100
    assert res["cut"] < 50


# --- explicit methods ------------------------------------------------------

def test_plane_method_recovers_previous_best_fit_behaviour(tmp_path):
    path, tr = _block_dsm(tmp_path)
    res = compute_volume(path, _square(tr, 30, 70, 30, 70), base_method="plane")
    assert res["base_plane"] == "plane"
    assert abs(res["volume"] - 2000) < 100


def test_plane_absorbs_tilted_ground(tmp_path):
    yy, xx = np.mgrid[0:100, 0:100]
    elev = 10.0 + 0.1 * xx + 0.05 * yy
    elev[40:60, 40:60] += 5.0
    path, tr = _write_dsm(tmp_path / "tilt.tif", elev)
    res = compute_volume(path, _square(tr, 30, 70, 30, 70), base_method="plane")
    assert abs(res["volume"] - 2000) < 150


def test_highest_base_yields_net_cut(tmp_path):
    path, tr = _block_dsm(tmp_path)
    res = compute_volume(path, _square(tr, 30, 70, 30, 70), base_method="highest")
    assert res["base_plane"] == "highest"
    # Base is the highest boundary sample (flat 10 m ground), so the block is
    # still fill; the surrounding ground contributes no cut.
    assert res["volume"] > 0


def test_lowest_and_average_bases_differ_on_sloped_ground(tmp_path):
    yy, xx = np.mgrid[0:100, 0:100]
    elev = 10.0 + 0.2 * xx
    path, tr = _write_dsm(tmp_path / "slope.tif", elev)
    poly = _square(tr, 30, 70, 30, 70)
    low = compute_volume(path, poly, base_method="lowest")
    avg = compute_volume(path, poly, base_method="average")
    high = compute_volume(path, poly, base_method="highest")
    assert low["volume"] > avg["volume"] > high["volume"]
    assert low["base_plane"] == "lowest"


def test_invalid_base_method_raises_value_error(tmp_path):
    path, tr = _block_dsm(tmp_path)
    with pytest.raises(ValueError, match="base method"):
        compute_volume(path, _square(tr, 30, 70, 30, 70), base_method="bogus")


# --- triangulate vs plane on non-planar ground -----------------------------

def test_triangulate_follows_undulating_ground_unlike_plane(tmp_path):
    """On non-planar terrain the TIN tracks the boundary; a plane averages it.

    This is the divergence that made our plane-only result disagree with stock
    WebODM, whose default is triangulate.
    """
    yy, xx = np.mgrid[0:100, 0:100]
    elev = 10.0 + 3.0 * np.sin(xx / 12.0) + 2.0 * np.cos(yy / 9.0)
    elev[45:55, 45:55] += 6.0
    path, tr = _write_dsm(tmp_path / "ridge.tif", elev)
    octagon = [(30, 50), (36, 66), (50, 70), (64, 66),
               (70, 50), (64, 34), (50, 30), (36, 34)]
    poly = _ring(tr, octagon)
    tri = compute_volume(path, poly, base_method="triangulate")
    pln = compute_volume(path, poly, base_method="plane")
    assert tri["base_plane"] == "triangulate"
    # Materially different, and triangulate reports the larger net here.
    assert tri["volume"] > pln["volume"] * 1.5


# --- unit scaling ----------------------------------------------------------

def test_band_units_in_feet_are_converted_to_cubic_metres(tmp_path):
    """A DSM whose band units are feet must be scaled by 0.3048**3."""
    elev = np.full((100, 100), 10.0)
    elev[40:60, 40:60] = 15.0  # 2000 ft3 of block
    path, tr = _write_dsm(tmp_path / "ft.tif", elev, band_units="ft")
    res = compute_volume(path, _square(tr, 30, 70, 30, 70), base_method="triangulate")
    assert abs(res["volume"] - 2000 * 0.3048 ** 3) < 5


def test_foot_based_crs_is_converted_even_without_band_units(tmp_path):
    """EPSG:2229 is in US survey feet but leaves band units unset.

    WebODM only reads band units and so misses this case; we fall back to the
    CRS's linear units.
    """
    elev = np.full((100, 100), 10.0)
    elev[40:60, 40:60] = 15.0
    path, tr = _write_dsm(tmp_path / "usft.tif", elev, crs="EPSG:2229")
    poly = _square(tr, 30, 70, 30, 70, crs="EPSG:2229")
    res = compute_volume(path, poly, base_method="triangulate")
    expected = 2000 * (1200.0 / 3937.0) ** 3
    assert abs(res["volume"] - expected) < 5


def test_metre_dsm_is_unscaled(tmp_path):
    path, tr = _block_dsm(tmp_path, "m.tif")
    res = compute_volume(path, _square(tr, 30, 70, 30, 70), base_method="triangulate")
    assert abs(res["volume"] - 2000) < 100
