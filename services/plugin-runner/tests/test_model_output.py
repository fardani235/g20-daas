"""The ``model`` output kind (GLB), the run context and the progress file."""

import json
import os
import shutil

import pytest

from app import sandbox
from tests.conftest import REPO_ROOT, make_package

RECON_PLUGIN_DIR = os.path.join(REPO_ROOT, "plugins", "3d-reconstruction")

# Writes a minimal GLB whose extras carry the georef block, echoes context,
# and publishes progress twice.
GLB_PLUGIN = r"""
import json, struct, sys
req = json.load(open(sys.argv[1]))
def report(p, m):
    json.dump({"percent": p, "message": m}, open(req["progress_path"], "w"))
report(10, "starting")
gltf = {
  "asset": {"version": "2.0", "extras": {"webodm_georef": {"epsg": 32633, "origin": [500010.0, 4499990.0, 100.0],
                                                             "bounds": [500000, 4499968, 500032, 4500000]}}},
  "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
  "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
  "accessors": [{"componentType": 5126, "count": 3, "type": "VEC3", "bufferView": 0},
                {"componentType": 5123, "count": 3, "type": "SCALAR", "bufferView": 1}],
  "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 6}],
  "buffers": [{"byteLength": 44}],
}
if req["params"].get("no_georef"):
    del gltf["asset"]["extras"]
js = json.dumps(gltf).encode()
js += b" " * (-len(js) % 4)
bin_ = struct.pack("<9f", 0,0,0, 1,0,0, 0,1,0) + struct.pack("<3H", 0,1,2) + b"\0\0"
body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bin_), 0x004E4942) + bin_
open(req["output_path"], "wb").write(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
json.dump({"metadata": {"context_epsg": req.get("context", {}).get("task", {}).get("epsg")}}, open(req["result_path"], "w"))
report(100, "done")
"""

MODEL_MANIFEST = {
    "id": "glb-plugin", "label": "GLB", "version": "1.0.0", "entrypoint": "main.py", "output_kind": "model",
    "inputs": [{"name": "raster", "datasets": ["dsm"]}],
}


def _run(pkg, dem, run_dir, params=None, context=None):
    out = os.path.join(run_dir, "output.glb")
    return sandbox.run_package(pkg, {"raster": dem}, params or {}, out, run_dir, output_kind="model",
                               timeout_seconds=60, context=context)


def test_model_output_gets_georef_from_extras(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": GLB_PLUGIN}, MODEL_MANIFEST)
    result = _run(pkg, dem, run_dir, context={"task": {"epsg": 32633}})
    md = result["metadata"]
    assert md["epsg"] == 32633
    assert md["extent"]["type"] == "Polygon"
    assert len(md["bounds_4326"]) == 4 and 14 < md["bounds_4326"][0] < 16  # UTM 33N
    assert md["origin"] == [500010.0, 4499990.0, 100.0]
    assert md["triangles"] == 1 and md["points"] == 0 and md["images"] == 0
    assert md["context_epsg"] == 32633  # the plugin saw the context
    # The output and the plugin's last progress file are what remains for the caller.
    assert sorted(os.listdir(run_dir)) == ["output.glb", "progress.json"]
    assert json.load(open(sandbox.progress_path_for(run_dir))) == {"percent": 100, "message": "done"}


def test_model_without_georef_is_accepted_without_extent(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": GLB_PLUGIN}, MODEL_MANIFEST)
    md = _run(pkg, dem, run_dir, params={"no_georef": True})["metadata"]
    assert md["epsg"] is None and md["extent"] is None and md["origin"] is None
    assert md["triangles"] == 1


def test_model_output_must_be_a_glb(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": "import json,sys\nreq=json.load(open(sys.argv[1]))\n"
                                                       "open(req['output_path'],'wb').write(b'not a glb at all')\n"},
                       MODEL_MANIFEST)
    with pytest.raises(sandbox.PluginError, match="not a GLB"):
        _run(pkg, dem, run_dir)


def test_context_is_optional_and_defaults_to_empty(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": GLB_PLUGIN}, MODEL_MANIFEST)
    assert _run(pkg, dem, run_dir)["metadata"]["context_epsg"] is None


@pytest.mark.skipif(not os.path.isdir(RECON_PLUGIN_DIR), reason="3d-reconstruction plugin not present")
def test_reconstruction_plugin_runs_in_the_sandbox(tmp_path, dem, run_dir):
    """The real plugin, through the real sandbox: DSM only -> shaded-relief terrain GLB."""
    pytest.importorskip("fast_simplification")
    pytest.importorskip("DracoPy")
    pkg = shutil.make_archive(str(tmp_path / "recon"), "zip", root_dir=RECON_PLUGIN_DIR)
    out = os.path.join(run_dir, "output.glb")
    result = sandbox.run_package(
        pkg, {"dsm": dem}, {"quality": "web-lite", "max_triangles": 1000, "texture_size": 256}, out, run_dir,
        output_kind="model", timeout_seconds=300, context={"task": {"epsg": 32633, "processing_options": []}},
    )
    md = result["metadata"]
    assert md["workflow"] == "terrain"
    assert md["epsg"] == 32633 and md["extent"]["type"] == "Polygon"
    assert md["triangles"] > 0 and md["images"] == 1
    assert md["extensions_required"] == ["KHR_draco_mesh_compression"]
    with open(out, "rb") as f:
        assert f.read(4) == b"glTF"
