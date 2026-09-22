"""End-to-end runs through the entrypoint on the synthetic scene."""

import json
import os
import sys

import numpy as np
import pytest
import rasterio

import main as entry
from segplugin.pipeline import MODELS_DIR
from tests.conftest import reproject_to, scene_mask, write_dsm, write_dtm, write_orthophoto

try:
    import onnxruntime  # noqa: F401
    HAS_ORT = True
except ImportError:
    HAS_ORT = False


def _run(request_path, capsys=None):
    return entry.main(["main.py", request_path])


def _metadata(req):
    return json.load(open(req["result_path"]))["metadata"]


def test_dsm_and_dtm_height_classes_end_to_end(scene, make_request):
    req, path = make_request({"dsm": scene["dsm"], "dtm": scene["dtm"]}, {"resolution_m": 0.4})
    assert _run(path) == 0
    md = _metadata(req)
    assert md["model_id"] == "height-classes" and md["height_mode"] == "dsm-dtm"
    assert md["inputs_used"] == ["dsm", "dtm"]
    labels, building, trees, ground = scene_mask(req["output_path"])
    assert (labels[building] == 2).mean() > 0.95        # high_object
    assert (labels[trees] == 2).mean() > 0.9
    assert (labels[ground] == 0).mean() > 0.95           # ground
    with rasterio.open(req["output_path"]) as ds:
        assert ds.nodata == 255 and ds.colormap(1)[2][:3] == (0xE6, 0x55, 0x0D)
        assert json.loads(ds.tags()["SEGMENTATION_CLASSES"])[0]["name"] == "ground"
        assert ds.crs.to_epsg() == 32633 and ds.res[0] == pytest.approx(0.4)
    names = {c["name"]: c for c in md["stats"]["classes"]}
    assert names["high_object"]["area_m2"] == pytest.approx(120 + 100, rel=0.15)


def test_dsm_only_estimates_ground_and_warns(scene, make_request):
    req, path = make_request({"dsm": scene["dsm"]}, {"ground_window_m": 16})
    assert _run(path) == 0
    md = _metadata(req)
    assert md["height_mode"] == "dsm-estimated-ground"
    assert any("ground estimated" in w for w in md["progress"]["warnings"])
    labels, building, trees, ground = scene_mask(req["output_path"])
    assert (labels[building] == 2).mean() > 0.9
    assert (labels[ground] == 0).mean() > 0.9


def test_all_three_inputs_split_vegetation_from_structures(scene, make_request):
    req, path = make_request(scene, {"model": "height-classes"})
    assert _run(path) == 0
    md = _metadata(req)
    assert md["inputs_used"] == ["dsm", "dtm", "orthophoto"]
    labels, building, trees, ground = scene_mask(req["output_path"])
    assert (labels[building] == 6).mean() > 0.9          # high_structure (grey)
    assert (labels[trees] == 4).mean() > 0.9             # high_vegetation (green)
    assert md["backend"]["vegetation_index"] == "ExG"


def test_dtm_only_runs_geomorphons(tmp_path, make_request):
    dtm = write_dtm(tmp_path / "dtm.tif", res=0.5, slope=0.2)
    req, path = make_request({"dtm": dtm}, {"resolution_m": 1.0, "geomorphon_search_m": 5, "smoothing_radius_px": 0})
    assert _run(path) == 0
    md = _metadata(req)
    assert md["model_id"] == "geomorphons" and md["grid"]["resolution_m"] == pytest.approx(1.0)
    with rasterio.open(req["output_path"]) as ds:
        labels = ds.read(1)
    inner = labels[5:-5, 5:-5]
    assert (inner == 6).mean() > 0.9                     # a uniform slope is "slope"


def test_inputs_on_different_grids_and_crs(tmp_path, scene, make_request):
    dtm_4326 = reproject_to(scene["dtm"], tmp_path / "dtm4326.tif")
    req, path = make_request({"dsm": scene["dsm"], "dtm": dtm_4326}, {"model": "height-classes"})
    assert _run(path) == 0
    labels, building, trees, ground = scene_mask(req["output_path"])
    assert (labels[building] == 2).mean() > 0.9
    assert (labels[ground] == 0).mean() > 0.9


def test_vector_variant_writes_polygons_in_4326(scene, make_request):
    req, path = make_request({"dsm": scene["dsm"], "dtm": scene["dtm"]}, {"min_segment_area_m2": 5}, vector=True)
    assert _run(path) == 0
    fc = json.load(open(req["output_path"]))
    assert fc["type"] == "FeatureCollection" and fc["features"]
    classes = {f["properties"]["class"] for f in fc["features"]}
    assert "high_object" in classes and "ground" not in classes    # background not emitted
    lon, lat = fc["features"][0]["geometry"]["coordinates"][0][0]
    assert 14 < lon < 16 and 40 < lat < 41                         # UTM 33N -> lon/lat
    areas = sorted((f["properties"]["area"] for f in fc["features"]), reverse=True)
    assert areas[0] == pytest.approx(120, rel=0.15)                # the building
    md = _metadata(req)
    assert md["vector"]["feature_count"] == len(fc["features"])


def test_class_filter_and_unknown_class(scene, make_request):
    req, path = make_request({"dsm": scene["dsm"], "dtm": scene["dtm"]}, {"class_filter": "high_object"})
    assert _run(path) == 0
    labels, building, trees, ground = scene_mask(req["output_path"])
    assert set(np.unique(labels)) <= {0, 2, 255}
    req, path = make_request({"dsm": scene["dsm"]}, {"class_filter": "spaceship"})
    assert _run(path) == 1


def test_error_cases_exit_1_with_clear_message(scene, make_request, capsys, tmp_path):
    # incompatible explicit model
    req, path = make_request({"dtm": scene["dtm"]}, {"model": "height-classes"})
    assert _run(path) == 1
    assert "needs dsm" in capsys.readouterr().err
    # no inputs at all
    req, path = make_request({})
    assert _run(path) == 1
    assert "No input datasets" in capsys.readouterr().err
    # unreadable file
    junk = tmp_path / "junk.tif"
    junk.write_bytes(b"nope")
    req, path = make_request({"dsm": str(junk)})
    assert _run(path) == 1
    assert "not a readable raster" in capsys.readouterr().err
    # nothing written on failure
    assert not os.path.exists(req["output_path"])


def test_ignored_inputs_are_reported(scene, make_request):
    req, path = make_request(scene, {"model": "geomorphons", "resolution_m": 1.0, "geomorphon_search_m": 5})
    assert _run(path) == 0
    md = _metadata(req)
    assert md["inputs_used"] == ["dtm"] and md["inputs_ignored"] == ["orthophoto", "dsm"]
    assert any("does not use" in w for w in md["progress"]["warnings"])


@pytest.mark.skipif(not HAS_ORT, reason="onnxruntime not installed")
def test_onnx_landcover_model_runs_with_and_without_height(scene, make_request):
    req, path = make_request({"orthophoto": scene["orthophoto"]}, {"resolution_m": 0.4, "smoothing_radius_px": 0})
    assert _run(path) == 0
    md = _metadata(req)
    assert md["model_id"] in ("flair-rgb-resnet34-unet", "landcover-segformer-b0")
    assert md["height_fusion"] is False
    assert md["tiles"]["size"] == [100, 100]              # small scene, single padded tile
    with rasterio.open(req["output_path"]) as ds:
        labels = ds.read(1)
    assert labels.shape == (100, 100)
    assert (labels[:, :5] == 255).all()                   # alpha strip stays nodata
    assert set(np.unique(labels[:, 10:])) <= set(range(19))

    req2, path2 = make_request(scene, {"resolution_m": 0.4, "smoothing_radius_px": 0})
    assert _run(path2) == 0
    md2 = _metadata(req2)
    assert md2["height_fusion"] is True and md2["height_mode"] == "dsm-dtm"
    assert md2["inputs_used"] == ["orthophoto", "dsm", "dtm"]


@pytest.mark.skipif(not HAS_ORT, reason="onnxruntime not installed")
def test_onnx_model_missing_file_is_a_model_error(tmp_path, scene, make_request):
    import shutil
    from segplugin import pipeline
    from segplugin.errors import ModelError
    models = tmp_path / "models"
    shutil.copytree(MODELS_DIR, models, ignore=shutil.ignore_patterns("*.onnx"))
    req, _ = make_request({"orthophoto": scene["orthophoto"]}, {"model": "landcover-segformer-b0"})
    with pytest.raises(ModelError, match="unavailable"):
        pipeline.run(req, models_dir=str(models))
    # A card whose file is present but corrupt is a hard error.
    shutil.copy(os.path.join(MODELS_DIR, "landcover-segformer-b0.onnx"), models / "landcover-segformer-b0.onnx")
    with open(models / "landcover-segformer-b0.onnx", "r+b") as f:
        f.seek(100)
        f.write(b"corrupt")
    with pytest.raises(ModelError, match="corrupt"):
        pipeline.run(req, models_dir=str(models))
