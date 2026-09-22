import json

import pytest

from segplugin import registry
from segplugin.errors import InputError, ModelError
from segplugin.pipeline import MODELS_DIR


@pytest.fixture
def cards():
    return registry.load_cards(MODELS_DIR)


ALL_IDS = {"flair-rgb-resnet34-unet", "landcover-segformer-b0", "height-classes", "geomorphons"}


def test_shipped_cards_load_and_cover_all_input_modes(cards):
    # The FLAIR weights are fetched, not committed; either way the RGB modes
    # resolve to an ONNX land-cover model.
    assert set(cards) | set(cards.unavailable) == ALL_IDS
    rgb_model = "flair-rgb-resnet34-unet" if "flair-rgb-resnet34-unet" in cards else "landcover-segformer-b0"
    auto = lambda sel: registry.select_model(cards, "auto", set(sel)).id
    assert auto(["orthophoto"]) == rgb_model
    assert auto(["orthophoto", "dsm", "dtm"]) == rgb_model
    assert auto(["dsm"]) == "height-classes"
    assert auto(["dsm", "dtm"]) == "height-classes"
    assert auto(["dtm"]) == "geomorphons"


def test_explicit_model_incompatible_inputs_is_a_clear_error(cards):
    with pytest.raises(InputError, match="needs orthophoto"):
        registry.select_model(cards, "landcover-segformer-b0", {"dsm"})
    with pytest.raises(InputError, match="needs dsm"):
        registry.select_model(cards, "height-classes", {"dtm", "orthophoto"})
    with pytest.raises(ModelError, match="Unknown model"):
        registry.select_model(cards, "nope", {"dsm"})
    with pytest.raises(InputError, match="No model can run"):
        registry.select_model(cards, "auto", set())


def test_used_and_ignored_inputs(cards):
    used, ignored = cards["geomorphons"].used_inputs({"dtm", "dsm", "orthophoto"})
    assert used == ["dtm"] and ignored == ["orthophoto", "dsm"]
    used, ignored = cards["landcover-segformer-b0"].used_inputs({"orthophoto", "dsm"})
    assert used == ["orthophoto", "dsm"] and ignored == []


def _card(**over):
    base = {
        "id": "x-model", "backend": "height",
        "inputs": {"required": ["dsm"]},
        "classes": [{"id": 0, "name": "a", "color": "#000000"}],
    }
    base.update(over)
    return base


@pytest.mark.parametrize("bad, fragment", [
    (_card(id="Bad"), "'id'"),
    (_card(backend="torch"), "'backend'"),
    (_card(inputs={"required": ["lidar"]}), "task datasets"),
    (_card(inputs={"optional": ["dsm"]}), "at least one"),
    (_card(classes=[]), "'classes'"),
    (_card(classes=[{"id": 300, "name": "a", "color": "#000000"}]), "0-254"),
    (_card(classes=[{"id": 0, "name": "a", "color": "red"}]), "#rrggbb"),
    (_card(backend="onnx"), "'onnx' section"),
    (_card(backend="onnx", onnx={"file": "../x.onnx"}), "inside models/"),
    (_card(height_fusion={"classes": {"zzz": "elevated"}}), "unknown class"),
])
def test_card_validation(bad, fragment):
    with pytest.raises(ModelError, match=fragment):
        registry.validate_card(bad, "/models/x.json")


def test_onnx_card_without_weights_is_skipped_not_fatal(tmp_path):
    import shutil
    models = tmp_path / "models"
    shutil.copytree(MODELS_DIR, models, ignore=shutil.ignore_patterns("*.onnx"))
    cards = registry.load_cards(str(models))
    assert set(cards) == {"height-classes", "geomorphons"}
    assert set(cards.unavailable) == {"flair-rgb-resnet34-unet", "landcover-segformer-b0"}
    with pytest.raises(ModelError, match="unavailable.*fetch_models"):
        registry.select_model(cards, "flair-rgb-resnet34-unet", {"orthophoto"})
    with pytest.raises(InputError, match="No model can run"):
        registry.select_model(cards, "auto", {"orthophoto"})


def test_loading_a_directory_with_a_broken_card_fails_loudly(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(ModelError, match="not valid JSON"):
        registry.load_cards(str(tmp_path))
    (tmp_path / "bad.json").write_text(json.dumps(_card()))
    (tmp_path / "dup.json").write_text(json.dumps(_card()))
    with pytest.raises(ModelError, match="duplicate model id"):
        registry.load_cards(str(tmp_path))
