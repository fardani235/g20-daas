import numpy as np
import pytest

from detplugin import torchvision_det


def test_preprocess_respects_batch_dimension():
    image = np.zeros((256, 256, 3), dtype=np.uint8)
    batched, _, _, _ = torchvision_det.preprocess(image, (256, 256), batch=True)
    assert batched.shape == (1, 3, 256, 256)
    unbatched, _, _, _ = torchvision_det.preprocess(image, (256, 256), batch=False)
    assert unbatched.shape == (3, 256, 256)


def test_preprocess_normalisation_modes():
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    imagenet, *_ = torchvision_det.preprocess(image, (8, 8), normalize="imagenet")
    assert imagenet[0, 0, 0, 0] == pytest.approx(-0.485 / 0.229)
    unit, *_ = torchvision_det.preprocess(image, (8, 8), normalize="unit")
    assert unit.min() == 0.0 and unit.max() == 0.0


def test_decode_default_is_0based_and_thresholds():
    outputs = {
        "boxes": np.array([[1, 2, 3, 4], [5, 6, 7, 8]], dtype=np.float32),
        "scores": np.array([0.9, 0.2], dtype=np.float32),
        "labels": np.array([0, 1], dtype=np.int64),
    }
    detections = torchvision_det.decode(outputs, 0.5)
    assert len(detections) == 1
    det = detections[0]
    assert det["class_id"] == 0
    assert (det["x1"], det["y1"], det["x2"], det["y2"]) == (1.0, 2.0, 3.0, 4.0)
    assert round(det["confidence"], 3) == 0.9


def test_decode_applies_label_offset():
    outputs = {
        "boxes": np.array([[1, 2, 3, 4]], dtype=np.float32),
        "scores": np.array([0.9], dtype=np.float32),
        "labels": np.array([1], dtype=np.int64),
    }
    assert torchvision_det.decode(outputs, 0.5, label_offset=1)[0]["class_id"] == 0


def test_decode_accepts_batched_outputs():
    outputs = {
        "boxes": np.array([[[1, 2, 3, 4]]], dtype=np.float32),
        "scores": np.array([[0.9]], dtype=np.float32),
        "labels": np.array([[0]], dtype=np.int64),
    }
    assert len(torchvision_det.decode(outputs, 0.5)) == 1
