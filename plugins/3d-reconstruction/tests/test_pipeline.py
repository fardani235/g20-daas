import json
import os
import subprocess
import sys

import numpy as np
import pytest

from conftest import EPSG, PLUGIN_DIR, make_request, make_textured_glb, write_request
from recon import gltf, pipeline
from recon.errors import InputError, ParameterError
from recon.progress import Progress


def _run(tmp_path, inputs, params=None, context=None):
    request = make_request(tmp_path, inputs, params, context)
    metadata = pipeline.run(request, Progress(stream=open(os.devnull, "w"), progress_path=request["progress_path"]))
    doc = gltf.GltfDocument.open(request["output_path"])
    return metadata, doc, request


def _bounds_ok(doc, lo=(663500, 5328136), hi=(663564, 5328200)):
    georef = doc.georef()
    assert georef["epsg"] == EPSG
    minx, miny, maxx, maxy = georef["bounds"]
    assert lo[0] - 1 <= minx <= maxx <= hi[0] + 1 and lo[1] - 1 <= miny <= maxy <= hi[1] + 1
    # Positions are local to the RTC origin and small.
    center = doc.rtc_center()
    assert center == georef["origin"]
    prim = doc.gltf["meshes"][0]["primitives"][0]
    acc = doc.gltf["accessors"][prim["attributes"]["POSITION"]]
    assert max(abs(v) for v in acc["min"] + acc["max"]) < 200
    return georef


# ----------------------------------------------------------------- workflows

def test_terrain_from_dsm_and_orthophoto(tmp_path, dsm, orthophoto, dtm):
    metadata, doc, request = _run(tmp_path, {"dsm": dsm, "orthophoto": orthophoto, "dtm": dtm},
                                  {"quality": "web-lite"})
    assert metadata["workflow"] == "terrain"
    assert metadata["inputs_used"] == {"surface": "dsm", "hole_donor": "dtm", "orthophoto": "texture"}
    georef = _bounds_ok(doc)
    assert 500 <= georef["z_range"][0] < georef["z_range"][1] <= 521
    holes = metadata["surface"]["holes"]
    assert holes["from_donors"] > 0  # the enclosed 7x7 hole came from the DTM
    assert metadata["mesh"]["triangles"] > 100
    assert doc.gltf["extensionsRequired"] == ["KHR_draco_mesh_compression"]
    assert doc.gltf["materials"][0]["extensions"] == {"KHR_materials_unlit": {}}
    assert metadata["texture"]["coverage"]["orthophoto"] > 0.5
    assert metadata["texture"]["coverage"]["shaded-relief"] > 0.1  # transparent quarter of the ortho
    assert holes["left_open"] == 0  # the DTM covers everything, including the strip
    assert json.load(open(request["progress_path"]))["percent"] == 100
    assert metadata["progress"]["stages"]


def test_large_holes_stay_open_without_a_donor(tmp_path, dsm):
    metadata, doc, _ = _run(tmp_path, {"dsm": dsm}, {"quality": "web-lite"})
    holes = metadata["surface"]["holes"]
    assert holes["from_donors"] == 0
    assert holes["interpolated"] >= 49        # the enclosed 7x7 gap is bridged
    assert holes["left_open"] >= 3 * 60       # the strip outside the survey is not


def test_terrain_from_dtm_only_uses_shaded_relief(tmp_path, dtm):
    metadata, doc, _ = _run(tmp_path, {"dtm": dtm}, {"quality": "web-lite", "compression": "none"})
    assert metadata["inputs_used"]["surface"] == "dtm"
    assert metadata["texture"]["coverage"] == {"shaded-relief": 1.0}
    assert "extensionsRequired" not in doc.gltf
    prim = doc.gltf["meshes"][0]["primitives"][0]
    assert doc.gltf["accessors"][prim["attributes"]["TEXCOORD_0"]]["componentType"] == gltf.FLOAT


def test_point_cloud_surface_with_point_colours(tmp_path, point_cloud):
    metadata, doc, _ = _run(tmp_path, {"point_cloud": point_cloud}, {"quality": "web-lite", "point_statistic": "mean"})
    assert metadata["workflow"] == "point-cloud"
    assert metadata["point_cloud"]["points_used"] == 40_000
    assert metadata["point_cloud"]["classes_seen"] == [2, 6]
    assert metadata["texture"]["coverage"]["point-cloud"] > 0.9
    _bounds_ok(doc)


def test_point_cloud_class_filter_and_dtm_donor(tmp_path, point_cloud, dtm):
    metadata, doc, _ = _run(tmp_path, {"point_cloud": point_cloud, "dtm": dtm},
                            {"workflow": "point-cloud", "point_classes": "2", "quality": "web-lite"})
    assert metadata["point_cloud"]["points_used"] < 40_000
    assert metadata["inputs_used"]["hole_donor"] == "dtm"
    assert metadata["surface"]["holes"]["from_donors"] > 0


def test_points_workflow(tmp_path, point_cloud, orthophoto):
    metadata, doc, _ = _run(tmp_path, {"point_cloud": point_cloud, "orthophoto": orthophoto},
                            {"workflow": "points", "max_points": 5000, "texture_source": "orthophoto"})
    assert metadata["workflow"] == "points"
    assert 4000 <= metadata["mesh"]["points"] <= 5000
    assert metadata["point_cloud"]["colour"] == "orthophoto"
    prim = doc.gltf["meshes"][0]["primitives"][0]
    assert prim["mode"] == gltf.MODE_POINTS and "COLOR_0" in prim["attributes"]
    _bounds_ok(doc)


def test_mesh_workflow_optimises_existing_glb(tmp_path, odm_glb):
    metadata, doc, _ = _run(tmp_path, {"model": odm_glb}, {"texture_size": 256, "quality": "balanced"})
    assert metadata["workflow"] == "mesh"
    assert metadata["mesh"]["triangles"] == 2 and metadata["mesh"]["primitives"] == 1
    assert metadata["texture"]["images"][0]["before"] == [512, 512]
    assert metadata["texture"]["images"][0]["after"] == [256, 256]
    georef = doc.georef()
    assert georef["epsg"] == EPSG and georef["origin"] == [663532.0, 5328168.0, 500.0]
    assert georef["georeferenced"] is True
    assert doc.rtc_center() == [663532.0, 5328168.0, 500.0]


def test_mesh_workflow_recentres_absolute_coordinates(tmp_path):
    path = make_textured_glb(tmp_path / "abs.glb", absolute=True)
    metadata, doc, _ = _run(tmp_path, {"model": path})
    assert metadata["mesh"]["recentred"] is True
    prim = doc.gltf["meshes"][0]["primitives"][0]
    acc = doc.gltf["accessors"][prim["attributes"]["POSITION"]]
    assert max(abs(v) for v in acc["max"]) < 100
    assert abs(doc.rtc_center()[0] - 663532) < 20


def test_mesh_workflow_obj_zip(tmp_path, obj_zip):
    metadata, doc, _ = _run(tmp_path, {"model": obj_zip}, {"compression": "none"})
    assert metadata["mesh"]["triangles"] == 2
    assert doc.georef()["georeferenced"] is False
    assert doc.rtc_center() is None
    assert len(doc.gltf["images"]) == 1


def test_auto_prefers_model_then_dsm_then_points(tmp_path, dsm, point_cloud, odm_glb):
    assert pipeline.choose_workflow("auto", {"model": odm_glb, "dsm": dsm}) == "mesh"
    assert pipeline.choose_workflow("auto", {"dsm": dsm, "point_cloud": point_cloud}) == "terrain"
    assert pipeline.choose_workflow("auto", {"point_cloud": point_cloud}) == "point-cloud"
    with pytest.raises(InputError):
        pipeline.choose_workflow("auto", {"orthophoto": "x"})
    with pytest.raises(InputError):
        pipeline.choose_workflow("mesh", {"dsm": dsm})


def test_auto_quality_follows_odm_options(tmp_path, dtm):
    ctx = {"task": {"epsg": EPSG, "processing_options": [{"name": "pc-quality", "value": "ultra"}]}}
    metadata, _, _ = _run(tmp_path, {"dtm": dtm}, {"max_triangles": 2000, "texture_size": 256}, ctx)
    assert metadata["params"]["quality"] == "high-detail"
    ctx = {"task": {"epsg": EPSG, "processing_options": json.dumps(json.dumps([{"name": "fast-orthophoto", "value": True}]))}}
    metadata, _, _ = _run(tmp_path, {"dtm": dtm}, {"max_triangles": 2000, "texture_size": 256}, ctx)
    assert metadata["params"]["quality"] == "web-lite"


def test_missing_inputs_and_bad_params_are_user_errors(tmp_path, dsm):
    with pytest.raises(InputError):
        _run(tmp_path, {})
    with pytest.raises(ParameterError):
        _run(tmp_path, {"dsm": dsm}, {"quality": "ultra"})
    with pytest.raises(ParameterError):
        _run(tmp_path, {"dsm": dsm}, {"texture_size": 100})
    with pytest.raises(ParameterError):
        _run(tmp_path, {"dsm": dsm}, {"nope": 1})


def test_geographic_crs_is_rejected(tmp_path):
    from conftest import write_raster
    path = write_raster(tmp_path / "geo.tif", np.full((16, 16), 5.0, np.float32), res=0.0001, x0=11.0, y0=48.0, epsg=4326)
    with pytest.raises(InputError, match="projected"):
        _run(tmp_path, {"dsm": path})


# ------------------------------------------------------------------- entrypoint

def test_entrypoint_end_to_end(tmp_path, dsm, orthophoto):
    request = make_request(tmp_path, {"dsm": dsm, "orthophoto": orthophoto}, {"quality": "web-lite"})
    req = write_request(tmp_path, request)
    res = subprocess.run([sys.executable, os.path.join(PLUGIN_DIR, "main.py"), req],
                         cwd=PLUGIN_DIR, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr
    assert os.path.getsize(request["output_path"]) > 0
    result = json.load(open(request["result_path"]))
    assert result["metadata"]["workflow"] == "terrain"
    assert "finished" in res.stderr


def test_entrypoint_reports_user_errors_cleanly(tmp_path):
    request = make_request(tmp_path, {})
    req = write_request(tmp_path, request)
    res = subprocess.run([sys.executable, os.path.join(PLUGIN_DIR, "main.py"), req],
                         cwd=PLUGIN_DIR, capture_output=True, text=True, timeout=120)
    assert res.returncode == 1
    assert "error: select at least" in res.stderr
    assert "Traceback" not in res.stderr
