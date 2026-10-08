"""Parameter parsing and validation."""

import pytest

from cdplugin.errors import ParameterError
from cdplugin.params import CONFIDENCE_K, Params


def test_defaults():
    p = Params.from_request(None)
    assert (p.reference_role, p.vertical_alignment, p.horizontal_alignment) == ("before", "auto", "auto")
    assert p.confidence == "95" and p.k == pytest.approx(1.96)
    assert p.min_height_change_m == 0 and p.min_area_m2 == 4 and p.registration_tolerance_m == -1
    assert p.vegetation_filter and p.appearance_change and p.mask_insignificant
    assert p.unknown == {}


def test_values_are_typed_and_normalised():
    p = Params.from_request({
        "reference_role": "After", "vertical_alignment": "TILT", "horizontal_alignment": "off",
        "confidence": "99%", "min_height_change_m": "0.2", "min_area_m2": 10, "resolution_m": 0.5,
        "vegetation_filter": "false", "appearance_change": 0, "max_shift_m": 5,
    })
    assert (p.reference_role, p.vertical_alignment, p.horizontal_alignment) == ("after", "tilt", "off")
    assert p.confidence == "99" and p.k == CONFIDENCE_K["99"]
    assert p.min_height_change_m == 0.2 and p.min_area_m2 == 10.0 and p.resolution_m == 0.5
    assert p.vegetation_filter is False and p.appearance_change is False and p.max_shift_m == 5.0


def test_empty_none_and_auto_keep_the_default():
    p = Params.from_request({"confidence": "", "min_area_m2": None, "resolution_m": "auto",
                             "reference_role": " ", "vertical_alignment": "auto"})
    assert p.confidence == "95" and p.min_area_m2 == 4 and p.resolution_m == 0 and p.reference_role == "before"
    assert p.vertical_alignment == "auto"


def test_unknown_parameters_are_kept_aside():
    p = Params.from_request({"min_area_m2": 2, "colour": "pink"})
    assert p.unknown == {"colour": "pink"} and "unknown" not in p.raw()


@pytest.mark.parametrize("bad", [
    {"reference_role": "middle"}, {"vertical_alignment": "plane"}, {"horizontal_alignment": "on"},
    {"confidence": "50"}, {"min_height_change_m": -1}, {"min_area_m2": -0.1},
    {"registration_tolerance_m": -0.5}, {"edge_buffer_m": -1}, {"resolution_m": -2},
    {"max_shift_m": 50}, {"min_area_m2": "big"}, {"vegetation_filter": "maybe"},
])
def test_invalid_values_are_refused(bad):
    with pytest.raises(ParameterError):
        Params.from_request(bad)
