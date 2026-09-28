import pytest

from detplugin.errors import ParameterError
from detplugin.params import Params
from detplugin.registry import ModelCard


def _card(recommended=None):
    return ModelCard({"id": "m", "file": "m.onnx", "classes": ["a"], "recommended": recommended or {}}, "/x/m.json")


def test_defaults_mean_model_recommended():
    p = Params.from_request({})
    assert (p.model, p.confidence, p.iou, p.tile_size, p.overlap) == ("", 0.0, 0.0, 0, -1)
    eff = p.effective(_card({"confidence": 0.4, "iou": 0.5, "tile_size": 512, "overlap": 96}), (640, 640))
    assert eff == {"confidence": 0.4, "iou": 0.5, "tile_size": 512, "overlap": 96,
                   "tile_size_m": None, "overlap_m": None, "max_detections": 5000}


def test_run_values_override_recommended_and_fallbacks_apply():
    p = Params.from_request({"confidence": "0.7", "tile_size": 320, "overlap": 0, "tile_size_m": 20, "overlap_m": 0})
    eff = p.effective(_card({"confidence": 0.4}), (256, 256))
    assert eff["confidence"] == 0.7 and eff["iou"] == 0.45   # run value; fallback (card says nothing)
    assert eff["tile_size"] == 320 and eff["overlap"] == 0     # 0 overlap is a real value, not "default"
    assert eff["tile_size_m"] == 20.0 and eff["overlap_m"] == 0.0


def test_tile_size_falls_back_to_model_input():
    assert Params.from_request({}).effective(_card(), (512, 384))["tile_size"] == 512


def test_unknown_parameters_are_collected_not_fatal():
    p = Params.from_request({"foo": 1, "confidence": 0.5})
    assert p.unknown == {"foo": 1} and p.confidence == 0.5


@pytest.mark.parametrize("bad", [
    {"confidence": 1.5}, {"iou": -0.1}, {"tile_size": 32}, {"tile_size": 5000}, {"overlap": -2},
    {"tile_size_m": -1}, {"overlap_m": -2}, {"tile_size_m": 10, "overlap_m": 10}, {"max_detections": 0},
    {"confidence": "high"},
])
def test_invalid_values_are_parameter_errors(bad):
    with pytest.raises(ParameterError):
        Params.from_request(bad)


def test_class_filter_accepts_commas_semicolons_and_newlines():
    p = Params.from_request({"class_filter": "car, truck;bus\nvan,,"})
    assert p.class_filter_names() == ["car", "truck", "bus", "van"]
