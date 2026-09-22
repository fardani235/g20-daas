"""Synthetic drone-mapping fixtures.

A small scene in UTM 33N: flat ground at 100 m with a "building" block (8 m
tall, grey) and a "tree" patch (6 m tall, green) on a green/brown ground, as an
RGBA orthophoto at 0.2 m, a DSM at 0.4 m and a DTM at 0.5 m — deliberately on
three different grids (and, for one fixture, a different CRS) so alignment is
exercised by every test.
"""

import json
import os
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

ORIGIN = (500000.0, 4500000.0)
SIZE_M = 40.0
CRS = "EPSG:32633"

# Scene layout in metres from the top-left corner.
BUILDING = (10, 10, 22, 20)   # x0, y0, x1, y1
TREES = (26, 24, 36, 34)


def _scene(res):
    n = int(SIZE_M / res)
    yy, xx = np.mgrid[0:n, 0:n]
    x, y = (xx + 0.5) * res, (yy + 0.5) * res
    building = (x >= BUILDING[0]) & (x < BUILDING[2]) & (y >= BUILDING[1]) & (y < BUILDING[3])
    trees = (x >= TREES[0]) & (x < TREES[2]) & (y >= TREES[1]) & (y < TREES[3])
    return n, building, trees, x, y


def write_orthophoto(path, res=0.2, alpha=True, crs=CRS):
    n, building, trees, x, y = _scene(res)
    rgb = np.zeros((3, n, n), dtype="uint8")
    rgb[0], rgb[1], rgb[2] = 120, 150, 70            # grass-ish ground
    rgb[:, building] = np.array([140, 140, 140])[:, None]
    rgb[0, trees], rgb[1, trees], rgb[2, trees] = 30, 110, 30
    bands = [rgb[0], rgb[1], rgb[2]]
    if alpha:
        a = np.full((n, n), 255, dtype="uint8")
        a[:, :int(2 / res)] = 0                       # a 2 m nodata strip on the left
        bands.append(a)
    with rasterio.open(
        path, "w", driver="GTiff", height=n, width=n, count=len(bands), dtype="uint8",
        crs=crs, transform=from_origin(ORIGIN[0], ORIGIN[1], res, res),
    ) as ds:
        for i, b in enumerate(bands, 1):
            ds.write(b, i)
    return str(path)


def write_dtm(path, res=0.5, slope=0.0):
    n, _, _, x, y = _scene(res)
    z = (100.0 + slope * x).astype("float32")
    z[0, 0] = -9999
    with rasterio.open(
        path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32",
        crs=CRS, transform=from_origin(ORIGIN[0], ORIGIN[1], res, res), nodata=-9999,
    ) as ds:
        ds.write(z, 1)
    return str(path)


def write_dsm(path, res=0.4, slope=0.0):
    n, building, trees, x, y = _scene(res)
    z = (100.0 + slope * x).astype("float32")
    z[building] += 8.0
    z[trees] += 6.0 + 0.5 * np.sin(x[trees] * 3)
    z[0, 0] = -9999
    with rasterio.open(
        path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32",
        crs=CRS, transform=from_origin(ORIGIN[0], ORIGIN[1], res, res), nodata=-9999,
    ) as ds:
        ds.write(z, 1)
    return str(path)


def reproject_to(src_path, dst_path, dst_crs="EPSG:4326"):
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds)
        profile = src.profile.copy()
        profile.update(crs=dst_crs, transform=transform, width=width, height=height)
        with rasterio.open(dst_path, "w", **profile) as dst:
            for i in range(1, src.count + 1):
                reproject(rasterio.band(src, i), rasterio.band(dst, i),
                          resampling=Resampling.nearest)
    return str(dst_path)


@pytest.fixture
def scene(tmp_path):
    return {
        "orthophoto": write_orthophoto(tmp_path / "ortho.tif"),
        "dsm": write_dsm(tmp_path / "dsm.tif"),
        "dtm": write_dtm(tmp_path / "dtm.tif"),
    }


@pytest.fixture
def make_request(tmp_path):
    def _make(inputs, params=None, vector=False):
        out = tmp_path / ("out.geojson" if vector else "out.tif")
        req = {
            "inputs": inputs,
            "params": params or {},
            "output_path": str(out),
            "result_path": str(tmp_path / "result.json"),
            "work_dir": str(tmp_path),
        }
        path = tmp_path / "request.json"
        path.write_text(json.dumps(req))
        return req, str(path)
    return _make


def scene_mask(path):
    """Boolean masks of the building and tree footprints on an output raster's grid."""
    with rasterio.open(path) as ds:
        labels = ds.read(1)
        res = ds.transform.a
        n = ds.width
    yy, xx = np.mgrid[0:ds.height, 0:n]
    x, y = (xx + 0.5) * res + (ds.transform.c - ORIGIN[0]), (yy + 0.5) * res + (ORIGIN[1] - ds.transform.f)
    building = (x >= BUILDING[0] + 1) & (x < BUILDING[2] - 1) & (y >= BUILDING[1] + 1) & (y < BUILDING[3] - 1)
    trees = (x >= TREES[0] + 1) & (x < TREES[2] - 1) & (y >= TREES[1] + 1) & (y < TREES[3] - 1)
    ground = ~((x >= BUILDING[0] - 1) & (x < BUILDING[2] + 1) & (y >= BUILDING[1] - 1) & (y < BUILDING[3] + 1)) \
        & ~((x >= TREES[0] - 1) & (x < TREES[2] + 1) & (y >= TREES[1] - 1) & (y < TREES[3] + 1)) & (x > 3)
    return labels, building, trees, ground
