"""Preprocessing and decoding for torchvision-style detection ONNX models.

Torchvision detection exports (Faster R-CNN, RetinaNet — e.g. DeepForest) take
an ImageNet-normalised CHW image (commonly without a batch dimension) and emit
already-NMS'd ``boxes`` (xyxy, input-pixel coords), ``scores`` and ``labels``.
Standard torchvision reserves label 0 for background (classes are 1-based);
DeepForest's export is 0-based — the card's ``label_offset`` says which.
"""

import numpy as np

from . import yolo

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def preprocess(image: np.ndarray, size: tuple[int, int], batch: bool = True, normalize: str = "imagenet"):
    """Letterbox + normalise an HWC image into the model's input layout."""
    padded, scale, pad_x, pad_y = yolo.letterbox(image, size)
    arr = padded.astype(np.float32) / 255.0
    if normalize == "imagenet":
        arr = (arr - IMAGENET_MEAN) / IMAGENET_STD
    chw = np.ascontiguousarray(np.transpose(arr, (2, 0, 1)))
    if batch:
        chw = np.ascontiguousarray(chw[None, ...])
    return chw, scale, pad_x, pad_y


def decode(outputs: dict, confidence: float, label_offset: int = 0) -> list[dict]:
    """Decode ``boxes``/``scores``/``labels`` arrays into detections.

    ``label_offset`` is subtracted from each model label to index the card's
    class list: 0 for exports that are already 0-based, 1 for standard
    torchvision detectors that reserve 0 for background.
    """
    boxes = np.asarray(outputs["boxes"]).reshape(-1, 4)
    scores = np.asarray(outputs["scores"]).reshape(-1)
    labels = np.asarray(outputs["labels"]).reshape(-1)
    detections = []
    for box, score, label in zip(boxes, scores, labels):
        if float(score) < confidence:
            continue
        x1, y1, x2, y2 = (float(v) for v in box)
        detections.append({
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "class_id": int(label) - label_offset,
            "confidence": float(score),
        })
    return detections
