"""Object detection over a task orthophoto: tiling, inference, and GeoJSON.

The orthophoto is far larger than a detector's fixed input, so it is processed
in overlapping tiles; detections are mapped back to raster pixels, merged with a
single per-class NMS pass across tiles, then reprojected to EPSG:4326.

Two accuracy guards beyond the raw model:

- **Tiling that ignores GSD is inconsistent.** ``tile_size`` is in raster
  pixels, so the same value covers a different ground area on a 5 cm orthophoto
  than on a 2 cm one, changing how large objects appear to the model. Setting
  ``tile_size_m`` / ``overlap_m`` chooses the tile by *ground* size instead, so
  object scale stays comparable across datasets.
- **Letterbox padding and tile edges produce fake boxes.** Edge tiles are not
  square, so they are padded to the model input. Detections centred in that
  padding, and boxes clipped at an interior tile edge (a neighbouring tile sees
  them whole), are dropped before they become annotations.
"""

import json
import os

import numpy as np
import rasterio
from pydantic import BaseModel, Field, field_validator, model_validator
from rasterio.windows import Window
from rasterio.warp import transform_geom

from app.analysis.detection import models, torchvision_det, yolo

_TILE_MIN, _TILE_MAX = 64, 4096

# Platform default model/labels (an operator can point the default at an aerial
# model, e.g. VisDrone, without touching requests).
_DEFAULT_MODEL = os.environ.get("OBJECT_DETECTION_DEFAULT_MODEL") or "yolov8n.onnx"
_DEFAULT_LABELS = os.environ.get("OBJECT_DETECTION_DEFAULT_LABELS") or "coco.txt"


class DetectionParams(BaseModel):
    model: str = Field(_DEFAULT_MODEL, description="ONNX detector in the models directory")
    labels: str = Field(_DEFAULT_LABELS, description="Class labels file in the models directory")
    family: str = Field(
        "auto", description="Model family: auto, yolo, or torchvision (DeepForest-style)"
    )
    label_offset: int = Field(
        0, ge=0, le=10,
        description="Subtract from model labels to index the labels file "
                    "(0 for DeepForest exports, 1 for standard torchvision)",
    )
    confidence: float = Field(0.25, ge=0.0, le=1.0, description="Minimum class confidence")
    iou: float = Field(0.45, ge=0.0, le=1.0, description="NMS IoU threshold")
    tile_size: int = Field(640, ge=_TILE_MIN, le=_TILE_MAX, description="Tile size in raster pixels")
    tile_size_m: float | None = Field(
        None, gt=0, description="Tile ground size in metres (overrides tile_size)"
    )
    overlap: int = Field(64, ge=0, description="Tile overlap in raster pixels")
    overlap_m: float | None = Field(
        None, ge=0, description="Tile overlap in metres (overrides overlap)"
    )
    max_detections: int = Field(5000, gt=0, description="Maximum detections to keep")
    classes: list[str] = Field(
        default_factory=list, description="Optional subset of labels to keep"
    )

    @field_validator("tile_size")
    @classmethod
    def _tile_multiple_of_32(cls, value: int) -> int:
        if value % 32 != 0:
            raise ValueError("tile_size must be a multiple of 32")
        return value

    @field_validator("family")
    @classmethod
    def _known_family(cls, value: str) -> str:
        if value not in ("auto", "yolo", "torchvision"):
            raise ValueError("family must be auto, yolo, or torchvision")
        return value

    @model_validator(mode="after")
    def _overlap_less_than_tile(self):
        if self.overlap >= self.tile_size and not self.tile_size_m:
            raise ValueError("overlap must be smaller than tile_size")
        if self.tile_size_m and self.overlap_m is not None and self.overlap_m >= self.tile_size_m:
            raise ValueError("overlap_m must be smaller than tile_size_m")
        return self


def _gsd(ds) -> float:
    """Mean ground sample distance of an open raster, in CRS units per pixel."""
    return (abs(ds.res[0]) + abs(ds.res[1])) / 2 or 1.0


def _resolve_tiling(ds, params: DetectionParams) -> tuple[int, int]:
    """Effective (tile_px, overlap_px), honouring ground-size parameters when set."""
    gsd = _gsd(ds)
    if params.tile_size_m:
        tile = int(round(params.tile_size_m / gsd))
        tile = max(_TILE_MIN, min(_TILE_MAX, tile))
        tile = max(32, round(tile / 32) * 32)
    else:
        tile = params.tile_size

    if params.overlap_m is not None:
        overlap = int(round(params.overlap_m / gsd))
    else:
        overlap = params.overlap
    overlap = max(0, min(tile - 1, overlap))
    return tile, overlap


def _crs_polygon(transform, src_crs, x1, y1, x2, y2, dst_crs="EPSG:4326"):
    """Raster-pixel bounding box -> GeoJSON polygon in ``dst_crs``."""
    xs, ys = rasterio.transform.xy(
        transform,
        [y1, y1, y2, y2, y1],
        [x1, x2, x2, x1, x1],
    )
    ring = [[float(x), float(y)] for x, y in zip(xs, ys)]
    geom = {"type": "Polygon", "coordinates": [ring]}
    if src_crs is None:
        raise ValueError("raster is not georeferenced")
    if src_crs.to_string() == dst_crs:
        return geom
    return transform_geom(src_crs, dst_crs, geom)


def _windows(width: int, height: int, tile: int, overlap: int):
    stride = max(1, tile - overlap)
    for row_off in range(0, max(1, height), stride):
        for col_off in range(0, max(1, width), stride):
            w = min(tile, width - col_off)
            h = min(tile, height - row_off)
            if w <= 0 or h <= 0:
                continue
            yield Window(col_off, row_off, w, h)


def _tile_to_image(array: np.ndarray) -> np.ndarray:
    """``(bands, h, w)`` raster window -> ``(h, w, 3)`` uint8 RGB-ish image."""
    bands = array.shape[0]
    if bands >= 3:
        hwc = np.transpose(array[:3], (1, 2, 0))
    else:
        hwc = np.repeat(np.transpose(array[:1], (1, 2, 0)), 3, axis=2)
    if hwc.dtype != np.uint8:
        hwc = np.clip(hwc, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(hwc)


def _map_detection(det, scale, pad_x, pad_y, valid_w, valid_h, window, raster_w, raster_h,
                   tol=1.5):
    """Map a model-space detection to global raster pixels, dropping artifacts.

    Returns ``(x1, y1, x2, y2)`` in raster pixels, or ``None`` when the detection
    is centred in letterbox padding or was clipped at an interior tile edge.
    """
    vx1, vy1 = pad_x, pad_y
    vx2, vy2 = pad_x + valid_w, pad_y + valid_h

    cx = (det["x1"] + det["x2"]) / 2
    cy = (det["y1"] + det["y2"]) / 2
    if not (vx1 <= cx < vx2 and vy1 <= cy < vy2):
        return None

    x1, y1 = max(det["x1"], vx1), max(det["y1"], vy1)
    x2, y2 = min(det["x2"], vx2), min(det["y2"], vy2)
    if x2 <= x1 or y2 <= y1:
        return None

    col0, row0 = int(window.col_off), int(window.row_off)
    col1, row1 = col0 + int(window.width), row0 + int(window.height)

    # A box clipped at an interior tile edge is seen whole by a neighbour tile.
    if (det["x1"] < vx1 - tol and col0 > 0) or (det["x2"] > vx2 + tol and col1 < raster_w):
        return None
    if (det["y1"] < vy1 - tol and row0 > 0) or (det["y2"] > vy2 + tol and row1 < raster_h):
        return None

    gx1 = max(0.0, min(col0 + (x1 - pad_x) / scale, raster_w))
    gy1 = max(0.0, min(row0 + (y1 - pad_y) / scale, raster_h))
    gx2 = max(0.0, min(col0 + (x2 - pad_x) / scale, raster_w))
    gy2 = max(0.0, min(row0 + (y2 - pad_y) / scale, raster_h))
    if gx2 <= gx1 or gy2 <= gy1:
        return None
    return gx1, gy1, gx2, gy2


def run_detection(inputs: dict, params: DetectionParams, output_path: str) -> dict:
    src = inputs.get("raster")
    if not src:
        raise ValueError("object-detection requires an orthophoto input")

    session = models.load_session(params.model)
    labels = models.read_labels(params.labels)
    spec = models.inspect_session(session, labels, family=params.family)
    model_input = spec.input_size

    allowed = {label for label in params.classes} if params.classes else None

    raw: list[dict] = []
    with rasterio.open(src) as ds:
        if ds.crs is None:
            raise ValueError("orthophoto is not georeferenced")

        tile, overlap = _resolve_tiling(ds, params)
        gsd = _gsd(ds)
        raster_w, raster_h = ds.width, ds.height

        for window in _windows(raster_w, raster_h, tile, overlap):
            tile_array = ds.read(window=window)
            image = _tile_to_image(tile_array)
            if spec.family == "torchvision":
                tensor, scale, pad_x, pad_y = torchvision_det.preprocess(
                    image, model_input, batch=spec.has_batch_dim
                )
            else:
                tensor, scale, pad_x, pad_y = yolo.preprocess(image, model_input)
            valid_w = round(image.shape[1] * scale)
            valid_h = round(image.shape[0] * scale)

            outputs = session.run(spec.output_names, {spec.input_name: tensor})
            if spec.family == "torchvision":
                decoded = torchvision_det.decode(
                    dict(zip(("boxes", "scores", "labels"), outputs)),
                    params.confidence,
                    label_offset=params.label_offset,
                )
            else:
                decoded = yolo.decode(outputs[0], params.confidence)

            for det in decoded:
                if det["class_id"] < 0 or det["class_id"] >= len(labels):
                    continue
                if allowed is not None and labels[det["class_id"]] not in allowed:
                    continue
                box = _map_detection(
                    det, scale, pad_x, pad_y, valid_w, valid_h,
                    window, raster_w, raster_h,
                )
                if box is None:
                    continue
                raw.append({
                    "x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3],
                    "class_id": det["class_id"],
                    "confidence": det["confidence"],
                })

        merged = yolo.nms(raw, params.iou)[: params.max_detections]

        features = []
        counts: dict[str, int] = {}
        for det in merged:
            label = labels[det["class_id"]]
            geometry = _crs_polygon(
                ds.transform, ds.crs,
                det["x1"], det["y1"], det["x2"], det["y2"],
            )
            features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "class": label,
                    "class_id": det["class_id"],
                    "confidence": round(det["confidence"], 4),
                },
            })
            counts[label] = counts.get(label, 0) + 1

    collection = {"type": "FeatureCollection", "features": features}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(collection, f)

    return {
        "output_path": output_path,
        "metadata": {
            "counts": counts,
            "total": len(features),
            "model": params.model,
            "labels": params.labels,
            "family": spec.family,
            "tile_size": tile,
            "overlap": overlap,
            "gsd": round(gsd, 6),
        },
    }
