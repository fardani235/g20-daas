"""End-to-end runs through the entrypoint on a synthetic orthophoto.

The fixture detectors return fixed boxes in model-input coordinates, so the
expected raster/geographic positions follow from the letterbox scale: a 256 px
tile fed to a 640 px model is scaled by 2.5, so the YOLO fixture's centre box
(cx=cy=320, 100 px) lands at raster pixels 108-148 in both axes.
"""

import json
import os
import shutil

import pytest
import rasterio
from rasterio.warp import transform_bounds

import main as entry
from detplugin import pipeline
from detplugin.errors import InputError, ParameterError
from detplugin.progress import Progress
from tests.conftest import SHIPPED_MODELS, install_models, write_orthophoto, write_ungeoreferenced


@pytest.fixture(autouse=True)
def _use_test_models(models_dir, monkeypatch):
    monkeypatch.setattr(pipeline, "MODELS_DIR", models_dir)


def _run(request_path):
    return entry.main(["main.py", request_path])


def _result(req):
    with open(req["output_path"], encoding="utf-8") as f:
        collection = json.load(f)
    with open(req["result_path"], encoding="utf-8") as f:
        metadata = json.load(f)["metadata"]
    return collection, metadata


def _lonlat_bounds(path):
    with rasterio.open(path) as ds:
        return transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)


def _inside(feature, bounds, tol=1e-6):
    minx, miny, maxx, maxy = bounds
    return all(minx - tol <= x <= maxx + tol and miny - tol <= y <= maxy + tol
               for x, y in feature["geometry"]["coordinates"][0])


def test_yolo_run_emits_georeferenced_boxes_with_class_and_confidence(ortho, make_request):
    req, path = make_request(ortho)  # default model: yolo80, recommended 640/64/0.25
    assert _run(path) == 0
    collection, md = _result(req)

    assert collection["type"] == "FeatureCollection" and len(collection["features"]) == 2
    by_class = {f["properties"]["class"]: f for f in collection["features"]}
    assert set(by_class) == {"c0", "c1"}
    c0 = by_class["c0"]
    assert c0["geometry"]["type"] == "Polygon" and len(c0["geometry"]["coordinates"][0]) == 5
    assert c0["properties"]["confidence"] == 0.9 and c0["properties"]["class_id"] == 0
    # 40x40 raster px at 0.1 m -> 16 m²
    assert c0["properties"]["area_m2"] == pytest.approx(16.0, abs=0.05)
    bounds = _lonlat_bounds(ortho)
    assert all(_inside(f, bounds) for f in collection["features"])

    assert md["model_id"] == "yolo80" and md["backend"]["family"] == "yolo"
    assert md["total"] == 2 and md["counts"] == {"c0": 1, "c1": 1}
    assert md["tiles"] == {"count": 1, "size": 640, "overlap": 64, "skipped_empty": 0}
    assert md["parameters"]["confidence"] == 0.25 and md["orthophoto"]["epsg"] == 32633
    assert md["legend"][0] == {"name": "c0", "color": "#ff0000"}
    assert md["progress"]["stages"][0]["name"] == "open orthophoto"
    assert json.load(open(req["progress_path"]))["percent"] == 100


def test_box_position_follows_letterbox_scale(ortho, make_request):
    req, path = make_request(ortho, {"class_filter": "c0"})
    assert _run(path) == 0
    collection, _ = _result(req)
    ring = collection["features"][0]["geometry"]["coordinates"][0]
    with rasterio.open(ortho) as ds:
        from rasterio.warp import transform
        xs, ys = transform("EPSG:4326", ds.crs, [p[0] for p in ring], [p[1] for p in ring])
        cols = [(x - ds.transform.c) / ds.transform.a for x in xs]
        rows = [(ds.transform.f - y) / -ds.transform.e for y in ys]
    assert min(cols) == pytest.approx(108, abs=0.01) and max(cols) == pytest.approx(148, abs=0.01)
    assert min(rows) == pytest.approx(108, abs=0.01) and max(rows) == pytest.approx(148, abs=0.01)


def test_class_filter_and_confidence_restrict_output(ortho, make_request):
    req, path = make_request(ortho, {"class_filter": "c1"})
    assert _run(path) == 0
    collection, md = _result(req)
    assert [f["properties"]["class"] for f in collection["features"]] == ["c1"]
    assert md["counts"] == {"c1": 1}

    req, path = make_request(ortho, {"confidence": 0.7})
    assert _run(path) == 0
    collection, _ = _result(req)
    assert [f["properties"]["class"] for f in collection["features"]] == ["c0"]


def test_unknown_class_filter_names_the_models_classes(ortho, make_request):
    req, _ = make_request(ortho, {"class_filter": "car"})
    with pytest.raises(ParameterError, match="class_filter names not in model 'yolo80': car"):
        pipeline.run(req, Progress(stream=open(os.devnull, "w")))


def test_no_detections_gives_empty_collection(ortho, make_request):
    req, path = make_request(ortho, {"model": "silent"})
    assert _run(path) == 0
    collection, md = _result(req)
    assert collection["features"] == [] and md["total"] == 0 and md["counts"] == {}


def test_max_detections_caps_and_warns(ortho, make_request):
    req, path = make_request(ortho, {"max_detections": 1})
    assert _run(path) == 0
    collection, md = _result(req)
    assert len(collection["features"]) == 1 and collection["features"][0]["properties"]["confidence"] == 0.9
    assert any("max_detections" in w for w in md["progress"]["warnings"])


def test_torchvision_model_and_label_offset(ortho, make_request):
    req, path = make_request(ortho, {"model": "tv-tree", "confidence": 0.3})
    assert _run(path) == 0
    collection, md = _result(req)
    assert md["backend"]["family"] == "torchvision" and md["tiles"]["size"] == 256
    assert len(collection["features"]) == 2
    assert {f["properties"]["class"] for f in collection["features"]} == {"tree"}

    req, path = make_request(ortho, {"model": "tv-tree"})  # card recommends 0.5: drops the 0.4 box
    assert _run(path) == 0
    collection, _ = _result(req)
    assert len(collection["features"]) == 1

    req, path = make_request(ortho, {"model": "tv-1based"})  # labels are 1, offset 1 -> "tree"
    assert _run(path) == 0
    collection, md = _result(req)
    assert [f["properties"]["class"] for f in collection["features"]] == ["tree", "tree"]
    assert md["progress"]["warnings"] == []


def test_labels_outside_the_card_are_dropped_with_a_warning(ortho, make_request):
    # tv-tree lists one class but the 1-based fixture returns label 1 -> out of range
    req, path = make_request(ortho, {"model": "tv-tree", "confidence": 0.3})
    shutil.copy(os.path.join(os.path.dirname(pipeline.MODELS_DIR), "models", "tiny_torchvision_1based.onnx"),
                os.path.join(pipeline.MODELS_DIR, "tiny_torchvision.onnx"))
    assert _run(path) == 0
    collection, md = _result(req)
    assert collection["features"] == []
    assert any("class id outside the card's 1 classes" in w for w in md["progress"]["warnings"])


def test_multi_tile_run_covers_the_raster_and_stays_in_bounds(tmp_path, make_request):
    ortho = write_orthophoto(tmp_path / "big.tif", width=1000, height=700, res=0.1)
    req, path = make_request(ortho, {"tile_size": 320, "overlap": 32})
    assert _run(path) == 0
    collection, md = _result(req)
    assert md["tiles"]["count"] == 4 * 3 and md["tiles"]["size"] == 320
    assert md["total"] >= 1 and md["candidates"] >= md["total"]
    bounds = _lonlat_bounds(ortho)
    assert all(_inside(f, bounds) for f in collection["features"])


def test_ground_tile_size_is_derived_from_gsd(tmp_path, make_request):
    ortho = write_orthophoto(tmp_path / "o.tif", width=600, height=600, res=0.05)
    req, path = make_request(ortho, {"tile_size_m": 16, "overlap_m": 1.6})  # 320 px, 32 px at 5 cm
    assert _run(path) == 0
    _, md = _result(req)
    assert md["tiles"]["size"] == 320 and md["tiles"]["overlap"] == 32
    assert md["orthophoto"]["gsd_m"] == pytest.approx(0.05)


def test_geographic_crs_gsd_is_in_metres(tmp_path, make_request):
    # ~1e-6 degrees per pixel near 45°N is roughly 8-11 cm
    ortho = write_orthophoto(tmp_path / "geo.tif", width=128, height=128, res=1e-6, crs="EPSG:4326")
    with rasterio.open(ortho, "r+") as ds:
        from rasterio.transform import from_origin
        ds.transform = from_origin(9.0, 45.0, 1e-6, 1e-6)
    req, path = make_request(ortho, {"tile_size_m": 12.8})
    assert _run(path) == 0
    _, md = _result(req)
    assert 0.07 < md["orthophoto"]["gsd_m"] < 0.12
    assert 96 <= md["tiles"]["size"] <= 192


def test_tiles_outside_the_valid_area_are_skipped(tmp_path, make_request):
    ortho = write_orthophoto(tmp_path / "rgba.tif", width=512, height=128, res=0.1,
                             alpha=True, transparent_right_of=256)
    req, path = make_request(ortho, {"tile_size": 128, "overlap": 0})
    assert _run(path) == 0
    _, md = _result(req)
    assert md["tiles"]["count"] == 4 and md["tiles"]["skipped_empty"] == 2


def test_non_8bit_orthophoto_warns_but_runs(tmp_path, make_request):
    ortho = write_orthophoto(tmp_path / "u16.tif", dtype="uint16")
    req, path = make_request(ortho)
    assert _run(path) == 0
    _, md = _result(req)
    assert any("not 8-bit" in w for w in md["progress"]["warnings"])


def test_unknown_parameters_warn(ortho, make_request):
    req, path = make_request(ortho, {"threshold": 0.5})
    assert _run(path) == 0
    _, md = _result(req)
    assert any("unknown parameters: threshold" in w for w in md["progress"]["warnings"])


def test_legacy_raster_input_name_is_accepted(ortho, tmp_path):
    req = {"inputs": {"raster": ortho}, "params": {}, "output_path": str(tmp_path / "o.geojson")}
    assert pipeline.run(req, Progress(stream=open(os.devnull, "w")))["total"] == 2


# --- failures the customer must be able to read -------------------------------

def _fails_with(capsys, path, *fragments):
    assert _run(path) == 1
    err = capsys.readouterr().err
    for fragment in fragments:
        assert fragment in err, err
    assert "Traceback" not in err, err


def test_missing_orthophoto_input(tmp_path, capsys):
    req = {"inputs": {}, "params": {}, "output_path": str(tmp_path / "o.geojson")}
    p = tmp_path / "r.json"
    p.write_text(json.dumps(req))
    _fails_with(capsys, str(p), "error: no orthophoto was selected")


def test_unreadable_and_ungeoreferenced_orthophoto(tmp_path, make_request, capsys):
    _, path = make_request(str(tmp_path / "nope.tif"))
    _fails_with(capsys, path, "error: orthophoto is not a readable raster")
    _, path = make_request(write_ungeoreferenced(tmp_path / "plain.tif"))
    _fails_with(capsys, path, "no coordinate reference system")
    with pytest.raises(InputError):
        pipeline.run({"inputs": {"orthophoto": str(tmp_path / "plain.tif")}, "params": {},
                      "output_path": str(tmp_path / "x.geojson")}, Progress(stream=open(os.devnull, "w")))


def test_unknown_model(ortho, make_request, capsys):
    _, path = make_request(ortho, {"model": "nope"})
    _fails_with(capsys, path, "error: Unknown model 'nope'", "yolo80")


def test_weights_not_packaged(ortho, make_request, capsys):
    _, path = make_request(ortho, {"model": "missing"})
    _fails_with(capsys, path, "Model 'missing' is unavailable", "models/not-fetched.onnx is not in the package",
                "tools/fetch_models.py")


def test_shipped_cards_without_weights_fail_clearly(ortho, make_request, tmp_path, monkeypatch, capsys):
    """The committed package has cards but no weights until fetch_models.py runs."""
    d = tmp_path / "shipped"
    d.mkdir()
    for name in os.listdir(SHIPPED_MODELS):
        if name.endswith(".json"):
            shutil.copy(os.path.join(SHIPPED_MODELS, name), d / name)
    monkeypatch.setattr(pipeline, "MODELS_DIR", str(d))
    _, path = make_request(ortho)  # default model from the shipped cards
    _fails_with(capsys, path, "Model 'deepforest-tree-crowns' is unavailable",
                "models/deepforest-tree-crowns.onnx is not in the package")


def test_checksum_mismatch(ortho, make_request, capsys):
    _, path = make_request(ortho, {"model": "badsum"})
    _fails_with(capsys, path, "does not match the checksum in its card (badsum)")


def test_class_count_mismatch(ortho, make_request, capsys):
    _, path = make_request(ortho, {"model": "sixcls"})
    _fails_with(capsys, path, "model 'sixcls' predicts 6 classes but its card lists 80")


def test_unsupported_model_output(ortho, make_request, capsys):
    _, path = make_request(ortho, {"model": "badrank"})
    _fails_with(capsys, path, "model 'badrank' is not a supported detector")


def test_invalid_parameter(ortho, make_request, capsys):
    _, path = make_request(ortho, {"confidence": 3})
    _fails_with(capsys, path, "error: confidence must be between 0")


def test_usage_error():
    assert entry.main(["main.py"]) == 2
