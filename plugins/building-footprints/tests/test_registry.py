import json
import os

import pytest

from bfplugin.errors import ModelError
from bfplugin.registry import default_model_id, load_cards, select_model, validate_card
from tests.conftest import CARDS, SHIPPED_MODELS, install_models


def test_shipped_card_is_valid_and_fetchable():
    cards = load_cards(SHIPPED_MODELS, require_files=False)
    card = cards["hot-dinov3s-buildings"]
    assert card.default and card.license == "Apache-2.0"
    assert card.source["url"].startswith("https://huggingface.co/") and len(card.sha256) == 64
    assert card.onnx["mean"] and card.onnx["std"] and card.input_size == 256
    assert card.output("distance")["activation"] == "tanh" and card.output("mask")["activation"] == "sigmoid"
    assert card.recommended["resolution_m"] == pytest.approx(0.25)


def test_missing_weights_are_unavailable_not_fatal(tmp_path):
    cards = load_cards(install_models(tmp_path / "m", ["tiny", "missing"]))
    assert "tiny" in cards and "missing" in cards.unavailable
    assert "tools/fetch_models.py" in cards.unavailable["missing"]
    with pytest.raises(ModelError, match="unavailable"):
        select_model(cards, "missing")


def test_default_and_auto_selection(tmp_path):
    cards = load_cards(install_models(tmp_path / "m", ["tiny", "badshape"]))
    assert default_model_id(cards) == "tiny"
    assert select_model(cards, "auto").id == "tiny" and select_model(cards, "").id == "tiny"
    with pytest.raises(ModelError, match="Unknown model 'nope'"):
        select_model(cards, "nope")


def test_two_defaults_is_an_error(tmp_path):
    d = install_models(tmp_path / "m", ["tiny", "badshape"])
    data = dict(CARDS["badshape"], default=True)
    json.dump(data, open(os.path.join(d, "badshape.json"), "w"))
    with pytest.raises(ModelError, match="more than one"):
        load_cards(d)


@pytest.mark.parametrize("patch, message", [
    ({"id": "Bad Id"}, "lowercase slug"),
    ({"file": "../x.onnx"}, "inside models/"),
    ({"sha256": "abc"}, "64-character"),
    ({"class": {"name": "b", "color": "red"}}, "#rrggbb"),
    ({"onnx": {"input_size": 7}}, "input_size"),
    ({"onnx": {"channels": ["red", "green"]}}, "permutation"),
    ({"onnx": {"mean": [1, 2, 3], "std": None}}, "given together"),
    ({"onnx": {"outputs": {"mask": {"channel": 0}}}}, "'distance'"),
    ({"onnx": {"outputs": {"mask": {"channel": 0}, "distance": {"channel": 0}}}}, "used twice"),
    ({"onnx": {"outputs": {"mask": {"channel": 0, "activation": "relu"}, "distance": {"channel": 2}}}}, "activation"),
    ({"recommended": {"bogus": 1, "resolution_m": 0.2}}, "not a parameter"),
    ({"recommended": {"tile_size": 256}}, "resolution_m"),
    ({"recommended": {"resolution_m": 0.2, "confidence": 1.0}}, "confidence"),
])
def test_invalid_cards_name_the_problem(patch, message):
    data = json.loads(json.dumps(CARDS["tiny"]))
    for key, value in patch.items():
        if isinstance(value, dict) and key == "onnx":
            data["onnx"] = {**data["onnx"], **value}
        else:
            data[key] = value
    with pytest.raises(ModelError, match=message):
        validate_card(data, "/x/tiny.json")
