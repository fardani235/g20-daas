"""Object detection over a task orthophoto: tiling, inference, and GeoJSON.

The orthophoto is far larger than a detector's fixed input, so it is processed
in overlapping tiles; detections are mapped back to raster pixels, merged with a
single per-class NMS pass across tiles, then reprojected to EPSG:4326.
"""

import json

import numpy as np
import rasterio
from pydantic import BaseModel, Field, field_validator, model_validator
from rasterio.windows import Window
from rasterio.warp import transform_geom

from app.analysis.detection import models, yolo


class DetectionParams(BaseModel):
    model: str = Field("yolov8n.onnx", description="ONNX detector in the models directory")
    labels: str = Field("coco.txt", description="Class labels file in the models directory")
    confidence: float = Field(0.25, ge=0.0, le=1.0, description="Minimum class confidence")
    iou: float = Field(0.45, ge=0.0, le=1.0, description="NMS IoU threshold")
    tile_size: int = Field(640, ge=64, le=4096, description="Tile size in raster pixels")
    overlap: int = Field(64, ge=0, description="Tile overlap in raster pixels")
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

    @model_validator(mode="after")
    def _overlap_less_than_tile(self):
        if self.overlap >= self.tile_size:
            raise ValueError("overlap must be smaller than tile_size")
        return self


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


def run_detection(inputs: dict, params: DetectionParams, output_path: str) -> dict:
    src = inputs.get("raster")
    if not src:
        raise ValueError("object-detection requires an orthophoto input")

    session = models.load_session(params.model)
    labels = models.read_labels(params.labels)
    info = models.validate_session(session, labels)
    if session.get_inputs() and info.input_size is None:
        model_input = (params.tile_size, params.tile_size)
    else:
        model_input = info.input_size

    allowed = {label for label in params.classes} if params.classes else None

    raw: list[dict] = []
    with rasterio.open(src) as ds:
        if ds.crs is None:
            raise ValueError("orthophoto is not georeferenced")

        for window in _windows(ds.width, ds.height, params.tile_size, params.overlap):
            tile = ds.read(window=window)
            image = _tile_to_image(tile)
            tensor, scale, pad_x, pad_y = yolo.preprocess(image, model_input)
            output = session.run([info.output_name], {info.input_name: tensor})[0]

            for det in yolo.decode(output, params.confidence):
                if det["class_id"] >= len(labels):
                    continue
                if allowed is not None and labels[det["class_id"]] not in allowed:
                    continue
                raw.append({
                    "x1": window.col_off + (det["x1"] - pad_x) / scale,
                    "y1": window.row_off + (det["y1"] - pad_y) / scale,
                    "x2": window.col_off + (det["x2"] - pad_x) / scale,
                    "y2": window.row_off + (det["y2"] - pad_y) / scale,
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
        },
    }
