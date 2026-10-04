import pytest

from bfplugin.errors import ParameterError
from bfplugin.params import Params
from bfplugin.registry import validate_card
from tests.conftest import CARDS


def _card():
    return validate_card(CARDS["tiny"], "/x/tiny.json")


def test_defaults_resolve_to_card_values():
    eff = Params.from_request({}).effective(_card())
    rec = CARDS["tiny"]["recommended"]
    assert eff == {k: rec[k] for k in eff}


def test_explicit_values_win_and_zero_is_meaningful_where_minus_one_is_the_sentinel():
    p = Params.from_request({"resolution_m": 0.5, "overlap": 0, "min_area_m2": 0, "simplify_m": 0,
                             "regularize_overlap_tol_m2": 0, "confidence": 0.7})
    eff = p.effective(_card())
    assert eff["resolution_m"] == 0.5 and eff["overlap"] == 0 and eff["min_area_m2"] == 0
    assert eff["simplify_m"] == 0 and eff["regularize_overlap_tol_m2"] == 0 and eff["confidence"] == 0.7


def test_auto_strings_and_nulls_fall_back_to_the_card():
    p = Params.from_request({"model": "auto", "resolution_m": "auto", "confidence": "", "overlap": None,
                             "h_maxima_depth": "AUTO"})
    assert p.model == "auto"
    eff = p.effective(_card())
    assert eff["resolution_m"] == 0.2 and eff["confidence"] == 0.5 and eff["overlap"] == 64


def test_unknown_parameters_are_kept_aside_and_strings_coerced():
    p = Params.from_request({"resolution_m": "0.3", "seed_min_distance": "7", "bogus": 1})
    assert p.resolution_m == 0.3 and p.seed_min_distance == 7 and p.unknown == {"bogus": 1}


@pytest.mark.parametrize("raw", [
    {"resolution_m": -1}, {"tile_size": 10}, {"overlap": -2}, {"confidence": 1.5}, {"seed_min_distance": -1},
    {"h_maxima_depth": 3}, {"min_area_m2": -2}, {"regularize_area_threshold": 2}, {"resolution_m": "fast"},
])
def test_out_of_range_values_are_rejected(raw):
    with pytest.raises(ParameterError):
        Params.from_request(raw)


def test_missing_recommendation_is_an_actionable_error():
    data = dict(CARDS["tiny"])
    data["recommended"] = {"resolution_m": 0.2}
    card = validate_card(data, "/x/tiny.json")
    with pytest.raises(ParameterError, match="has no recommended\\.(overlap|confidence)"):
        Params.from_request({}).effective(card)
