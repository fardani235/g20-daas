"""End-to-end runs through the entrypoint on the synthetic scene with the fixture model."""

import json
import os

import pytest
from shapely.geometry import shape

import main as entry
from bfplugin import pipeline
from tests.conftest import AREAS, write_scene, write_ungeoreferenced


def _run(path):
    return entry.main(["main.py", path])


def _result(req):
    fc = json.load(open(req["output_path"]))
    md = json.load(open(req["result_path"]))["metadata"]
    return fc, md


def _by_area(fc):
    return sorted(fc["features"], key=lambda f: f["properties"]["area"])


def test_end_to_end_separates_touching_buildings(scene, make_request, use_fixture_models, capsys):
    req, path = make_request(scene)
    assert _run(path) == 0, capsys.readouterr().err
    fc, md = _result(req)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 4        # A, B, C, E; D dropped
    feats = _by_area(fc)
    areas = [f["properties"]["area"] for f in feats]
    expected = sorted([AREAS["B"], AREAS["E"], AREAS["A"], AREAS["C"]])
    assert areas == pytest.approx(expected, rel=0.08)
    for f in fc["features"]:
        p = f["properties"]
        assert p["class"] == "building" and p["score"] > 0.95 and set(p) >= {"class", "score", "area"}
        lon, lat = f["geometry"]["coordinates"][0][0]
        assert 14 < lon < 16 and 40 < lat < 41                                     # UTM 33N → lon/lat
        assert shape(f["geometry"]).is_valid and len(f["geometry"]["coordinates"]) == 1  # exterior ring only
    # the L-shape is the only one not regularised; the rectangles have 4 corners
    regularized = [f["properties"]["regularized"] for f in feats]
    assert regularized == [True, True, True, False]
    assert all(len(f["geometry"]["coordinates"][0]) == 5 for f in feats[:3])
    # A and B shared a wall and came out of one blob
    assert md["instances"]["components"] == 4 and md["instances"]["split"] == 1 and md["instances"]["instances"] == 4
    assert md["instances"]["dropped_small"] == 1 and md["vector"]["dropped_small"] == 0      # D (1 m²) dropped before seeding
    assert md["model_id"] == "tiny" and md["grid"]["resolution_m"] == pytest.approx(0.2)
    assert md["tiles"]["weighting"] == "gaussian" and md["tiles"]["overlap"] == 64 and md["tiles"]["count"] == 4
    assert md["legend"] == [{"name": "building", "color": "#d7301f"}]
    assert md["vector"]["feature_count"] == 4 and md["progress"]["stages"][-1]["name"] == "write output"
    progress = json.load(open(req["progress_path"]))
    assert progress["percent"] == 100


def test_small_blob_seeding_path_also_separates(scene, make_request, use_fixture_models):
    req, path = make_request(scene, {"large_blob_area_px": 100000, "seed_min_distance": 3})
    assert _run(path) == 0
    fc, md = _result(req)
    assert len(fc["features"]) == 4 and md["instances"]["large_blobs"] == 0 and md["instances"]["split"] == 1


def test_parameters_change_the_result(scene, make_request, use_fixture_models):
    # keep everything (D too) and never regularise
    req, path = make_request(scene, {"min_area_m2": 0, "regularize_area_threshold": 1, "simplify_m": 0})
    assert _run(path) == 0
    fc, md = _result(req)
    assert len(fc["features"]) == 5 and md["vector"]["regularized"] == 0
    assert all(not f["properties"]["regularized"] for f in fc["features"])
    assert md["parameters"]["effective"]["min_area_m2"] == 0 and md["parameters"]["requested"]["simplify_m"] == 0
    # a very deep prominence requirement merges A and B
    req, path = make_request(scene, {"h_maxima_depth": 1.5, "large_blob_area_px": 1})
    assert _run(path) == 0
    fc, md = _result(req)
    assert md["instances"]["split"] == 0 and len(fc["features"]) == 3


def test_coarser_resolution_and_no_overlap(scene, make_request, use_fixture_models):
    req, path = make_request(scene, {"resolution_m": 0.4, "overlap": 0})
    assert _run(path) == 0
    fc, md = _result(req)
    assert md["grid"]["resolution_m"] == pytest.approx(0.4) and md["tiles"]["overlap"] == 0
    assert len(fc["features"]) == 4


def test_finer_than_native_resolution_warns_and_uses_native(scene, make_request, use_fixture_models):
    req, path = make_request(scene, {"resolution_m": 0.05})
    assert _run(path) == 0
    _, md = _result(req)
    assert md["grid"]["resolution_m"] == pytest.approx(0.2)
    assert any("finer than the orthophoto" in w for w in md["progress"]["warnings"])


def test_grid_over_the_pixel_budget_fails_with_the_resolution_to_use(scene, make_request, use_fixture_models, capsys,
                                                                     monkeypatch):
    monkeypatch.setattr(pipeline, "DEFAULT_MAX_PIXELS", 20_000)       # the scene is 400 x 300 = 120 000 px
    req, path = make_request(scene)
    assert _run(path) == 1
    err = capsys.readouterr().err
    assert "400x300 px (0.12 megapixels), above the 0.02 megapixel limit" in err
    assert "set resolution_m to 0.50 or more" in err          # 0.49 would still be one row over budget
    assert "Traceback" not in err and not os.path.exists(req["output_path"])
    # the suggestion is sufficient: re-running at it fits the same budget
    req, path = make_request(scene, {"resolution_m": 0.50})
    assert _run(path) == 0
    _, md = _result(req)
    assert md["grid"]["pixels"] <= 20_000


def test_suggested_resolution_always_fits():
    from bfplugin.grid import _grid_size, suggest_resolution
    for budget in (20_000, 33_333, 50_000, 99_999):
        s = suggest_resolution(0, 0, 80, 60, 1.0, 0.2, 120_000, budget)
        w, h = _grid_size(0, 0, 80, 60, s)
        assert w * h <= budget and s >= 0.2 * (120_000 / budget) ** 0.5 - 1e-9


def test_unknown_parameter_is_a_warning(scene, make_request, use_fixture_models):
    req, path = make_request(scene, {"bogus": 3})
    assert _run(path) == 0
    _, md = _result(req)
    assert any("bogus" in w for w in md["progress"]["warnings"])


@pytest.mark.parametrize("params, message", [
    ({"model": "missing"}, "unavailable"),
    ({"model": "badsum"}, "sha256"),
    ({"model": "badshape"}, "shape"),
    ({"model": "twoch"}, "channel 2"),
    ({"model": "wrongsize"}, "input_size"),
    ({"model": "nope"}, "Unknown model"),
    ({"confidence": 2}, "confidence"),
])
def test_model_and_parameter_problems_fail_with_one_clear_line(scene, make_request, use_fixture_models, capsys,
                                                               params, message):
    req, path = make_request(scene, params)
    assert _run(path) == 1
    err = capsys.readouterr().err
    assert "error: " in err and message in err and "Traceback" not in err
    assert not os.path.exists(req["output_path"])


def test_input_problems(tmp_path, make_request, use_fixture_models, capsys):
    req, path = make_request(None)
    assert _run(path) == 1 and "No orthophoto" in capsys.readouterr().err
    req, path = make_request(write_ungeoreferenced(tmp_path / "raw.tif"))
    assert _run(path) == 1 and "coordinate reference system" in capsys.readouterr().err
    (tmp_path / "junk.tif").write_bytes(b"not a tiff")
    req, path = make_request(str(tmp_path / "junk.tif"))
    assert _run(path) == 1 and "not a readable raster" in capsys.readouterr().err


def test_three_band_scene_without_alpha(tmp_path, make_request, use_fixture_models):
    ortho = write_scene(tmp_path / "rgb.tif", alpha=False)
    req, path = make_request(ortho)
    assert _run(path) == 0
    fc, md = _result(req)
    assert len(fc["features"]) == 4 and md["tiles"]["skipped_empty"] == 0


def test_shipped_card_without_weights_reports_unavailable(scene, make_request, capsys):
    """The repo has no weights: the real card must fail with the fetch instruction, not a crash."""
    if os.path.isfile(os.path.join(pipeline.MODELS_DIR, "dinov3s_buildings.onnx")):
        pytest.skip("weights are present locally")
    req, path = make_request(scene)
    assert _run(path) == 1
    err = capsys.readouterr().err
    assert "hot-dinov3s-buildings" in err and "tools/fetch_models.py" in err and "Traceback" not in err
