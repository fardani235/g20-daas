import numpy as np

from app.analysis.detection import yolo


def test_decode_extracts_box_class_and_confidence():
    out = np.zeros((1, 5, 2), dtype=np.float32)  # 1 class, 2 anchors
    out[0, 0, 0] = 50.0  # cx
    out[0, 1, 0] = 60.0  # cy
    out[0, 2, 0] = 20.0  # w
    out[0, 3, 0] = 10.0  # h
    out[0, 4, 0] = 0.9

    dets = yolo.decode(out, 0.25)

    assert len(dets) == 1
    det = dets[0]
    assert det["class_id"] == 0
    assert round(det["confidence"], 2) == 0.9
    assert (det["x1"], det["y1"], det["x2"], det["y2"]) == (40.0, 55.0, 60.0, 65.0)


def test_decode_threshold_filters_low_confidence():
    out = np.zeros((1, 5, 1), dtype=np.float32)
    out[0, 4, 0] = 0.1
    assert yolo.decode(out, 0.25) == []


def test_decode_rejects_unexpected_shape():
    import pytest

    with pytest.raises(ValueError):
        yolo.decode(np.zeros((1, 84), dtype=np.float32), 0.25)


def test_iou_and_nms_deduplicate_overlapping_boxes():
    a = {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "class_id": 0, "confidence": 0.9}
    b = {"x1": 1, "y1": 1, "x2": 11, "y2": 11, "class_id": 0, "confidence": 0.8}
    assert round(yolo.iou(a, b), 3) == round(81 / (100 + 100 - 81), 3)
    assert len(yolo.nms([a, b], 0.45)) == 1


def test_nms_keeps_distinct_boxes_and_separate_classes():
    a = {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "class_id": 0, "confidence": 0.9}
    b = {"x1": 100, "y1": 100, "x2": 110, "y2": 110, "class_id": 0, "confidence": 0.8}
    c = {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "class_id": 1, "confidence": 0.7}
    assert len(yolo.nms([a, b, c], 0.45)) == 3
