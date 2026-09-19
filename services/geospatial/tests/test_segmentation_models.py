import shutil
from pathlib import Path

import pytest

from app.analysis.segmentation import models

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _models_dir(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setenv(models.MODELS_DIR_ENV, str(d))
    return d


def _install(model_file, models_dir, labels):
    shutil.copy(FIXTURES / model_file, models_dir / model_file)
    (models_dir / "labels.txt").write_text("\n".join(labels) + "\n")


def test_resolve_accepts_bare_filename(_models_dir):
    (_models_dir / "model.onnx").write_bytes(b"x")
    resolved = models.resolve_asset("model.onnx")
    assert resolved == str((_models_dir / "model.onnx").resolve())


@pytest.mark.parametrize(
    "bad", ["../secret", "/etc/passwd", "a/b.onnx", "..", ".", "", "sub/../x"]
)
def test_resolve_rejects_traversal_and_paths(_models_dir, bad):
    with pytest.raises(models.ModelError):
        models.resolve_asset(bad)


def test_resolve_rejects_symlink_escape(tmp_path, _models_dir):
    secret = tmp_path / "secret.onnx"
    secret.write_bytes(b"s")
    (_models_dir / "link").symlink_to(secret)
    with pytest.raises(models.ModelError):
        models.resolve_asset("link")


def test_inspect_multiclass_model(_models_dir):
    _install("tiny_segmenter.onnx", _models_dir, ["background", "building"])
    session = models.load_session("tiny_segmenter.onnx")
    spec = models.inspect_session(session, models.read_labels("labels.txt"))
    assert spec.mode == "multiclass"
    assert spec.num_classes == 2
    assert spec.input_name == "images"
    assert spec.output_name == "masks"
    assert spec.input_size == (64, 64)
    assert spec.has_batch_dim is True


def test_inspect_binary_model(_models_dir):
    _install("tiny_segmenter_binary.onnx", _models_dir, ["background", "building"])
    session = models.load_session("tiny_segmenter_binary.onnx")
    spec = models.inspect_session(session, models.read_labels("labels.txt"))
    assert spec.mode == "binary"
    assert spec.num_classes == 1
    assert spec.foreground_index == 1


def test_inspect_binary_single_label(_models_dir):
    _install("tiny_segmenter_binary.onnx", _models_dir, ["building"])
    session = models.load_session("tiny_segmenter_binary.onnx")
    spec = models.inspect_session(session, models.read_labels("labels.txt"))
    assert spec.mode == "binary"
    assert spec.foreground_index == 0


def test_inspect_rejects_badrank_output(_models_dir):
    _install("tiny_segmenter_badrank.onnx", _models_dir, ["background", "building"])
    session = models.load_session("tiny_segmenter_badrank.onnx")
    with pytest.raises(models.ModelError):
        models.inspect_session(session, models.read_labels("labels.txt"))


def test_inspect_rejects_label_mismatch(_models_dir):
    _install("tiny_segmenter.onnx", _models_dir, ["a", "b", "c"])
    session = models.load_session("tiny_segmenter.onnx")
    with pytest.raises(models.ModelError):
        models.inspect_session(session, models.read_labels("labels.txt"))


def test_validate_session_ok(_models_dir):
    _install("tiny_segmenter.onnx", _models_dir, ["background", "building"])
    session = models.load_session("tiny_segmenter.onnx")
    spec = models.validate_session(session, models.read_labels("labels.txt"))
    assert spec.num_classes == 2


def test_load_missing_model(_models_dir):
    with pytest.raises(models.ModelError):
        models.load_session("nope.onnx")
