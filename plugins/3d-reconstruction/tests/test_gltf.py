import json
import os
import shutil
import subprocess

import numpy as np
import pytest

from recon import gltf

DRACO_JS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "frappe-bench", "apps", "webodm_frontend", "frontend", "public", "draco"))


def _quad(draco=None, colors=False):
    b = gltf.GlbBuilder()
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    uv = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float32)
    idx = np.array([[0, 1, 2], [0, 2, 3]], np.uint32)
    kwargs = {"colors": np.array([[255, 0, 0]] * 4, np.uint8)} if colors else {"uvs": uv}
    prim = b.add_primitive(pos, indices=idx, draco=draco, **kwargs)
    b.add_node(b.add_mesh([prim]))
    return b, pos, uv, idx


def test_glb_roundtrip_uncompressed():
    b, pos, uv, idx = _quad()
    b.set_georef({"epsg": 32632, "origin": [1.0, 2.0, 3.0], "bounds": [0, 0, 1, 1]})
    data = b.build()
    assert data[:4] == b"glTF" and len(data) % 4 == 0
    doc = gltf.GltfDocument.from_glb_bytes(data)
    prim = doc.gltf["meshes"][0]["primitives"][0]
    geo = doc.primitive_geometry(prim)
    np.testing.assert_allclose(geo["positions"], pos)
    np.testing.assert_allclose(geo["uvs"], uv)
    assert geo["indices"].tolist() == idx.tolist()
    assert doc.rtc_center() == [1.0, 2.0, 3.0]
    assert doc.georef()["epsg"] == 32632 and doc.georef()["up_axis"] == "Z"
    assert "CESIUM_RTC" in doc.gltf["extensionsUsed"]
    assert "extensionsRequired" not in doc.gltf
    assert gltf.triangle_count(doc.gltf) == 2


def test_glb_roundtrip_draco_uses_uint16_uvs():
    b, pos, uv, idx = _quad(draco={"bits": 12, "level": 5})
    data = b.build()
    doc = gltf.GltfDocument.from_glb_bytes(data)
    prim = doc.gltf["meshes"][0]["primitives"][0]
    ext = prim["extensions"]["KHR_draco_mesh_compression"]
    assert set(ext["attributes"]) == {"POSITION", "TEXCOORD_0"}
    uv_acc = doc.gltf["accessors"][prim["attributes"]["TEXCOORD_0"]]
    assert uv_acc["componentType"] == gltf.UNSIGNED_SHORT and uv_acc["normalized"] is True
    assert "bufferView" not in uv_acc
    assert doc.gltf["extensionsRequired"] == ["KHR_draco_mesh_compression"]
    geo = doc.primitive_geometry(prim)
    # Draco reorders vertices; compare as sets of (position, uv) rows.
    got = sorted(map(tuple, np.round(np.hstack([geo["positions"], geo["uvs"]]), 3).tolist()))
    want = sorted(map(tuple, np.round(np.hstack([pos, uv]), 3).tolist()))
    assert got == want
    assert geo["indices"].shape == (2, 3)


def test_draco_point_cloud_with_colors():
    b = gltf.GlbBuilder()
    pos = np.random.default_rng(0).random((50, 3)).astype(np.float32)
    col = np.random.default_rng(1).integers(0, 255, (50, 3)).astype(np.uint8)
    prim = b.add_primitive(pos, colors=col, mode=gltf.MODE_POINTS)
    b.add_node(b.add_mesh([prim]))
    doc = gltf.GltfDocument.from_glb_bytes(b.build())
    geo = doc.primitive_geometry(doc.gltf["meshes"][0]["primitives"][0])
    assert geo["mode"] == gltf.MODE_POINTS
    np.testing.assert_allclose(geo["positions"], pos)
    assert geo["colors"].tolist() == col.tolist()
    assert gltf.point_count(doc.gltf) == 50


def test_draco_attribute_ids_are_read_back_from_blob():
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], np.float32)
    uv = np.array([[0, 0], [1, 0], [1, 1]], np.float32)
    blob, ids = gltf.encode_draco(pos, np.array([[0, 1, 2]], np.uint32), uvs=uv)
    assert set(ids) == {"POSITION", "TEXCOORD_0"}
    assert len(set(ids.values())) == 2
    blob2, ids2 = gltf.encode_draco(pos, np.array([[0, 1, 2]], np.uint32), colors=np.zeros((3, 3), np.uint8))
    assert set(ids2) == {"POSITION", "COLOR_0"}


@pytest.mark.skipif(not shutil.which("node") or not os.path.isfile(os.path.join(DRACO_JS, "draco_decoder.js")),
                    reason="node or the viewer's Draco decoder not available")
def test_draco_ids_match_the_viewers_decoder(tmp_path):
    """The unique ids we write must be what three.js's DRACOLoader will look up."""
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    uv = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float32)
    blob, ids = gltf.encode_draco(pos, np.array([[0, 1, 2], [0, 2, 3]], np.uint32), uvs=uv)
    drc = tmp_path / "t.drc"
    drc.write_bytes(blob)
    script = tmp_path / "check.cjs"
    script.write_text(r"""
const fs = require('fs'); const vm = require('vm');
const [dir, file] = process.argv.slice(2);
const src = fs.readFileSync(dir + '/draco_decoder.js', 'utf8');
const ctx = { console, __dirname: dir, __filename: dir + '/draco_decoder.js', require, process, Buffer, TextDecoder, WebAssembly, setTimeout, clearTimeout };
vm.createContext(ctx);
vm.runInContext(src + '\nthis.DracoDecoderModule = DracoDecoderModule;', ctx);
(async () => {
  const draco = await ctx.DracoDecoderModule();
  const buf = fs.readFileSync(file);
  const dec = new draco.Decoder(); const ba = new draco.DecoderBuffer(); ba.Init(new Int8Array(buf), buf.length);
  const mesh = new draco.Mesh(); const st = dec.DecodeBufferToMesh(ba, mesh);
  const out = { ok: st.ok(), attrs: {} };
  for (let i = 0; i < mesh.num_attributes(); i++) { const a = dec.GetAttribute(mesh, i); out.attrs[a.unique_id()] = { type: a.attribute_type(), comps: a.num_components() }; }
  console.log(JSON.stringify(out));
})();
""")
    res = subprocess.run(["node", str(script), DRACO_JS, str(drc)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["ok"]
    assert out["attrs"][str(ids["POSITION"])] == {"type": 0, "comps": 3}   # POSITION
    assert out["attrs"][str(ids["TEXCOORD_0"])]["comps"] == 2


def test_gltf_zip_and_obj_zip_open(tmp_path, obj_zip):
    with pytest.raises(ValueError):
        gltf.GltfDocument.open(obj_zip)  # OBJ archives are handled by objloader, not the glTF reader
    from recon.objloader import load_obj_zip
    loaded = load_obj_zip(obj_zip)
    prim = loaded["primitives"][0]
    assert prim["positions"].shape == (4, 3) and prim["indices"].shape == (2, 3)
    assert prim["uvs"] is not None and prim["image"] is not None and prim["mime"] == "image/png"
    assert prim["uvs"][:, 1].max() <= 1.0
