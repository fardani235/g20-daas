
import numpy as np
import pytest

from recon3d import gltf as g
from recon3d import texture as t


def _tri():
    pos = np.array([[0, 0, 0], [10, 0, 1], [0, 10, 2], [10, 10, 3]], dtype="float64")
    idx = np.array([[0, 1, 2], [1, 3, 2]], dtype="uint32")
    uv = np.array([[0, 1], [1, 1], [0, 0], [1, 0]], dtype="float32")
    return pos, idx, uv


def test_glb_roundtrip_and_quantization():
    pos, idx, uv = _tri()
    b = g.GlbBuilder()
    tex = b.add_texture(b"\xff\xd8fake", "image/jpeg")
    mat = b.add_unlit_material(tex)
    node = b.add_mesh(pos, idx, mat, uv, quantize=True, name="tile")
    glb = b.to_glb(extras={"webodm_georef": {"epsg": 32633}}, rtc_center=(1.0, 2.0, 3.0))
    root, binary = g.parse_glb(glb)
    assert root["asset"]["version"] == "2.0"
    assert root["extensions"]["CESIUM_RTC"]["center"] == [1.0, 2.0, 3.0]
    assert "KHR_mesh_quantization" in root["extensionsRequired"]
    assert root["materials"][0]["extensions"] == {"KHR_materials_unlit": {}}
    assert root["extras"]["webodm_georef"]["epsg"] == 32633
    assert len(glb) % 4 == 0 and len(binary) % 4 == 0
    # dequantize and compare
    prim = root["meshes"][0]["primitives"][0]
    acc = root["accessors"][prim["attributes"]["POSITION"]]
    assert acc["componentType"] == g.UNSIGNED_SHORT and acc["type"] == "VEC3"
    view = root["bufferViews"][acc["bufferView"]]
    raw = np.frombuffer(binary, dtype="uint8", offset=view["byteOffset"], count=view["byteLength"])
    q = raw.reshape(acc["count"], view["byteStride"])[:, :6].copy().view("<u2")
    n = root["nodes"][node]
    world = q * np.array(n["scale"]) + np.array(n["translation"])
    assert np.allclose(world, pos, atol=10 / 65535 + 1e-9)
    # indices are 16-bit for small meshes, uvs normalized ushort
    assert root["accessors"][prim["indices"]]["componentType"] == g.UNSIGNED_SHORT
    uv_acc = root["accessors"][prim["attributes"]["TEXCOORD_0"]]
    assert uv_acc["normalized"] is True
    assert view["byteStride"] == 8  # 3 x uint16 padded to 4-byte multiple


def test_float_positions_and_uint32_indices():
    pos = np.random.default_rng(0).random((70000, 3))
    idx = np.arange(69999).repeat(3).reshape(-1, 3) % 70000
    b = g.GlbBuilder()
    mat = b.add_unlit_material(None)
    b.add_mesh(pos, idx, mat, None, quantize=False)
    root, _ = g.parse_glb(b.to_glb())
    prim = root["meshes"][0]["primitives"][0]
    assert root["accessors"][prim["attributes"]["POSITION"]]["componentType"] == g.FLOAT
    assert root["accessors"][prim["indices"]]["componentType"] == g.UNSIGNED_INT
    assert "extensionsRequired" not in root
    assert "TEXCOORD_0" not in prim["attributes"]


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        g.parse_glb(b"not a glb at all")


def test_texture_encode_decode():
    rgb = np.zeros((3, 64, 64), dtype="uint8")
    rgb[0] = 200
    data, mime = t.encode(rgb, "jpeg", 90)
    assert mime == "image/jpeg" and data[:2] == b"\xff\xd8"
    arr, w, h = t.decode(data)
    assert (w, h) == (64, 64) and arr.shape == (3, 64, 64)
    assert abs(int(arr[0].mean()) - 200) < 6
    small, _, _ = t.decode(data, max_side=16)
    assert small.shape == (3, 16, 16)
    png, mime = t.encode(np.concatenate([rgb, np.full((1, 64, 64), 128, "uint8")]), "png")
    assert mime == "image/png" and t.decode(png)[0].shape[0] == 4


def test_hillshade_and_colour_tiles():
    z = np.tile(np.linspace(0, 10, 17, dtype="float32"), (17, 1))
    rgb = t.hillshade_tile(z, 1.0, 0, 0, 16, 32)
    assert rgb.shape == (3, 32, 32) and rgb.dtype == np.uint8
    z[3:6, 3:6] = np.nan
    assert t.hillshade_tile(z, 1.0, 0, 0, 16, 8).shape == (3, 8, 8)
    colours = np.zeros((3, 17, 17), dtype="uint8")
    colours[:, 8, 8] = (10, 20, 30)
    tile = t.grid_colour_tile(colours, 0, 0, 16, 16)
    assert tile.shape == (3, 16, 16) and tile.any()
