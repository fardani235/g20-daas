"""YOLO-style preprocessing, decoding, and non-maximum suppression.

Pure functions (numpy/Pillow only) so tiling, decoding, and merging can be
tested without loading a model. The detection output contract is YOLOv8's ONNX
form: ``(1, 4 + num_classes, anchors)`` with ``cx, cy, w, h`` in input-pixel
coordinates and per-class probabilities already activated.
"""

import numpy as np
from PIL import Image


def letterbox(image: np.ndarray, size: tuple[int, int]):
    """Resize an ``H x W x C`` uint8 image into ``size`` (h, w) preserving aspect.

    Returns ``(padded, scale, pad_x, pad_y)`` where ``padded`` is the resized
    image placed on a mid-grey canvas of exactly ``size``.
    """
    target_h, target_w = size
    src_h, src_w = image.shape[:2]
    scale = min(target_h / src_h, target_w / src_w)
    new_h, new_w = max(1, round(src_h * scale)), max(1, round(src_w * scale))

    pil = Image.fromarray(image).resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (target_w, target_h), (114, 114, 114))
    pad_x = (target_w - new_w) // 2
    pad_y = (target_h - new_h) // 2
    canvas.paste(pil, (pad_x, pad_y))
    return np.asarray(canvas, dtype=np.uint8), scale, pad_x, pad_y


def preprocess(image: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float, int, int]:
    """Letterbox and normalise an ``H x W x C`` image into a ``1x3xHxW`` tensor."""
    padded, scale, pad_x, pad_y = letterbox(image, size)
    tensor = padded.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))[None, ...]  # HWC -> 1CHW
    return np.ascontiguousarray(tensor), scale, pad_x, pad_y


def decode(output: np.ndarray, confidence: float) -> list[dict]:
    """Decode a YOLO output into detections in input-pixel coordinates."""
    pred = np.asarray(output)
    if pred.ndim == 3:
        pred = pred[0]
    if pred.ndim != 2 or pred.shape[0] < 5:
        raise ValueError(f"unexpected detection output shape: {np.asarray(output).shape}")

    boxes = pred[:4]   # (4, anchors): cx, cy, w, h
    scores = pred[4:]  # (num_classes, anchors)
    class_ids = np.argmax(scores, axis=0)
    confidences = scores[class_ids, np.arange(scores.shape[1])]
    keep = np.nonzero(confidences >= confidence)[0]

    detections = []
    for i in keep:
        cx, cy, w, h = boxes[:, i]
        detections.append({
            "x1": float(cx - w / 2),
            "y1": float(cy - h / 2),
            "x2": float(cx + w / 2),
            "y2": float(cy + h / 2),
            "class_id": int(class_ids[i]),
            "confidence": float(confidences[i]),
        })
    return detections


def iou(a: dict, b: dict) -> float:
    ix1, iy1 = max(a["x1"], b["x1"]), max(a["y1"], b["y1"])
    ix2, iy2 = min(a["x2"], b["x2"]), min(a["y2"], b["y2"])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a["x2"] - a["x1"]) * max(0.0, a["y2"] - a["y1"])
    area_b = max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def nms(detections: list[dict], iou_threshold: float) -> list[dict]:
    """Greedy per-class non-maximum suppression."""
    kept: list[dict] = []
    by_class: dict[int, list[dict]] = {}
    for det in detections:
        by_class.setdefault(det["class_id"], []).append(det)

    for group in by_class.values():
        group = sorted(group, key=lambda d: d["confidence"], reverse=True)
        while group:
            best = group.pop(0)
            kept.append(best)
            group = [d for d in group if iou(best, d) <= iou_threshold]

    return sorted(kept, key=lambda d: d["confidence"], reverse=True)
