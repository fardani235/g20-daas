"""Synthetic fixtures: a small hill DSM, a flat DTM, an orthophoto, a LAZ cloud and an ODM-like GLB.

Everything is generated in EPSG:32632 around (663500, 5328100) so the CRS,
origin and bounds logic is exercised with realistic magnitudes.
"""

import json
import os
import sys
import zipfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from recon import gltf  # noqa: E402

EPSG = 32632
X0, Y0 = 663500.0, 5328200.0  # upper-left corner
RES = 1.0
SIZE = 64  # cells per side -> 64 m x 64 m


def _hill(size=SIZE, base=500.0, height=20.0):
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2
    r2 = ((x - cx) ** 2 + (y - cy) ** 2) / (size / 3) ** 2
    return (base + height * np.exp(-r2)).astype(np.float32)


def write_raster(path, data, *, res=RES, x0=X0, y0=Y0, nodata=None, count=None, dtype=None, epsg=EPSG):
    if data.ndim == 2:
        data = data[None]
    count = count or data.shape[0]
    with rasterio.open(
        path, "w", driver="GTiff", height=data.shape[1], width=data.shape[2], count=count,
        dtype=dtype or data.dtype, crs=f"EPSG:{epsg}", transform=from_origin(x0, y0, res, res), nodata=nodata,
    ) as ds:
        ds.write(data[:count])
    return str(path)


@pytest.fixture
def dsm(tmp_path):
    z = _hill()
    z[5:12, 5:12] = -9999  # a small enclosed hole
    z[:, :3] = -9999       # a strip outside the survey
    return write_raster(tmp_path / "dsm.tif", z, nodata=-9999)


@pytest.fixture
def dtm(tmp_path):
    return write_raster(tmp_path / "dtm.tif", np.full((SIZE, SIZE), 500.0, np.float32), nodata=-9999)


@pytest.fixture
def orthophoto(tmp_path):
    y, x = np.mgrid[0:SIZE * 4, 0:SIZE * 4]
    r = ((x // 8 + y // 8) % 2 * 200 + 30).astype(np.uint8)  # checkerboard
    g = np.full_like(r, 120)
    b = (x * 255 // (SIZE * 4)).astype(np.uint8)
    a = np.full_like(r, 255)
    a[:, : SIZE] = 0  # left quarter transparent
    return write_raster(tmp_path / "ortho.tif", np.stack([r, g, b, a]), res=RES / 4)


@pytest.fixture
def point_cloud(tmp_path):
    import laspy

    rng = np.random.default_rng(1)
    n = 40_000
    x = rng.uniform(X0, X0 + SIZE * RES, n)
    y = rng.uniform(Y0 - SIZE * RES, Y0, n)
    z = 500.0 + 20.0 * np.exp(-(((x - (X0 + 32)) ** 2 + (y - (Y0 - 32)) ** 2) / (SIZE / 3) ** 2))
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.offsets = [X0, Y0 - SIZE, 0]
    header.scales = [0.001, 0.001, 0.001]
    # GeoTIFF key directory with ProjectedCSTypeGeoKey (3072) = EPSG, like ODM's output.
    from laspy.vlrs.known import GeoKeyDirectoryVlr, GeoKeyEntryStruct
    vlr = GeoKeyDirectoryVlr()
    key = GeoKeyEntryStruct()
    key.id, key.tiff_tag_location, key.count, key.value_offset = 3072, 0, 1, EPSG
    vlr.geo_keys = [key]
    vlr.geo_keys_header.number_of_keys = 1
    header.vlrs.append(vlr)
    las = laspy.LasData(header)
    las.x, las.y, las.z = x, y, z
    las.red = (x - X0) / SIZE * 255
    las.green = np.full(n, 90)
    las.blue = np.full(n, 30)
    las.classification = np.where(z > 505, 6, 2)
    path = tmp_path / "cloud.laz"
    las.write(str(path))
    return str(path)


def make_textured_glb(path, *, draco=False, rtc=(663532.0, 5328168.0, 500.0), absolute=False):
    """A two-triangle textured quad in ODM's conventions (Z-up, local coordinates, unlit)."""
    from PIL import Image
    import io

    b = gltf.GlbBuilder(generator="test")
    img = Image.new("RGB", (512, 512), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    tex = b.add_texture(b.add_image(buf.getvalue(), "image/jpeg"))
    mat = b.add_material(tex)
    pos = np.array([[-10, -10, 0], [10, -10, 0], [10, 10, 2], [-10, 10, 2]], np.float32)
    if absolute:
        pos = pos + np.array(rtc, np.float32)
    uv = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float32)
    idx = np.array([[0, 1, 2], [0, 2, 3]], np.uint32)
    prim = b.add_primitive(pos, indices=idx, uvs=uv, material=mat, draco={"bits": 12, "level": 5} if draco else None)
    b.add_node(b.add_mesh([prim]))
    if rtc is not None and not absolute:
        b.gltf["extensions"] = {"CESIUM_RTC": {"center": list(rtc)}}
        b._extensions_used.add("CESIUM_RTC")
    b.write(str(path))
    return str(path)


@pytest.fixture
def odm_glb(tmp_path):
    return make_textured_glb(tmp_path / "model.glb", draco=True)


@pytest.fixture
def obj_zip(tmp_path):
    """ODM's odm_texturing fallback: OBJ + MTL + texture in a zip."""
    from PIL import Image

    tex = tmp_path / "odm_textured_model_geo_material0000_map_Kd.png"
    Image.new("RGB", (64, 64), (10, 200, 10)).save(tex)
    obj = "\n".join([
        "mtllib odm_textured_model_geo.mtl",
        "v -5 -5 0", "v 5 -5 0", "v 5 5 1", "v -5 5 1",
        "vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1",
        "usemtl material0000",
        "f 1/1 2/2 3/3", "f 1/1 3/3 4/4",
    ])
    mtl = "newmtl material0000\nmap_Kd odm_textured_model_geo_material0000_map_Kd.png\n"
    path = tmp_path / "model.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("odm_texturing/odm_textured_model_geo.obj", obj)
        zf.writestr("odm_texturing/odm_textured_model_geo.mtl", mtl)
        zf.write(tex, "odm_texturing/odm_textured_model_geo_material0000_map_Kd.png")
    return str(path)


def make_request(tmp_path, inputs: dict, params: dict | None = None, context: dict | None = None) -> dict:
    return {
        "inputs": inputs,
        "params": params or {},
        "context": context or {"task": {"name": "t1", "epsg": EPSG, "processing_options": []}},
        "output_path": str(tmp_path / "out.glb"),
        "result_path": str(tmp_path / "result.json"),
        "progress_path": str(tmp_path / "progress.json"),
        "work_dir": str(tmp_path),
    }


def write_request(tmp_path, request: dict) -> str:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    return str(path)
