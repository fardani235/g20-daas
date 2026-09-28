import numpy as np
import pytest

from detplugin import yolo


def test_letterbox_pads_non_square_tiles_symmetrically():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    padded, scale, pad_x, pad_y = yolo.letterbox(image, (640, 640))
    assert padded.shape == (640, 640, 3)
    assert scale == pytest.approx(3.2)
    assert (pad_x, pad_y) == (0, 160)
    assert tuple(padded[0, 0]) == yolo.PAD_COLOR  # padding is mid-grey


def test_preprocess_layout_and_range():
    image = np.full((64, 64, 3), 255, dtype=np.uint8)
    tensor, scale, _, _ = yolo.preprocess(image, (64, 64))
    assert tensor.shape == (1, 3, 64, 64) and tensor.dtype == np.float32
    assert tensor.max() == pytest.approx(1.0) and scale == 1.0
    unbatched, *_ = yolo.preprocess(image, (64, 64), batch=False)
    assert unbatched.shape == (3, 64, 64)


def test_decode_extracts_box_class_and_confidence():
    out = np.zeros((1, 5, 2), dtype=np.float32)  # 1 class, 2 anchors
    out[0, :4, 0] = [50.0, 60.0, 20.0, 10.0]      # cx, cy, w, h
    out[0, 4, 0] = 0.9
    dets = yolo.decode(out, 0.25)
    assert len(dets) == 1
    det = dets[0]
    assert det["class_id"] == 0 and round(det["confidence"], 2) == 0.9
    assert (det["x1"], det["y1"], det["x2"], det["y2"]) == (40.0, 55.0, 60.0, 65.0)


def test_decode_threshold_filters_low_confidence():
    out = np.zeros((1, 5, 1), dtype=np.float32)
    out[0, 4, 0] = 0.1
    assert yolo.decode(out, 0.25) == []


def test_decode_rejects_unexpected_shape():
    with pytest.raises(ValueError):
        yolo.decode(np.zeros((1, 84), dtype=np.float32), 0.25)


def test_iou_and_nms_deduplicate_overlapping_boxes():
    a = {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "class_id": 0, "confidence": 0.9}
    b = {"x1": 1, "y1": 1, "x2": 11, "y2": 11, "class_id": 0, "confidence": 0.8}
    assert round(yolo.iou(a, b), 3) == round(81 / (100 + 100 - 81), 3)
    assert yolo.nms([b, a], 0.45) == [a]


def test_nms_keeps_distinct_boxes_and_separate_classes():
    a = {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "class_id": 0, "confidence": 0.9}
    b = {"x1": 100, "y1": 100, "x2": 110, "y2": 110, "class_id": 0, "confidence": 0.8}
    c = {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "class_id": 1, "confidence": 0.7}
    kept = yolo.nms([c, b, a], 0.45)
    assert len(kept) == 3
    assert [d["confidence"] for d in kept] == [0.9, 0.8, 0.7]  # most confident first
