import json
import os

import pytest

from detplugin.errors import ModelError
from detplugin.registry import default_model_id, load_cards, select_model, validate_card
from tests.conftest import CARDS, SHIPPED_MODELS, install_models


def _card(**over):
    data = {"id": "model", "file": "m.onnx", "classes": ["a", "b"]}
    data.update(over)
    return data


def test_shipped_cards_are_valid_and_fetchable():
    cards = load_cards(SHIPPED_MODELS, require_files=False)
    assert set(cards) == {"deepforest-tree-crowns", "visdrone-yolov11s"}
    for card in cards.values():
        assert card.source.get("url", "").startswith("https://") and card.sha256, card.id
        assert card.license in ("MIT", "AGPL-3.0")
        assert card.recommended.get("tile_size") and card.recommended.get("confidence")
    assert default_model_id(cards) == "deepforest-tree-crowns"
    assert cards["visdrone-yolov11s"].license == "AGPL-3.0"  # never committed, fetched on purpose


def test_load_cards_marks_missing_weights_unavailable(models_dir):
    cards = load_cards(models_dir)
    assert "missing" in cards.unavailable and "missing" not in cards
    assert "not in the package" in cards.unavailable["missing"]
    assert "yolo80" in cards and cards["yolo80"].default


def test_select_model_default_and_explicit(models_dir):
    cards = load_cards(models_dir)
    assert select_model(cards, "").id == "yolo80"
    assert select_model(cards, None).id == "yolo80"
    assert select_model(cards, "tv-tree").id == "tv-tree"


def test_select_model_errors_are_actionable(models_dir):
    cards = load_cards(models_dir)
    with pytest.raises(ModelError, match="unavailable.*not in the package"):
        select_model(cards, "missing")
    with pytest.raises(ModelError, match="Unknown model 'nope'.*yolo80"):
        select_model(cards, "nope")


def test_default_falls_back_to_first_id_alphabetically(tmp_path):
    d = install_models(tmp_path / "m", ["silent", "tv-tree"])
    assert default_model_id(load_cards(d)) == "silent"


def test_two_defaults_are_rejected(tmp_path):
    d = install_models(tmp_path / "m", ["yolo80"])
    second = dict(CARDS["silent"], default=True)
    (tmp_path / "m" / "silent.json").write_text(json.dumps(second))
    with pytest.raises(ModelError, match="more than one model card is marked default"):
        load_cards(d)


def test_duplicate_ids_and_bad_json_rejected(tmp_path):
    d = install_models(tmp_path / "m", ["yolo80"])
    (tmp_path / "m" / "copy.json").write_text(json.dumps(CARDS["yolo80"]))
    with pytest.raises(ModelError, match="duplicate model id"):
        load_cards(d)
    os.remove(tmp_path / "m" / "copy.json")
    (tmp_path / "m" / "broken.json").write_text("{not json")
    with pytest.raises(ModelError, match="not valid JSON"):
        load_cards(d)


def test_missing_models_dir_is_a_model_error(tmp_path):
    with pytest.raises(ModelError, match="models directory not found"):
        load_cards(str(tmp_path / "nowhere"))


@pytest.mark.parametrize("bad, message", [
    (_card(id="Bad Id"), "'id' must be a lowercase slug"),
    (_card(file="sub/m.onnx"), "'file' must be the name"),
    (_card(file=""), "'file' must be the name"),
    (_card(sha256="abc"), "'sha256' must be a 64-character"),
    (_card(family="detr"), "'family' must be one of"),
    (_card(normalize="zscore"), "'normalize' must be one of"),
    (_card(classes=[]), "'classes' must be a non-empty list"),
    (_card(classes=["a", "a"]), "class names must be unique"),
    (_card(classes=["a", ""]), "class names must be unique non-empty"),
    (_card(label_offset=-1), "'label_offset' must be an integer"),
    (_card(label_offset=True), "'label_offset' must be an integer"),
    (_card(recommended={"threshold": 0.5}), "recommended.threshold is not a parameter"),
    (_card(recommended={"confidence": "high"}), "recommended.confidence must be a number"),
    (_card(colors={"zzz": "#000000"}), "colors refers to unknown class"),
    (_card(colors={"a": "red"}), "color for 'a' must be"),
    (_card(default="yes"), "'default' must be true or false"),
    (_card(source="me"), "'source' must be an object"),
    ("not a dict", "model card must be a JSON object"),
])
def test_validate_card_messages(bad, message):
    with pytest.raises(ModelError, match=message):
        validate_card(bad, "/models/m.json")


def test_card_helpers():
    card = validate_card(_card(colors={"a": "#112233"}, label_offset=1, family="torchvision"), "/models/m.json")
    assert card.class_name(0) == "a" and card.class_name(2) is None and card.class_name(-1) is None
    assert card.legend() == [{"name": "a", "color": "#112233"}, {"name": "b", "color": None}]
    assert card.normalization_for("torchvision") == "imagenet"
    assert validate_card(_card(normalize="unit", family="torchvision"), "/m.json").normalization_for("torchvision") == "unit"
    assert card.summary()["label_offset"] == 1 and card.model_path == "/models/m.onnx"
