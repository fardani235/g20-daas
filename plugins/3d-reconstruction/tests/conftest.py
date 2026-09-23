"""Synthetic drone-mapping fixtures.

A 40 m x 40 m scene in UTM 33N: flat ground at 100 m with a 8 m "building"
block and a 6 m "tree" patch, as an RGBA orthophoto at 0.2 m, a DSM at 0.4 m
(with a no-data hole and a no-data strip), a DTM at 0.5 m, a coloured LAS
point cloud sampled from the DSM, and an "ODM-like" textured GLB — so every
workflow and every gap-filling path is exercised on data whose geometry is
known exactly.
"""

import json
import os
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

ORIGIN = (500000.0, 4500000.0)   # north-west corner
SIZE_M = 40.0
CRS = "EPSG:32633"
GROUND = 100.0
BUILDING = (10.0, 10.0, 22.0, 20.0)   # x0, y0, x1, y1 metres from the NW corner (y down)
BUILDING_H = 8.0
TREES = (26.0, 24.0, 36.0, 34.0)
TREES_H = 6.0


def surface_height(x, y):
    """DSM height at scene offsets (x east, y south of the NW corner)."""
    z = np.full(np.broadcast(x, y).shape, GROUND, dtype="float64")
    b = (x >= BUILDING[0]) & (x < BUILDING[2]) & (y >= BUILDING[1]) & (y < BUILDING[3])
    t = (x >= TREES[0]) & (x < TREES[2]) & (y >= TREES[1]) & (y < TREES[3])
    z[b] = GROUND + BUILDING_H
    z[t] = GROUND + TREES_H
    return z


def _nodes(res):
    n = int(SIZE_M / res)
    yy, xx = np.mgrid[0:n, 0:n]
    return n, (xx + 0.5) * res, (yy + 0.5) * res


def write_dsm(path, res=0.4, hole=True, strip=True):
    n, x, y = _nodes(res)
    z = surface_height(x, y).astype("float32")
    if hole:
        z[int(30 / res):int(33 / res), int(4 / res):int(7 / res)] = -9999   # interior hole (3x3 m)
    if strip:
        z[:, : int(1.0 / res)] = -9999                                  # 1 m no-data strip on the west
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32", crs=CRS,
                       transform=from_origin(ORIGIN[0], ORIGIN[1], res, res), nodata=-9999) as ds:
        ds.write(z, 1)
    return str(path)


def write_dtm(path, res=0.5):
    n, x, _y = _nodes(res)
    z = np.full((n, n), GROUND, dtype="float32") + (x / SIZE_M).astype("float32")  # gentle 1 m slope
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32", crs=CRS,
                       transform=from_origin(ORIGIN[0], ORIGIN[1], res, res), nodata=-9999) as ds:
        ds.write(z, 1)
    return str(path)


def scene_rgb(x, y):
    rgb = np.zeros((3,) + np.broadcast(x, y).shape, dtype="uint8")
    rgb[0], rgb[1], rgb[2] = 120, 150, 70
    b = (x >= BUILDING[0]) & (x < BUILDING[2]) & (y >= BUILDING[1]) & (y < BUILDING[3])
    t = (x >= TREES[0]) & (x < TREES[2]) & (y >= TREES[1]) & (y < TREES[3])
    rgb[:, b] = np.array([200, 40, 40])[:, None]      # red roof
    rgb[:, t] = np.array([30, 110, 30])[:, None]
    return rgb


def write_orthophoto(path, res=0.2, alpha=True):
    n, x, y = _nodes(res)
    rgb = scene_rgb(x, y)
    bands = [rgb[0], rgb[1], rgb[2]]
    if alpha:
        a = np.full((n, n), 255, dtype="uint8")
        a[:, : int(2.0 / res)] = 0                       # 2 m no-data strip on the west
        bands.append(a)
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=len(bands), dtype="uint8", crs=CRS,
                       transform=from_origin(ORIGIN[0], ORIGIN[1], res, res)) as ds:
        for i, b in enumerate(bands, 1):
            ds.write(b, i)
        if alpha:
            ds.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                              rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
    return str(path)


def write_las(path, spacing=0.25, with_crs=True, rgb=True, laz=False):
    import laspy
    n = int(SIZE_M / spacing)
    yy, xx = np.mgrid[0:n, 0:n]
    x = (xx + 0.5) * spacing + np.random.default_rng(1).uniform(-0.05, 0.05, xx.shape)
    y = (yy + 0.5) * spacing
    z = surface_height(x, y)
    colours = scene_rgb(x, y)
    header = laspy.LasHeader(point_format=3 if rgb else 1, version="1.2")
    header.offsets = [ORIGIN[0], ORIGIN[1] - SIZE_M, 0.0]
    header.scales = [0.001, 0.001, 0.001]
    if with_crs:
        header.vlrs.append(_pyproj_free_crs())   # GeoTIFF keys, no pyproj needed
    las = laspy.LasData(header)
    las.x = ORIGIN[0] + x.ravel()
    las.y = ORIGIN[1] - y.ravel()
    las.z = z.ravel()
    if rgb:
        las.red = colours[0].ravel().astype("uint16") * 257
        las.green = colours[1].ravel().astype("uint16") * 257
        las.blue = colours[2].ravel().astype("uint16") * 257
    las.write(str(path))
    return str(path)


def _pyproj_free_crs():
    """laspy's add_crs wants a pyproj CRS; build a GeoKeyDirectory VLR by hand instead."""
    from laspy.vlrs.known import GeoKeyDirectoryVlr, GeoKeyEntryStruct
    vlr = GeoKeyDirectoryVlr()
    key = GeoKeyEntryStruct()
    key.id = 3072
    key.tiff_tag_location = 0
    key.count = 1
    key.value_offset = 32633
    vlr.geo_keys = [key]
    vlr.geo_keys_header.number_of_keys = 1
    return vlr


def write_odm_like_glb(path, texture_px=1024, rtc=(500020.0, 4499980.0, 0.0), with_rtc=True):
    """A small textured mesh the way ODM ships it: float32 positions, a big JPEG, CESIUM_RTC."""
    from recon3d import gltf as g
    from recon3d import texture as t
    n = 9
    yy, xx = np.mgrid[0:n, 0:n]
    x = (xx / (n - 1) - 0.5) * SIZE_M
    y = (0.5 - yy / (n - 1)) * SIZE_M
    z = surface_height(x + SIZE_M / 2, SIZE_M / 2 - y) - GROUND
    pos = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1).astype("float32")
    uv = np.stack([xx.ravel() / (n - 1), yy.ravel() / (n - 1)], axis=1).astype("float32")
    quads = np.array([[r * n + c, (r + 1) * n + c, r * n + c + 1, (r + 1) * n + c, (r + 1) * n + c + 1, r * n + c + 1]
                      for r in range(n - 1) for c in range(n - 1)]).reshape(-1, 3)
    rgb = np.random.default_rng(0).integers(0, 255, (3, texture_px, texture_px), dtype="uint8")
    data, mime = t.encode(rgb, "jpeg", 90)
    b = g.GlbBuilder(generator="fake-odm")
    tex = b.add_texture(data, mime)
    mat = b.add_unlit_material(tex)
    b.add_mesh(pos, quads, mat, uv, quantize=False, name="odm_mesh")
    glb = b.to_glb(rtc_center=rtc if with_rtc else None)
    with open(path, "wb") as f:
        f.write(glb)
    return str(path)


@pytest.fixture
def dsm(tmp_path):
    return write_dsm(tmp_path / "dsm.tif")


@pytest.fixture
def dtm(tmp_path):
    return write_dtm(tmp_path / "dtm.tif")


@pytest.fixture
def orthophoto(tmp_path):
    return write_orthophoto(tmp_path / "orthophoto.tif")


@pytest.fixture
def las(tmp_path):
    pytest.importorskip("laspy")
    return write_las(tmp_path / "point_cloud.las")


@pytest.fixture
def odm_glb(tmp_path):
    return write_odm_like_glb(tmp_path / "model.glb")


def make_request(tmp_path, inputs, params=None, context=None):
    req = {
        "inputs": inputs,
        "params": params or {},
        "output_path": str(tmp_path / "output.glb"),
        "result_path": str(tmp_path / "result.json"),
        "work_dir": str(tmp_path / "work"),
    }
    if context is not None:
        req["context"] = context
    os.makedirs(req["work_dir"], exist_ok=True)
    return req


def read_glb(path):
    from recon3d import gltf as g
    with open(path, "rb") as f:
        return g.parse_glb(f.read())


def dump_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)
