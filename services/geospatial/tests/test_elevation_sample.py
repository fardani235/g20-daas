"""Elevation sampling of a DSM / DTM for flight planning (POST /raster/sample)."""

import asyncio

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.transform import from_origin
from rasterio.warp import transform

from app.routers.raster import SampleRequest, raster_sample
from app.utils.elevation import ElevationSampleError, sample_points

EPSG = "EPSG:32748"          # UTM 48S (West Java)
X0, Y0 = 780000.0, 9245000.0  # top-left corner
SIZE = 400                    # 400 x 400 px at 0.5 m = 200 m square


def _raster(tmp_path, fn, *, res=0.5, size=SIZE, nodata=-9999.0, name="dsm.tif", crs=EPSG, overviews=True):
    """Write ``fn(x_m, y_m)`` (metres east / south of the top-left corner) as a GeoTIFF."""
    ys, xs = np.mgrid[0:size, 0:size]
    data = fn((xs + 0.5) * res, (ys + 0.5) * res).astype("float32")
    path = str(tmp_path / name)
    with rasterio.open(path, "w", driver="GTiff", height=size, width=size, count=1, dtype="float32",
                       crs=crs, transform=from_origin(X0, Y0, res, res), nodata=nodata,
                       tiled=True, blockxsize=256, blockysize=256) as ds:
        ds.write(data, 1)
        if overviews:
            ds.build_overviews([2, 4, 8], rasterio.enums.Resampling.average)
    return path


def _lnglat(x_m, y_m):
    """Metres east / south of the top-left corner -> [lng, lat]."""
    lng, lat = transform(EPSG, "EPSG:4326", [X0 + x_m], [Y0 - y_m])
    return [lng[0], lat[0]]


def test_plane_is_reproduced(tmp_path):
    path = _raster(tmp_path, lambda x, y: 700 + 0.1 * x + 0.05 * y)
    pts = [(20, 20), (100, 100), (180, 60), (50, 170)]
    out = sample_points(path, [_lnglat(x, y) for x, y in pts], spacing_m=5.0, statistic="mean")
    assert out["resolution_m"] == 0.5
    assert out["cell_m"] == 7.5           # each value summarises 1.5 x the spacing
    assert out["covered"] == 4
    for (x, y), v in zip(pts, out["values"]):
        assert v == pytest.approx(700 + 0.1 * x + 0.05 * y, abs=0.25)


def test_max_sees_a_narrow_obstacle_that_the_mean_smooths_away(tmp_path):
    # Flat ground at 700 m with a 4 m wide, 30 m tall mast-like block at (100, 100).
    def surface(x, y):
        return np.where((abs(x - 100) < 2) & (abs(y - 100) < 2), 730.0, 700.0)

    path = _raster(tmp_path, surface)
    near = [_lnglat(104, 103)]           # 4-5 m from the block's centre
    top = sample_points(path, near, spacing_m=20.0, statistic="max")["values"][0]
    mean = sample_points(path, near, spacing_m=20.0, statistic="mean")["values"][0]
    low = sample_points(path, near, spacing_m=20.0, statistic="min")["values"][0]
    assert top == pytest.approx(730.0, abs=0.01)     # the real top, not an average of it
    assert mean < 703.0
    assert low == pytest.approx(700.0, abs=0.01)
    # A grid 20 m apart cannot step over the block, wherever the grid happens
    # to fall: some point always has it in its neighbourhood.
    for phase in range(0, 20, 3):
        grid = [_lnglat(x, y) for x in range(20 + phase, 181, 20) for y in range(20 + phase, 181, 20)]
        seen = sample_points(path, grid, spacing_m=20.0)["values"]
        assert max(seen) == pytest.approx(730.0, abs=0.01), phase
        assert sum(1 for v in seen if v > 701) <= 9, phase       # ...and it stays local (3 x 3 at most)
    assert sample_points(path, [_lnglat(140, 100)], spacing_m=20.0)["values"][0] == pytest.approx(700.0, abs=0.01)
    # Far from the block all three agree.
    far = sample_points(path, [_lnglat(30, 30)], spacing_m=20.0, statistic="max")["values"][0]
    assert far == pytest.approx(700.0, abs=0.01)


def test_outside_and_nodata_are_null_not_errors(tmp_path):
    def surface(x, y):
        return np.where(x < 50, -9999.0, 700.0)

    path = _raster(tmp_path, surface)
    out = sample_points(path, [_lnglat(150, 100), _lnglat(10, 100), _lnglat(-500, 100), _lnglat(100, 5000)],
                        spacing_m=5.0)
    assert out["values"][0] == pytest.approx(700.0, abs=0.01)
    assert out["values"][1:] == [None, None, None]
    assert out["covered"] == 1
    # Nothing on the raster at all still answers.
    none = sample_points(path, [_lnglat(-500, -500)], spacing_m=5.0)
    assert none["values"] == [None] and none["covered"] == 0


def test_large_requests_fall_back_to_overviews_and_coarser_cells(tmp_path, monkeypatch):
    path = _raster(tmp_path, lambda x, y: 700 + 0.1 * x)
    from app.utils import elevation

    opened = []
    real_open = rasterio.open

    def spy(p, *a, **kw):
        opened.append(kw.get("overview_level"))
        return real_open(p, *a, **kw)

    monkeypatch.setattr(elevation.rasterio, "open", spy)
    monkeypatch.setattr(elevation, "MAX_SOURCE_PIXELS", 5000)     # the 160 m window is 102 400 px natively
    monkeypatch.setattr(elevation, "MAX_CELLS", 400)
    out = sample_points(path, [_lnglat(20, 20), _lnglat(180, 180)], spacing_m=1.0, statistic="mean")
    assert opened[-1] == 2                                         # the 8x overview: 1600 px
    assert out["cell_m"] > 1.5                                     # cells grew to respect the budget
    assert out["values"][0] == pytest.approx(702.0, abs=1.5)
    assert out["values"][1] == pytest.approx(718.0, abs=1.5)


def test_spacing_finer_than_the_raster_uses_native_cells(tmp_path):
    path = _raster(tmp_path, lambda x, y: 700 + 0.1 * x, res=2.0, size=100, overviews=False)
    out = sample_points(path, [_lnglat(101, 50)], spacing_m=0.5, statistic="mean")
    assert out["cell_m"] == 3.0           # the spacing is raised to the 2 m pixel size
    assert out["values"][0] == pytest.approx(710.1, abs=0.25)


def test_foot_based_raster_answers_in_metres(tmp_path):
    # EPSG:2229 (California zone 5, US survey feet): heights in feet.
    path = str(tmp_path / "ft.tif")
    data = np.full((100, 100), 1000.0, dtype="float32")
    with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32",
                       crs="EPSG:2229", transform=from_origin(6400000, 1900000, 1.0, 1.0), nodata=-9999) as ds:
        ds.write(data, 1)
    lng, lat = transform("EPSG:2229", "EPSG:4326", [6400050.0], [1899950.0])
    out = sample_points(path, [[lng[0], lat[0]]], spacing_m=3.0, statistic="mean")
    assert out["values"][0] == pytest.approx(1000 * 1200 / 3937, abs=0.01)
    assert out["resolution_m"] == pytest.approx(0.3048, abs=0.001)


def test_bad_requests(tmp_path):
    path = _raster(tmp_path, lambda x, y: 700 + 0 * x)
    for kwargs in ({"points": []}, {"points": [[1]]}, {"points": [["a", "b"]]}, {"points": [[200, 0]]},
                   {"points": [[0, 0]], "spacing_m": 0}, {"points": [[0, 0]], "statistic": "median"},
                   {"points": [[0, 0]] * 20001}):
        with pytest.raises(ElevationSampleError):
            sample_points(path, **kwargs)
    plain = str(tmp_path / "nocrs.tif")
    with rasterio.open(plain, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32") as ds:
        ds.write(np.zeros((4, 4), dtype="float32"), 1)
    with pytest.raises(ElevationSampleError):
        sample_points(plain, [[0, 0]])


def test_endpoint(tmp_path):
    path = _raster(tmp_path, lambda x, y: 700 + 0.1 * x)
    res = asyncio.run(raster_sample(SampleRequest(path=path, points=[_lnglat(100, 100)], spacing_m=10,
                                                  statistic="mean")))
    assert set(res) == {"values", "resolution_m", "cell_m", "statistic", "covered"}
    assert res["values"][0] == pytest.approx(710.0, abs=0.8)
    for bad, status in ((SampleRequest(path="/nonexistent/x.tif", points=[[0, 0]]), 404),
                        (SampleRequest(path="relative.tif", points=[[0, 0]]), 400),
                        (SampleRequest(path="s3://not-allowed/x.tif", points=[[0, 0]]), 400),
                        (SampleRequest(path=path, points=[[0, 0]], statistic="median"), 422)):
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(raster_sample(bad))
        assert excinfo.value.status_code == status
