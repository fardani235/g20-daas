"""End-to-end runs through ``recon3d.workflow`` and ``main.py`` on the synthetic scene."""

import json
import os
import subprocess
import sys
import zipfile

import numpy as np
import pytest

from recon3d import glb_optimize
from recon3d import gltf as g
from recon3d.errors import InputError
from recon3d.progress import Progress
from recon3d.workflow import run
from tests.conftest import (
    BUILDING,
    BUILDING_H,
    GROUND,
    ORIGIN,
    PLUGIN_DIR,
    SIZE_M,
    make_request,
    read_glb,
    write_dsm,
    write_odm_like_glb,
)


def _run(req):
    return run(req, Progress(stream=open(os.devnull, "w")))


def _positions(root, binary):
    """All vertex positions of a GLB in the model frame (dequantized, node transforms applied)."""
    out = []
    for node in root["nodes"]:
        if "mesh" not in node:
            continue
        for prim in root["meshes"][node["mesh"]]["primitives"]:
            acc = root["accessors"][prim["attributes"]["POSITION"]]
            view = root["bufferViews"][acc["bufferView"]]
            dtype = {g.FLOAT: "<f4", g.UNSIGNED_SHORT: "<u2"}[acc["componentType"]]
            stride = view.get("byteStride") or (np.dtype(dtype).itemsize * 3)
            raw = np.frombuffer(binary, dtype="uint8", offset=view["byteOffset"] + acc.get("byteOffset", 0),
                                count=stride * (acc["count"] - 1) + np.dtype(dtype).itemsize * 3)
            arr = np.lib.stride_tricks.as_strided(raw, (acc["count"], np.dtype(dtype).itemsize * 3), (stride, 1))
            pos = np.ascontiguousarray(arr).view(dtype).reshape(-1, 3).astype("float64")
            pos = pos * np.array(node.get("scale", [1, 1, 1])) + np.array(node.get("translation", [0, 0, 0]))
            out.append(pos)
    return np.concatenate(out)


def test_dsm_and_orthophoto_build_a_georeferenced_terrain(tmp_path, dsm, dtm, orthophoto):
    req = make_request(tmp_path, {"dsm": dsm, "dtm": dtm, "orthophoto": orthophoto},
                       {"quality": "web-light"},
                       context={"task": {"name": "T1", "epsg": 32633,
                                         "processing_options": [{"name": "dsm", "value": True},
                                                                {"name": "mesh-size", "value": 200000}]}})
    meta = _run(req)
    assert meta["workflow"] == "terrain"
    assert meta["surface_source"] == "dsm" and meta["texture_source"] == "orthophoto"
    assert meta["epsg"] == 32633
    assert meta["triangles"] <= 150_000 and meta["triangles"] > 10
    assert meta["odm_options"] == {"dsm": True, "mesh-size": 200000}
    assert meta["fills"].get("interpolated") or meta["fills"].get("dtm")
    root, binary = read_glb(req["output_path"])
    georef = root["extras"]["webodm_georef"]
    assert georef["epsg"] == 32633 and georef["up_axis"] == "Z"
    w, s, e, n = georef["bounds"]
    # clipped to the orthophoto (2 m west strip has no data): footprint starts >= 2 m in
    assert w >= ORIGIN[0] + 2.0 - 1e-6 and e <= ORIGIN[0] + SIZE_M + 1e-6
    assert s >= ORIGIN[1] - SIZE_M - 1e-6 and n <= ORIGIN[1] + 1e-6
    lon_w, lat_s, lon_e, lat_n = georef["bounds_4326"]
    assert 14 < lon_w < lon_e < 16 and 40 < lat_s < lat_n < 41
    assert root["extensions"]["CESIUM_RTC"]["center"] == georef["origin"]
    assert root["images"][0]["mimeType"] == "image/jpeg"
    assert "KHR_materials_unlit" in root["extensionsUsed"]
    # geometry: the building roof is at GROUND + 8 in absolute heights (origin z = 0)
    pos = _positions(root, binary) + np.array(georef["origin"])
    roof = pos[(pos[:, 0] > ORIGIN[0] + BUILDING[0] + 1) & (pos[:, 0] < ORIGIN[0] + BUILDING[2] - 1)
               & (pos[:, 1] < ORIGIN[1] - BUILDING[1] - 1) & (pos[:, 1] > ORIGIN[1] - BUILDING[3] + 1)]
    assert len(roof) and np.allclose(roof[:, 2], GROUND + BUILDING_H, atol=0.6)
    ground = pos[(pos[:, 0] > ORIGIN[0] + 3) & (pos[:, 0] < ORIGIN[0] + 8) & (pos[:, 1] < ORIGIN[1] - 3)
                 & (pos[:, 1] > ORIGIN[1] - 8)]
    assert len(ground) and np.allclose(ground[:, 2], GROUND, atol=0.3)
    assert os.path.getsize(req["output_path"]) == meta["output_bytes"]


def test_dtm_only_gets_a_hillshade_texture_and_dsm_only_fills_from_nothing(tmp_path, dtm):
    req = make_request(tmp_path, {"dtm": dtm}, {"texture_size": 256, "quality": "web-light"})
    meta = _run(req)
    assert meta["surface_source"] == "dtm" and meta["texture_source"] == "hillshade"
    assert meta["tiles"] == 1 and meta["texture_size"] == 256
    root, _ = read_glb(req["output_path"])
    assert len(root["images"]) == 1


def test_dsm_without_dtm_interpolates_interior_hole_but_keeps_outline(tmp_path):
    dsm = write_dsm(tmp_path / "dsm.tif")
    req = make_request(tmp_path, {"dsm": dsm}, {"quality": "web-light", "texture_size": 256})
    meta = _run(req)
    assert meta["fills"] == {"interpolated": meta["fills"]["interpolated"]}
    assert meta["bounds"][0] >= ORIGIN[0] + 0.8 - 1e-6   # 1 m (2 x 0.4 m px) west strip stays no-data
    req2 = make_request(tmp_path, {"dsm": dsm}, {"quality": "web-light", "texture_size": 256, "fill_holes": False})
    meta2 = _run(req2)
    assert meta2["fills"] == {}


def test_point_cloud_only(tmp_path, las):
    req = make_request(tmp_path, {"point_cloud": las}, {"quality": "web-light", "texture_size": 512})
    meta = _run(req)
    assert meta["surface_source"] == "point_cloud" and meta["texture_source"] == "point_cloud"
    assert meta["epsg"] == 32633
    assert abs(meta["height_range_m"][0] - GROUND) < 0.5 and abs(meta["height_range_m"][1] - (GROUND + 8)) < 0.5
    root, _binary = read_glb(req["output_path"])
    assert root["images"] and root["extras"]["webodm_georef"]["bounds_4326"]


def test_point_cloud_surface_with_orthophoto_texture_and_explicit_choices(tmp_path, las, orthophoto, dsm):
    req = make_request(tmp_path, {"point_cloud": las, "orthophoto": orthophoto, "dsm": dsm},
                       {"surface_source": "point_cloud", "texture_source": "orthophoto", "point_cloud_stat": "mean",
                        "quality": "web-light", "texture_size": 256, "texture_tiles": 2, "quantize": False,
                        "mesh_method": "grid", "resolution_m": 1.0})
    meta = _run(req)
    assert meta["surface_source"] == "point_cloud" and meta["texture_source"] == "orthophoto"
    assert meta["tiles"] == 4 and meta["mesh_method"] == "grid" and meta["quantized"] is False
    root, _ = read_glb(req["output_path"])
    assert "extensionsRequired" not in root
    assert len(root["images"]) == 4


def test_existing_model_is_optimized_by_default(tmp_path, odm_glb, dsm):
    before = os.path.getsize(odm_glb)
    req = make_request(tmp_path, {"model": odm_glb, "dsm": dsm}, {"texture_size": 256})
    meta = _run(req)
    assert meta["workflow"] == "optimize-model"
    assert meta["textures_resized"] == 1 and meta["texture_max_side_after"] == 256
    assert meta["meshes_quantized"] == 1 and meta["triangles"] == 128
    assert meta["origin"] == [500020.0, 4499980.0, 0.0] and meta["epsg"] == 32633
    assert meta["bounds"] == pytest.approx([500000.0, 4499960.0, 500040.0, 4500000.0])
    assert meta["bounds_4326"] and os.path.getsize(req["output_path"]) < before
    root, binary = read_glb(req["output_path"])
    assert root["extensions"]["CESIUM_RTC"]["center"] == [500020.0, 4499980.0, 0.0]
    assert "KHR_mesh_quantization" in root["extensionsRequired"]
    pos = _positions(root, binary)
    assert pos[:, 0].min() == pytest.approx(-20, abs=0.01) and pos[:, 2].max() == pytest.approx(8, abs=0.01)
    assert root["asset"]["generator"].startswith("fake-odm + ")


def test_forced_terrain_with_model_present_and_forced_optimize_without_model(tmp_path, odm_glb, dsm):
    req = make_request(tmp_path, {"model": odm_glb, "dsm": dsm}, {"workflow": "terrain", "quality": "web-light",
                                                                    "texture_size": 256})
    assert _run(req)["workflow"] == "terrain"
    with pytest.raises(InputError, match="optimize-model"):
        _run(make_request(tmp_path, {"dsm": dsm}, {"workflow": "optimize-model"}))
    with pytest.raises(InputError, match="no usable input"):
        _run(make_request(tmp_path, {}))
    with pytest.raises(InputError, match="orthophoto"):
        _run(make_request(tmp_path, {"dsm": dsm}, {"texture_source": "orthophoto"}))


def test_gltf_zip_model_without_rtc(tmp_path):
    glb = write_odm_like_glb(tmp_path / "m.glb", texture_px=300, with_rtc=False)
    root, binary = read_glb(glb)
    # explode into .gltf + .bin + external image inside a zip (what WebODM stores for non-binary ODM output)
    img_view = root["bufferViews"][root["images"][0]["bufferView"]]
    img_bytes = binary[img_view["byteOffset"]:img_view["byteOffset"] + img_view["byteLength"]]
    root["images"][0] = {"uri": "textures/tex_0.jpg"}
    root["buffers"][0]["uri"] = "scene.bin"
    zpath = tmp_path / "model.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("odm_texturing/scene.gltf", json.dumps(root))
        zf.writestr("odm_texturing/scene.bin", binary)
        zf.writestr("odm_texturing/textures/tex_0.jpg", img_bytes)
    req = make_request(tmp_path, {"model": str(zpath)}, {"texture_size": 256})
    meta = _run(req)
    assert meta["model_format"] == "gltf-zip" and meta["origin"] is None and meta["epsg"] is None
    out_root, _ = read_glb(req["output_path"])
    assert out_root["extras"]["webodm_georef"]["origin_known"] is False
    assert out_root["images"][0]["mimeType"] == "image/jpeg" and "bufferView" in out_root["images"][0]


def test_main_entrypoint_as_the_sandbox_runs_it(tmp_path, dsm, orthophoto):
    req = make_request(tmp_path, {"dsm": dsm, "orthophoto": orthophoto}, {"quality": "web-light", "texture_size": 256})
    req_path = tmp_path / "request.json"
    req_path.write_text(json.dumps(req))
    proc = subprocess.run([sys.executable, "-E", "-s", "-B", os.path.join(PLUGIN_DIR, "main.py"), str(req_path)],
                          cwd=PLUGIN_DIR, capture_output=True, text=True, timeout=300, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "[3d-reconstruction" in proc.stderr and "finished" in proc.stderr
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["metadata"]["workflow"] == "terrain" and result["metadata"]["stages"]
    assert os.path.getsize(req["output_path"]) > 1000
    # bad parameter -> status 1 with one clear line
    bad = dict(req, params={"quality": "ultra"})
    req_path.write_text(json.dumps(bad))
    proc = subprocess.run([sys.executable, os.path.join(PLUGIN_DIR, "main.py"), str(req_path)],
                          cwd=PLUGIN_DIR, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 1 and "quality must be one of" in proc.stderr


def test_optimizer_keeps_unrelated_buffer_views_and_handles_shared_accessors(tmp_path):
    glb = write_odm_like_glb(tmp_path / "m.glb", texture_px=64)
    root, binary = read_glb(glb)
    # second mesh sharing the POSITION accessor -> not quantized, but everything still valid
    root["meshes"].append({"primitives": [dict(root["meshes"][0]["primitives"][0])]})
    root["nodes"].append({"mesh": 1})
    root["scenes"][0]["nodes"].append(1)
    src = tmp_path / "shared.glb"
    src.write_bytes(g.pack_glb(root, binary))
    from recon3d.sources import describe_model
    doc = glb_optimize.load(describe_model("model", str(src)))
    out, stats = glb_optimize.optimize(doc, texture_cap=4096, quality=80, quantize=True)
    assert stats["meshes_quantized"] == 0 and stats["textures_resized"] == 0
    out_root, _ = g.parse_glb(out)
    assert len(out_root["bufferViews"]) == len(root["bufferViews"])
