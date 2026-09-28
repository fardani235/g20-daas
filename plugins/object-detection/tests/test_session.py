import pytest

from detplugin.errors import ModelError
from detplugin.registry import load_cards
from detplugin.session import inspect_session, load_session, sha256_of, verify_checksum


@pytest.fixture
def cards(models_dir):
    return load_cards(models_dir)


def test_yolo_family_is_inferred_and_classes_checked(cards):
    card = cards["yolo80"]
    spec = inspect_session(load_session(card), card)
    assert spec.family == "yolo" and spec.num_classes == 80
    assert spec.input_name == "images" and spec.output_names == ["output0"]
    assert spec.input_size == (640, 640) and spec.has_batch_dim and spec.normalize == "unit"
    assert spec.describe()["input_size"] == [640, 640]


def test_torchvision_family_without_batch_dim(cards):
    card = cards["tv-tree"]
    spec = inspect_session(load_session(card), card)
    assert spec.family == "torchvision" and spec.has_batch_dim is False
    assert spec.output_names == ["boxes", "scores", "labels"] and spec.input_size == (256, 256)
    assert spec.normalize == "imagenet" and spec.num_classes is None


def test_auto_family_picks_torchvision_outputs(cards):
    card = cards["tv-1based"]
    assert inspect_session(load_session(card), card).family == "torchvision"


def test_class_count_mismatch_names_both_numbers(cards):
    card = cards["sixcls"]
    with pytest.raises(ModelError, match="predicts 6 classes but its card lists 80"):
        inspect_session(load_session(card), card)


def test_unrecognised_model_is_explained(cards):
    card = cards["badrank"]
    with pytest.raises(ModelError, match="not a supported detector.*output0\\[1, 84\\]"):
        inspect_session(load_session(card), card)


def test_declared_family_must_match_outputs(cards):
    card = cards["wrongfamily"]
    with pytest.raises(ModelError, match="declared family 'torchvision' but has no boxes"):
        inspect_session(load_session(card), card)


def test_checksum_mismatch_is_a_clear_error(cards):
    card = cards["badsum"]
    with pytest.raises(ModelError, match="does not match the checksum in its card"):
        verify_checksum(card)
    verify_checksum(cards["yolo80"])  # no sha256 in the card: nothing to verify
    assert len(sha256_of(card.model_path)) == 64


def test_unloadable_file_is_a_model_error(cards, models_dir):
    card = cards["yolo80"]
    with open(card.model_path, "wb") as f:
        f.write(b"not an onnx file")
    with pytest.raises(ModelError, match="cannot load model models/tiny_yolo.onnx"):
        load_session(card)
