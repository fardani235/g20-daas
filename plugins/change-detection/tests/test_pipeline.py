"""End-to-end runs on the synthetic site (see conftest for what changes between the dates)."""

import json
import os
import subprocess
import sys

import numpy as np
import pytest
import rasterio
from shapely.geometry import shape

from cdplugin.errors import InputError, ParameterError
from cdplugin.pipeline import run
from cdplugin.progress import Progress
from tests.conftest import CUT_M3, FILL_M3, ORIGIN, PLUGIN_DIR, reproject_to, request_for, survey


def _run(request, **kw):
    progress = Progress(stream=open(os.devnull, "w"), progress_path=request.get("progress_path"))
    return run(request, progress, **kw)


def _features(request):
    with open(request["output_path"], encoding="utf-8") as f:
        return json.load(f)["features"]


def _classes(features):
    return sorted(f["properties"]["class"] for f in features)


def _coarse_pair(tmp_path, **after_kw):
    """A quicker pair at 0.5 m for the scenario tests."""
    before = survey(tmp_path, "sep", res=0.5, seed=1)
    kw = {"changed": True, "res": 0.5, "offset": 0.83, "seed": 2, "gain": 0.85}
    kw.update(after_kw)
    return before, survey(tmp_path, "oct", **kw)


# --- the standard comparison -------------------------------------------------

@pytest.fixture(scope="module")
def standard(pair, tmp_path_factory):
    d = tmp_path_factory.mktemp("standard")
    request = request_for(d, pair["after"], pair["before"])
    return request, _run(request), _features(request)


def test_volumes_match_the_known_earthworks(standard):
    _, meta, _ = standard
    change = meta["change"]
    assert change["fill_volume_m3"] == pytest.approx(FILL_M3, rel=0.02)
    assert change["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.02)
    assert change["net_volume_m3"] == pytest.approx(FILL_M3 - CUT_M3, rel=0.03)
    # pit 600 m² + stockpile 400 m² + trench 40 m² of the area both surveys cover
    # (plus the one-cell rim where a sharp edge is blurred by resampling)
    assert change["changed_area_m2"] == pytest.approx(1040, rel=0.10)
    assert change["changed_share"] == pytest.approx(change["changed_area_m2"] / change["compared_area_m2"], abs=1e-5)
    assert 0.03 < change["changed_share"] < 0.045
    assert (change["fill_regions"], change["cut_regions"]) == (1, 2)


def test_height_offset_between_the_runs_is_found_and_removed(standard):
    _, meta, _ = standard
    vertical = meta["alignment"]["vertical"]
    assert vertical["offset_removed_m"] == pytest.approx(0.83, abs=0.005)
    assert vertical["noise_sigma_m"] == pytest.approx(0.025, abs=0.012)
    assert vertical["stable_fraction"] > 0.9
    assert vertical["systematic_sigma_m"] < 0.005 and vertical["consistency"]["systematic_sigma_measured"]
    assert meta["reliability"] == {"grade": "high", "reasons": []}
    assert meta["warnings"] == []


def test_grid_is_common_ground(standard):
    _, meta, _ = standard
    grid = meta["grid"]
    assert grid["epsg"] == 32633 and grid["resolution_m"] == 0.25      # the coarser of 0.20 and 0.25
    assert grid["bounds"][0] == pytest.approx(ORIGIN[0] + 4.0)           # the smaller footprint
    assert meta["alignment"]["horizontal"]["applied"] is False


def test_layer_shows_each_change_once_with_its_numbers(standard):
    _, meta, features = standard
    assert _classes(features) == ["appearance", "cut", "cut", "fill", "vegetation"]
    by_volume = [f["properties"] for f in features if f["properties"]["class"] in ("cut", "fill")]
    pit, pile, trench = by_volume
    assert pit["class"] == "cut" and pit["volume_m3"] == pytest.approx(1200, rel=0.02)
    assert pit["mean_change_m"] == pytest.approx(-2.0, abs=0.15) and pit["area"] == pytest.approx(600, rel=0.06)
    assert pile["class"] == "fill" and pile["volume_m3"] == pytest.approx(600, rel=0.02)
    # The 1 m wide trench survives the noise filters; the 1 m² speck does not.
    assert trench["class"] == "cut" and trench["volume_m3"] == pytest.approx(40, rel=0.1)
    assert all(p["volume_uncertainty_m3"] > 0 for p in by_volume)
    assert meta["vector"] == {"feature_count": 5, "regions": 5, "omitted_small_regions": 0}
    for f in features:
        assert shape(f["geometry"]).is_valid


def test_vegetation_is_reported_but_not_counted(standard):
    _, meta, features = standard
    trees = next(f["properties"] for f in features if f["properties"]["class"] == "vegetation")
    assert trees["area"] == pytest.approx(225, rel=0.15) and trees["mean_change_m"] > 2.5
    assert meta["change"]["vegetation_area_m2"] == pytest.approx(trees["area"], abs=0.01)
    assert meta["photos"]["vegetation_separated"] is True
    # 15 x 15 m x 3 m of canopy would otherwise more than double the fill.
    assert meta["change"]["fill_volume_m3"] < 620


def test_appearance_only_change_is_flagged_without_a_volume(standard):
    _, meta, features = standard
    paving = next(f["properties"] for f in features if f["properties"]["class"] == "appearance")
    assert paving["area"] == pytest.approx(144, rel=0.1) and "volume_m3" not in paving
    assert meta["change"]["appearance_area_m2"] == pytest.approx(paving["area"], abs=0.01)
    assert meta["photos"]["appearance"]["flagged_share"] < 0.01


def test_summary_is_readable(standard):
    _, meta, _ = standard
    rows = {r["label"]: r["value"] for r in meta["summary"]}
    assert rows["Compared"] == "Site A — 1 September (before) → Site A — 1 October (after)"
    assert 3.5 < float(rows["Changed"].split("%")[0]) < 4.2 and "% of that area (1," in rows["Changed"]
    assert rows["Fill"].startswith("60") and "m³ ±" in rows["Fill"]
    assert rows["Cut"].startswith("1,2") and "more cut than fill" in rows["Net"]
    assert rows["Height offset removed"].startswith("+0.83 m")
    assert rows["Smallest height change counted"] == "0.05 m (95% confidence)"
    assert rows["Reliability"] == "High"
    assert len(json.dumps({"metadata": meta})) < 64 * 1024        # the runner's result.json limit


def test_progress_was_published(standard):
    request, _, _ = standard
    with open(request["progress_path"], encoding="utf-8") as f:
        assert json.load(f) == {"percent": 100, "message": "finished"}


# --- which survey is which ---------------------------------------------------

def test_running_on_the_earlier_survey_gives_the_same_answer(pair, tmp_path):
    request = request_for(tmp_path, pair["before"], pair["after"], params={"reference_role": "after"})
    meta = _run(request)
    assert meta["change"]["fill_volume_m3"] == pytest.approx(FILL_M3, rel=0.02)
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.02)
    assert meta["alignment"]["vertical"]["offset_removed_m"] == pytest.approx(0.83, abs=0.005)
    assert meta["surveys"]["before"]["task"] == "t-oct" and meta["surveys"]["after"]["task"] == "t-sep"


def test_swapping_the_roles_swaps_cut_and_fill(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"], params={"reference_role": "after"})
    meta = _run(request)
    assert meta["change"]["cut_volume_m3"] == pytest.approx(FILL_M3, rel=0.02)
    assert meta["change"]["fill_volume_m3"] == pytest.approx(CUT_M3, rel=0.02)
    assert meta["alignment"]["vertical"]["offset_removed_m"] == pytest.approx(-0.83, abs=0.005)


# --- registration artefacts ----------------------------------------------------

def test_horizontal_shift_is_corrected(tmp_path):
    before, after = _coarse_pair(tmp_path, shift=(1.5, -1.0))
    request = request_for(tmp_path, after, before)
    meta = _run(request)
    horizontal = meta["alignment"]["horizontal"]
    # The comparison (earlier) survey sits 1.5 m west / 1.0 m north of this one.
    assert horizontal["applied"] and horizontal["significant"]
    assert horizontal["east_m"] == pytest.approx(-1.5, abs=0.3) and horizontal["north_m"] == pytest.approx(1.0, abs=0.3)
    assert meta["change"]["fill_volume_m3"] == pytest.approx(FILL_M3, rel=0.04)
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.04)
    # No building-edge slivers: the same regions as without a shift.
    assert (meta["change"]["fill_regions"], meta["change"]["cut_regions"]) == (1, 2)
    assert any(r["label"] == "Horizontal shift corrected" for r in meta["summary"])


def test_uncorrected_shift_widens_the_tolerance_and_says_so(tmp_path):
    before, after = _coarse_pair(tmp_path, shift=(1.5, -1.0))
    request = request_for(tmp_path, after, before, params={"horizontal_alignment": "off"})
    meta = _run(request)
    assert not meta["alignment"]["horizontal"]["applied"] and meta["alignment"]["horizontal"]["significant"]
    assert meta["detection"]["registration_tolerance_m"] >= 1.5
    assert any("shifted against each other" in w for w in meta["warnings"])
    assert meta["reliability"]["grade"] == "medium"
    # Building walls (4.5-6 m) displaced by 1.8 m must not become earthwork.
    assert meta["change"]["fill_regions"] <= 2 and meta["change"]["fill_volume_m3"] < 1.2 * FILL_M3


def test_without_tolerance_a_small_shift_floods_the_result(tmp_path):
    # Why the tolerance exists: half a cell of wobble, compared cell by cell.
    before, after = _coarse_pair(tmp_path, shift=(0.25, 0.0), noise=0.02)
    base = {"horizontal_alignment": "off"}
    strict = _run(request_for(tmp_path, after, before, params={**base, "registration_tolerance_m": 0},
                              output="strict.geojson"))
    tolerant = _run(request_for(tmp_path, after, before, params=base, output="tolerant.geojson"))
    assert strict["vector"]["regions"] > tolerant["vector"]["regions"]
    assert tolerant["change"]["cut_regions"] == 2 and tolerant["change"]["fill_regions"] == 1


def test_tilt_is_detected_then_removed_on_request(tmp_path):
    before, after = _coarse_pair(tmp_path, tilt=(0.5, -0.3))
    auto = _run(request_for(tmp_path, after, before, output="auto.geojson"))
    assert auto["alignment"]["vertical"]["consistency"]["tilt_across_site_m"] == pytest.approx(0.8, abs=0.1)
    assert any("not constant" in w for w in auto["warnings"])
    assert auto["reliability"]["grade"] == "medium"
    # Left uncorrected, the drift is carried as uncertainty: a higher bar, wider error bars.
    assert auto["detection"]["level_of_detection_m"] > 0.15
    assert auto["change"]["net_volume_sigma_m3"] > 50

    fixed = _run(request_for(tmp_path, after, before, params={"vertical_alignment": "tilt"}, output="tilt.geojson"))
    vertical = fixed["alignment"]["vertical"]
    assert vertical["tilt_removed"] and vertical["after_correction"]["tilt_across_site_m"] < 0.05
    assert fixed["detection"]["level_of_detection_m"] < 0.08
    assert fixed["change"]["fill_volume_m3"] == pytest.approx(FILL_M3, rel=0.04)
    assert fixed["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.04)
    assert fixed["reliability"]["grade"] == "high"


def test_trusting_the_heights_keeps_the_offset_and_flags_it(tmp_path):
    before, after = _coarse_pair(tmp_path, offset=0.20)
    meta = _run(request_for(tmp_path, after, before, params={"vertical_alignment": "none"}))
    vertical = meta["alignment"]["vertical"]
    assert vertical["offset_removed_m"] == 0 and vertical["offset_of_unchanged_ground_m"] == pytest.approx(0.2, abs=0.01)
    # The user vouched for the heights, so 0.2 m everywhere is reported as fill...
    assert meta["change"]["changed_share"] > 0.9
    assert meta["change"]["fill_volume_m3"] > 0.2 * 0.9 * meta["change"]["compared_area_m2"]
    # ...but not without saying what that rests on.
    assert meta["reliability"]["grade"] == "medium"
    assert any("same ground control" in r for r in meta["reliability"]["reasons"])
    assert any("use automatic height alignment" in w for w in meta["warnings"])
    rows = {r["label"]: r["value"] for r in meta["summary"]}
    assert "not removed" in rows["Height offset between surveys"]


def test_gcp_grade_surveys_can_be_compared_as_they_are(tmp_path):
    before, after = _coarse_pair(tmp_path, offset=0.0)
    meta = _run(request_for(tmp_path, after, before, params={"vertical_alignment": "none"}))
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.04)
    assert meta["reliability"]["grade"] == "high"


# --- honesty about what cannot be known ------------------------------------------

def test_site_wide_change_cannot_be_told_from_an_offset_and_the_run_says_so(tmp_path):
    # 80 % of the site raised by 0.6 m. From the two surfaces alone this is
    # indistinguishable from "the surveys are 0.6 m apart and 20 % was dug out".
    before = survey(tmp_path, "sep", res=0.5, seed=1, with_ortho=False)
    after = survey(tmp_path, "oct", res=0.5, seed=2, with_ortho=False)
    with rasterio.open(after["dsm"], "r+") as dst:
        z = dst.read(1)
        z[:, : int(z.shape[1] * 0.8)] += np.where(z[:, : int(z.shape[1] * 0.8)] > -9000, 0.6, 0)
        dst.write(z, 1)
    meta = _run(request_for(tmp_path, after, before))
    # The method takes the larger group as unchanged ground: here that is the
    # wrong one. It cannot know — but it must not present the result as solid.
    assert meta["alignment"]["vertical"]["offset_removed_m"] == pytest.approx(0.6, abs=0.02)
    assert meta["alignment"]["vertical"]["second_peak_offset_m"] == pytest.approx(0.0, abs=0.05)
    assert meta["reliability"]["grade"] == "medium"
    assert any("-0.60 m from the level taken as unchanged ground" in r and "inverted" in r
               for r in meta["reliability"]["reasons"])
    assert meta["summary"][-1]["value"].startswith("Medium — a second, smaller part of the site")
    # Trusting the heights (both surveys on ground control) resolves it.
    trusted = _run(request_for(tmp_path, after, before, params={"vertical_alignment": "none"},
                               output="trusted.geojson"))
    assert trusted["change"]["fill_area_m2"] > 0.7 * trusted["change"]["compared_area_m2"]
    assert trusted["change"]["cut_area_m2"] == 0


def test_two_equal_candidates_for_the_offset_are_reported(tmp_path):
    before = survey(tmp_path, "sep", res=0.5, seed=1, with_ortho=False)
    after = survey(tmp_path, "oct", res=0.5, seed=2, with_ortho=False)
    with rasterio.open(after["dsm"], "r+") as dst:
        z = dst.read(1)
        half = int(z.shape[1] * 0.42)
        z[:, :half] += np.where(z[:, :half] > -9000, 0.5, 0)
        dst.write(z, 1)
    meta = _run(request_for(tmp_path, after, before))
    assert meta["alignment"]["vertical"]["offset_removed_m"] == pytest.approx(0.0, abs=0.02)
    assert meta["alignment"]["vertical"]["second_peak_offset_m"] == pytest.approx(0.5, abs=0.05)
    assert meta["reliability"]["grade"] == "low"
    assert any("two height offsets" in r for r in meta["reliability"]["reasons"])
    # With the stable half identified the platform is measured as fill.
    assert meta["change"]["fill_area_m2"] > 0.35 * meta["change"]["compared_area_m2"]


def test_two_surveys_of_an_unchanged_site_report_nothing(tmp_path):
    before = survey(tmp_path, "a", res=0.5, seed=11)
    after = survey(tmp_path, "b", res=0.5, seed=12, offset=-1.4, gain=0.8)
    request = request_for(tmp_path, after, before)
    meta = _run(request)
    assert _features(request) == []
    change = meta["change"]
    assert change["changed_share"] == 0 and change["fill_volume_m3"] == 0 and change["cut_volume_m3"] == 0
    assert change["vegetation_area_m2"] == 0 and change["appearance_area_m2"] == 0
    assert meta["alignment"]["vertical"]["offset_removed_m"] == pytest.approx(-1.4, abs=0.005)
    assert meta["reliability"]["grade"] == "high"
    assert "balanced" in {r["label"]: r["value"] for r in meta["summary"]}["Net"]


def test_noise_alone_stays_below_the_level_of_detection(tmp_path):
    # Three times noisier surfaces: the bar rises with the measured noise instead of flooding.
    before = survey(tmp_path, "a", res=0.5, seed=21, noise=0.06, with_ortho=False)
    after = survey(tmp_path, "b", res=0.5, seed=22, noise=0.06, with_ortho=False)
    meta = _run(request_for(tmp_path, after, before))
    assert meta["detection"]["limited_by"] == "noise" and meta["detection"]["level_of_detection_m"] > 0.12
    assert meta["change"]["changed_share"] < 0.001


def test_mixing_dsm_and_dtm_is_called_out(pair, tmp_path):
    context = {"task": {"name": "a"}, "reference_task": {"name": "b"},
               "inputs": {"surface": {"dataset": "dsm"}, "reference_surface": {"dataset": "dtm"}}}
    meta = _run(request_for(tmp_path, pair["after"], pair["before"], context=context, orthos=False))
    assert meta["surface_kinds"] == {"this_survey": "dsm", "comparison_survey": "dtm"}
    assert any("DSM is compared with the comparison survey's DTM" in w for w in meta["warnings"])
    assert meta["reliability"]["grade"] == "low"


# --- inputs ------------------------------------------------------------------

def test_without_orthophotos_trees_count_as_fill_and_the_run_says_so(pair, tmp_path):
    meta = _run(request_for(tmp_path, pair["after"], pair["before"], orthos=False))
    assert meta["photos"] == {"used": False, "vegetation_separated": False, "appearance": None}
    assert meta["change"]["fill_volume_m3"] == pytest.approx(FILL_M3 + 675, rel=0.03)
    assert any("vegetation could not be separated" in w for w in meta["warnings"])
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.02)


def test_a_broken_orthophoto_never_blocks_the_elevation_result(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"])
    broken = tmp_path / "broken.tif"
    broken.write_bytes(b"not a tiff")
    request["inputs"]["reference_orthophoto"] = str(broken)
    meta = _run(request)
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.02)
    assert any("not a readable raster" in w and "continuing without it" in w for w in meta["warnings"])
    # One orthophoto is still enough to find the trees; appearance needs both.
    assert meta["photos"]["vegetation_separated"] and meta["photos"]["appearance"] is None
    assert meta["change"]["fill_volume_m3"] == pytest.approx(FILL_M3, rel=0.03)


def test_photo_stage_failure_is_a_warning_not_an_error(pair, tmp_path, monkeypatch):
    from cdplugin import pipeline

    def boom(*a, **k):
        raise RuntimeError("decoder exploded")
    monkeypatch.setattr(pipeline, "read_rgb", boom)
    meta = _run(request_for(tmp_path, pair["after"], pair["before"]))
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.02)
    assert any("decoder exploded" in w for w in meta["warnings"])
    assert meta["photos"]["used"] is False


def test_photos_only_gives_an_appearance_result_without_volumes(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"])
    request["inputs"] = {k: v for k, v in request["inputs"].items() if "orthophoto" in k}
    meta = _run(request)
    assert meta["mode"] == "appearance-only" and meta["alignment"] is None and meta["detection"] is None
    features = _features(request)
    assert features and {f["properties"]["class"] for f in features} == {"appearance"}
    # The resurfaced square is found; so are the pit/stockpile only if they look different (they do not here).
    assert any(f["properties"]["area"] == pytest.approx(144, rel=0.15) for f in features)
    assert meta["change"]["fill_volume_m3"] == 0 and meta["reliability"]["grade"] == "low"
    assert any("only the orthophotos are compared" in w for w in meta["warnings"])
    rows = {r["label"]: r["value"] for r in meta["summary"]}
    assert rows["Volumes"] == "not available without elevation surfaces"


def test_nothing_comparable_is_a_clear_error(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"])
    request["inputs"] = {"surface": pair["after"]["dsm"], "reference_orthophoto": pair["before"]["orthophoto"]}
    with pytest.raises(InputError, match="Nothing to compare"):
        _run(request)
    request["inputs"] = {}
    with pytest.raises(InputError, match="Nothing to compare"):
        _run(request)


def test_surveys_of_different_places_are_refused(tmp_path):
    a = survey(tmp_path, "a", res=0.5, extent=(0.0, 0.0, 80.0, 80.0), with_ortho=False)
    b = survey(tmp_path, "b", res=0.5, extent=(110.0, 0.0, 190.0, 80.0), with_ortho=False)
    with pytest.raises(InputError, match="do not overlap"):
        _run(request_for(tmp_path, a, b))


def test_the_same_survey_twice_is_refused(pair, tmp_path):
    with pytest.raises(InputError, match="identical"):
        _run(request_for(tmp_path, pair["after"], pair["after"], orthos=False))


def test_bad_parameter_is_refused_before_any_work(pair, tmp_path):
    with pytest.raises(ParameterError, match="confidence"):
        _run(request_for(tmp_path, pair["after"], pair["before"], params={"confidence": "50"}))


def test_unknown_parameters_are_ignored_with_a_warning(tmp_path):
    before, after = _coarse_pair(tmp_path)
    meta = _run(request_for(tmp_path, after, before, params={"colour": "pink"}, orthos=False))
    assert any("ignoring unknown parameters: colour" in w for w in meta["warnings"])


# --- different grids, CRSs and sizes ---------------------------------------------

def test_survey_in_another_coordinate_system(tmp_path):
    before, after = _coarse_pair(tmp_path)
    other = {"dsm": reproject_to(before["dsm"], str(tmp_path / "sep_utm34.tif"), "EPSG:32634")}
    meta = _run(request_for(tmp_path, after, other, orthos=False))
    assert meta["grid"]["epsg"] == 32633          # the survey the run belongs to sets the CRS
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.05)
    assert meta["alignment"]["vertical"]["offset_removed_m"] == pytest.approx(0.83, abs=0.01)
    # ...and the other way round: result in the other survey's zone, same volumes.
    meta = _run(request_for(tmp_path, other, after, params={"reference_role": "after"}, orthos=False,
                            output="other.geojson"))
    assert meta["grid"]["epsg"] == 32634
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.05)


def test_volumes_do_not_depend_on_the_comparison_resolution(pair, tmp_path):
    fine = _run(request_for(tmp_path, pair["after"], pair["before"], orthos=False, output="fine.geojson"))
    coarse = _run(request_for(tmp_path, pair["after"], pair["before"], orthos=False, output="coarse.geojson",
                              params={"resolution_m": 1.0}))
    assert coarse["grid"]["resolution_m"] == 1.0
    assert coarse["change"]["cut_volume_m3"] == pytest.approx(fine["change"]["cut_volume_m3"], rel=0.03)
    assert coarse["change"]["fill_volume_m3"] == pytest.approx(fine["change"]["fill_volume_m3"], rel=0.03)


def test_oversized_site_is_coarsened_automatically(pair, tmp_path):
    meta = _run(request_for(tmp_path, pair["after"], pair["before"], orthos=False), max_pixels=60_000)
    assert meta["grid"]["coarsened_to_fit_memory"] and meta["grid"]["pixels"] <= 60_000
    assert any("megapixel budget" in w for w in meta["warnings"])
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.05)


def test_min_area_and_min_height_are_respected(pair, tmp_path):
    meta = _run(request_for(tmp_path, pair["after"], pair["before"], orthos=False,
                            params={"min_area_m2": 100, "min_height_change_m": 1.7}))
    # Only the 2 m pit and (no orthophoto here) the 3 m of tree growth are both
    # larger than 100 m² and taller than 1.7 m; the 1.5 m stockpile is not.
    assert meta["change"]["cut_regions"] == 1 and meta["change"]["fill_regions"] == 1
    assert meta["change"]["fill_volume_m3"] == pytest.approx(675, rel=0.05)
    assert meta["detection"]["level_of_detection_m"] == 1.7 and meta["detection"]["min_region_area_m2"] == 100
    small = _run(request_for(tmp_path, pair["after"], pair["before"], orthos=False, output="small.geojson",
                             params={"min_area_m2": 0.5}))
    # With the area filter nearly off the 1 m² speck (and little else) appears.
    assert small["change"]["fill_regions"] >= 2


# --- raster package ------------------------------------------------------------

def test_raster_output_is_the_height_difference(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"], output="diff.tif")
    meta = _run(request)
    with rasterio.open(request["output_path"]) as ds:
        assert ds.count == 1 and ds.dtypes[0] == "float32" and ds.nodata == -9999.0
        assert ds.crs.to_epsg() == 32633 and ds.res == (0.25, 0.25)
        tags = ds.tags()
        data = ds.read(1, masked=True)
        pit = data[ds.index(ORIGIN[0] + 45, ORIGIN[1] + 40)]
        pile = data[ds.index(ORIGIN[0] + 110, ORIGIN[1] + 50)]
        quiet = data[ds.index(ORIGIN[0] + 180, ORIGIN[1] + 140)]
        paving = data[ds.index(ORIGIN[0] + 116, ORIGIN[1] + 106)]
    assert pit == pytest.approx(-2.0, abs=0.1) and pile == pytest.approx(1.5, abs=0.1)
    assert quiet is np.ma.masked and paving is np.ma.masked      # appearance has no height change to show
    assert float(tags["vertical_offset_removed_m"]) == pytest.approx(0.83, abs=0.005)
    assert tags["content"] == "significant change only"
    # Fill, cut and the vegetation growth (3 m over 225 m²) are all in the raster.
    volume = float(np.ma.sum(data)) * 0.0625
    assert volume == pytest.approx(meta["change"]["net_volume_m3"] + 675, abs=15)


def test_raster_output_can_carry_the_full_difference(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"], output="full.tif", orthos=False,
                          params={"mask_insignificant": False})
    _run(request)
    with rasterio.open(request["output_path"]) as ds:
        data = ds.read(1, masked=True)
    assert data.count() > 0.9 * data.size
    assert abs(float(np.ma.median(data))) < 0.01                 # unchanged ground sits at zero


def test_raster_output_needs_elevation(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"], output="x.tif")
    request["inputs"] = {k: v for k, v in request["inputs"].items() if "orthophoto" in k}
    with pytest.raises(InputError, match="needs an elevation surface"):
        _run(request)


# --- the entrypoint, as the sandbox runs it ----------------------------------------

def test_entrypoint_writes_output_result_and_progress(pair, tmp_path):
    request = request_for(tmp_path, pair["after"], pair["before"], orthos=False)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    res = subprocess.run([sys.executable, os.path.join(PLUGIN_DIR, "main.py"), str(path)],
                         capture_output=True, text=True, cwd=PLUGIN_DIR)
    assert res.returncode == 0, res.stderr
    assert "[change-detection" in res.stderr and "finished" in res.stderr
    with open(request["result_path"], encoding="utf-8") as f:
        meta = json.load(f)["metadata"]
    assert meta["change"]["cut_volume_m3"] == pytest.approx(CUT_M3, rel=0.02)
    assert os.path.getsize(request["output_path"]) > 0


def test_entrypoint_reports_expected_problems_in_one_line(tmp_path):
    a = survey(tmp_path, "a", res=0.5, extent=(0.0, 0.0, 80.0, 80.0), with_ortho=False)
    b = survey(tmp_path, "b", res=0.5, extent=(110.0, 0.0, 190.0, 80.0), with_ortho=False)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request_for(tmp_path, a, b)))
    res = subprocess.run([sys.executable, os.path.join(PLUGIN_DIR, "main.py"), str(path)],
                         capture_output=True, text=True, cwd=PLUGIN_DIR)
    assert res.returncode == 1
    assert "error: The two surveys do not overlap" in res.stderr and "Traceback" not in res.stderr
    assert not os.path.exists(tmp_path / "result.json")
