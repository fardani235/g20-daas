import shutil
from pathlib import Path

import pytest

from app.analysis.detection import models

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _models_dir(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setenv(models.MODELS_DIR_ENV, str(d))
    return d


def test_resolve_accepts_bare_filename(_models_dir):
    (_models_dir / "detector.onnx").write_bytes(b"x")
    resolved = models.resolve_asset("detector.onnx")
    assert resolved == str((_models_dir / "detector.onnx").resolve())


@pytest.mark.parametrize("bad", ["../secret", "/etc/passwd", "a/b.onnx", "..", ".", "", "sub/../x"])
def test_resolve_rejects_traversal_and_paths(_models_dir, bad):
    with pytest.raises(models.ModelError):
        models.resolve_asset(bad)


def test_resolve_rejects_symlink_escape(tmp_path, _models_dir):
    secret = tmp_path / "secret.onnx"
    secret.write_bytes(b"s")
    (_models_dir / "link").symlink_to(secret)
    with pytest.raises(models.ModelError):
        models.resolve_asset("link")


def test_resolve_missing_file(_models_dir):
    with pytest.raises(models.ModelError):
        models.resolve_asset("nope.onnx")


def test_read_labels_parses_lines(_models_dir):
    (_models_dir / "labels.txt").write_text("person\ncar\n\n  truck  \n")
    assert models.read_labels("labels.txt") == ["person", "car", "truck"]


def test_read_labels_rejects_empty(_models_dir):
    (_models_dir / "labels.txt").write_text("\n\n")
    with pytest.raises(models.ModelError):
        models.read_labels("labels.txt")


def _install(model_file, models_dir, labels=80):
    shutil.copy(FIXTURES / model_file, models_dir / model_file)
    (models_dir / "labels.txt").write_text("\n".join(f"c{i}" for i in range(labels)) + "\n")


def test_load_and_validate_ok(_models_dir):
    _install("tiny_detector.onnx", _models_dir, labels=80)
    session = models.load_session("tiny_detector.onnx")
    info = models.validate_session(session, models.read_labels("labels.txt"))
    assert info.num_classes == 80
    assert info.input_name == "images"
    assert info.output_name == "output0"
    assert info.input_size == (640, 640)


def test_validate_rejects_bad_output_rank(_models_dir):
    _install("tiny_badrank.onnx", _models_dir, labels=80)
    session = models.load_session("tiny_badrank.onnx")
    with pytest.raises(models.ModelError):
        models.validate_session(session, models.read_labels("labels.txt"))


def test_validate_rejects_label_mismatch(_models_dir):
    _install("tiny_badchannels.onnx", _models_dir, labels=80)  # model has 6 classes
    session = models.load_session("tiny_badchannels.onnx")
    with pytest.raises(models.ModelError):
        models.validate_session(session, models.read_labels("labels.txt"))


def test_load_missing_model(_models_dir):
    with pytest.raises(models.ModelError):
        models.load_session("nope.onnx")


def test_inspect_torchvision_family(_models_dir):
    _install("tiny_torchvision.onnx", _models_dir, labels=1)
    session = models.load_session("tiny_torchvision.onnx")
    labels = models.read_labels("labels.txt")
    spec = models.inspect_session(session, labels)
    assert spec.family == "torchvision"
    assert spec.input_size == (256, 256)
    assert spec.has_batch_dim is False
    assert models.validate_session(session, labels).num_classes == 1
