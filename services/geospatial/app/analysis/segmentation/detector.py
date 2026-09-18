"""Semantic segmentation over a task orthophoto: tiling, stitching, GeoJSON.

The orthophoto is far larger than a segmentation model's fixed input, so it is
processed in overlapping tiles using the same tiling semantics as object
detection (``tile_size``/``overlap`` in pixels, or ``tile_size_m``/``overlap_m``
in ground metres via GSD). Each tile is letterboxed to the model input and its
per-class mask is mapped back to the tile, then written into a whole-extent
label map.

Two guards keep the result usable:

- **Overlap disagreement is resolved by probability.** Where tiles overlap, the
  pixel keeps the prediction with the higher class confidence, so a region that
  spans a seam is emitted once and stays contiguous.
- **Raw masks are noisy.** A per-class morphological open/close removes isolated
  speckle, and connected regions below ``min_segment_area`` are dropped before
  vectorizing, so output is not dominated by fragments.
"""

import json
import os

import numpy as np
import rasterio
import rasterio.features
from pydantic import BaseModel, Field, field_validator, model_validator
from rasterio.warp import transform_geom
from scipy import ndimage
from shapely.geometry import mapping, shape

from app.analysis.detection import yolo
from app.analysis.detection.detector import (
    _gsd,
    _resolve_tiling,
    _tile_to_image,
    _windows,
)
from app.analysis.segmentation import models
from app.utils.volume import _to_meters_factor

_TILE_MIN, _TILE_MAX = 64, 4096
_UNCLASSIFIED = -1
_BACKGROUND_NAME = "background"

# Platform default model/labels. Provisioning the real artifact is an ops step;
# an operator can point the default at any permissive model in the directory.
DEFAULT_MODEL = os.environ.get("SEGMENTATION_DEFAULT_MODEL") or "segmentation.onnx"
DEFAULT_LABELS = os.environ.get("SEGMENTATION_DEFAULT_LABELS") or "segmentation.txt"


class SegmentationParams(BaseModel):
    model: str = Field(
        DEFAULT_MODEL, description="ONNX segmentation model in the models directory"
    )
    labels: str = Field(
        DEFAULT_LABELS, description="Class labels file in the models directory"
    )
    threshold: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Minimum class probability to classify a pixel",
    )
    tile_size: int = Field(
        512, ge=_TILE_MIN, le=_TILE_MAX, description="Tile size in raster pixels"
    )
    tile_size_m: float | None = Field(
        None, gt=0, description="Tile ground size in metres (overrides tile_size)"
    )
    overlap: int = Field(64, ge=0, description="Tile overlap in raster pixels")
    overlap_m: float | None = Field(
        None, ge=0, description="Tile overlap in metres (overrides overlap)"
    )
    min_segment_area: int = Field(
        64, ge=0, description="Minimum region area in raster pixels"
    )
    simplify_tolerance: float = Field(
        0.0, ge=0,
        description="Polygon simplification tolerance in raster pixels (0 disables)",
    )
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
        if self.overlap >= self.tile_size and not self.tile_size_m:
            raise ValueError("overlap must be smaller than tile_size")
        if (
            self.tile_size_m
            and self.overlap_m is not None
            and self.overlap_m >= self.tile_size_m
        ):
            raise ValueError("overlap_m must be smaller than tile_size_m")
        return self


def _as_probabilities(raw: np.ndarray, mode: str) -> np.ndarray:
    """Normalise a model output to scores in ``[0, 1]``.

    Outputs already in ``[0, 1]`` are treated as probabilities; anything else is
    treated as logits (softmax over classes, sigmoid for a single channel).
    """
    arr = np.asarray(raw, dtype=np.float32)
    if arr.ndim == 4:
        arr = arr[0]

    if mode == "binary":
        if arr.ndim == 3:
            arr = arr[0]
        if arr.size and arr.min() >= 0.0 and arr.max() <= 1.0:
            return arr
        return 1.0 / (1.0 + np.exp(-arr))

    if arr.size and arr.min() >= 0.0 and arr.max() <= 1.0:
        return arr
    shifted = arr - arr.max(axis=0, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=0, keepdims=True)


def decode_mask(
    raw: np.ndarray,
    spec: models.SegmentationSpec,
    labels: list[str],
    threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Decode a model mask into ``(class_map, confidence_map)``.

    ``class_map`` holds a class index per pixel, or ``_UNCLASSIFIED`` where the
    winning class is below ``threshold``. ``confidence_map`` holds the winning
    probability, or ``0`` where unclassified.
    """
    if spec.mode == "binary":
        score = _as_probabilities(raw, "binary")
        keep = score >= threshold
        class_map = np.where(keep, spec.foreground_index, _UNCLASSIFIED)
        confidence = np.where(keep, score, 0.0)
        return class_map.astype(np.int16), confidence.astype(np.float32)

    probs = _as_probabilities(raw, "multiclass")
    index = np.argmax(probs, axis=0)
    confidence = np.take_along_axis(probs, index[None, ...], axis=0)[0]
    keep = confidence >= threshold
    class_map = np.where(keep, index, _UNCLASSIFIED)
    return class_map.astype(np.int16), np.where(keep, confidence, 0.0).astype(np.float32)


def _resize_nearest(arr: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Resize a 2-D label/confidence array to ``shape`` with nearest sampling."""
    height, width = shape
    src_h, src_w = arr.shape
    if height <= 0 or width <= 0 or src_h == 0 or src_w == 0:
        return arr
    rows = np.arange(height) * src_h // height
    cols = np.arange(width) * src_w // width
    return arr[np.ix_(rows, cols)]


def _stitch(
    class_map: np.ndarray,
    confidence_map: np.ndarray,
    tile_classes: np.ndarray,
    tile_confidence: np.ndarray,
    window,
) -> None:
    """Write a tile into the global maps, keeping the higher-confidence class."""
    row = int(window.row_off)
    col = int(window.col_off)
    height, width = tile_classes.shape

    target_classes = class_map[row:row + height, col:col + width]
    target_confidence = confidence_map[row:row + height, col:col + width]

    better = tile_confidence > target_confidence
    target_classes[better] = tile_classes[better]
    target_confidence[better] = tile_confidence[better]


def _background_index(labels: list[str]) -> int | None:
    for index, name in enumerate(labels):
        if name.strip().lower() == _BACKGROUND_NAME:
            return index
    return None


def emit_classes(labels: list[str], params: SegmentationParams) -> list[int]:
    """Class indices to emit: everything except background and non-filtered classes."""
    background = _background_index(labels)
    allowed = set(params.classes) if params.classes else None
    result = []
    for index, name in enumerate(labels):
        if index == background:
            continue
        if allowed is not None and name not in allowed:
            continue
        result.append(index)
    return result


def _morphology(mask: np.ndarray) -> np.ndarray:
    """Open then close a binary mask with a 3x3 element to remove speckle."""
    structure = np.ones((3, 3), dtype=bool)
    opened = ndimage.binary_opening(mask, structure=structure)
    return ndimage.binary_closing(opened, structure=structure)


def clean_mask(
    class_map: np.ndarray,
    confidence_map: np.ndarray,
    labels: list[str],
    params: SegmentationParams,
) -> np.ndarray:
    """Morphologically clean each class and resolve overlaps by confidence."""
    cleaned = np.full(class_map.shape, _UNCLASSIFIED, dtype=np.int16)
    best = np.full(class_map.shape, -1.0, dtype=np.float32)

    for class_id in emit_classes(labels, params):
        mask = class_map == class_id
        if not mask.any():
            continue
        mask = _morphology(mask)
        if not mask.any():
            continue
        wins = mask & (confidence_map > best)
        cleaned[wins] = class_id
        best[wins] = confidence_map[wins]

    return cleaned


def _area_scale(ds) -> tuple[float, str]:
    """Per-CRS-unit-to-metre factor and the label for the resulting area unit."""
    factor = _to_meters_factor(ds) or 1.0
    if ds.crs is not None and ds.crs.is_geographic:
        return factor, "crs_units2"
    return factor, "m2"


def vectorize(
    ds,
    cleaned: np.ndarray,
    confidence_map: np.ndarray,
    labels: list[str],
    params: SegmentationParams,
) -> tuple[list[dict], dict[str, int], dict[str, float], str]:
    """Vectorize cleaned regions to EPSG:4326 polygons with class and area."""
    from rasterio.transform import Affine

    factor, area_unit = _area_scale(ds)
    pixel_area = abs(ds.transform.a) * abs(ds.transform.e) * (factor ** 2)

    features: list[dict] = []
    counts: dict[str, int] = {}
    areas: dict[str, float] = {}

    for class_id in emit_classes(labels, params):
        mask = cleaned == class_id
        if not mask.any():
            continue

        labelled, count = ndimage.label(mask)
        if count == 0:
            continue

        for component, slices in enumerate(ndimage.find_objects(labelled), start=1):
            if slices is None:
                continue
            region = labelled[slices] == component
            area_px = int(region.sum())
            if area_px < params.min_segment_area:
                continue

            region_confidence = confidence_map[slices][region]
            mean_confidence = float(region_confidence.mean()) if region_confidence.size else 0.0

            window_transform = ds.transform * Affine.translation(
                slices[1].start, slices[0].start
            )
            values = rasterio.features.shapes(
                region.astype(np.uint8), mask=region, transform=window_transform
            )
            for geometry, value in values:
                if not value:
                    continue
                polygon = shape(geometry)
                if params.simplify_tolerance:
                    polygon = polygon.simplify(
                        params.simplify_tolerance, preserve_topology=True
                    )
                if polygon.is_empty:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": transform_geom(ds.crs, "EPSG:4326", mapping(polygon)),
                    "properties": {
                        "class": labels[class_id],
                        "class_id": class_id,
                        "area": round(area_px * pixel_area, 3),
                        "confidence": round(mean_confidence, 4),
                    },
                })
                label = labels[class_id]
                counts[label] = counts.get(label, 0) + 1
                areas[label] = round(areas.get(label, 0.0) + area_px * pixel_area, 3)

    return features, counts, areas, area_unit


def run_segmentation(
    inputs: dict, params: SegmentationParams, output_path: str
) -> dict:
    """Run tiled semantic segmentation and write a GeoJSON feature collection."""
    src = inputs.get("raster")
    if not src:
        raise ValueError("semantic-segmentation requires an orthophoto input")

    session = models.load_session(params.model)
    labels = models.read_labels(params.labels)
    spec = models.inspect_session(session, labels)

    with rasterio.open(src) as ds:
        if ds.crs is None:
            raise ValueError("orthophoto is not georeferenced")

        tile, overlap = _resolve_tiling(ds, params)
        gsd = _gsd(ds)
        width, height = ds.width, ds.height

        class_map = np.full((height, width), _UNCLASSIFIED, dtype=np.int16)
        confidence_map = np.zeros((height, width), dtype=np.float32)

        for window in _windows(width, height, tile, overlap):
            tile_array = ds.read(window=window)
            image = _tile_to_image(tile_array)

            tensor, scale, pad_x, pad_y = yolo.preprocess(image, spec.input_size)
            if not spec.has_batch_dim:
                tensor = tensor[0]

            outputs = session.run([spec.output_name], {spec.input_name: tensor})
            tile_classes, tile_confidence = decode_mask(
                outputs[0], spec, labels, params.threshold
            )

            # Undo letterbox: keep the valid region and scale it back to the tile.
            valid_h = round(image.shape[0] * scale)
            valid_w = round(image.shape[1] * scale)
            tile_classes = tile_classes[pad_y:pad_y + valid_h, pad_x:pad_x + valid_w]
            tile_confidence = tile_confidence[pad_y:pad_y + valid_h, pad_x:pad_x + valid_w]
            tile_classes = _resize_nearest(
                tile_classes, (int(window.height), int(window.width))
            )
            tile_confidence = _resize_nearest(
                tile_confidence, (int(window.height), int(window.width))
            )

            _stitch(class_map, confidence_map, tile_classes, tile_confidence, window)

        cleaned = clean_mask(class_map, confidence_map, labels, params)
        features, counts, areas, area_unit = vectorize(
            ds, cleaned, confidence_map, labels, params
        )

    collection = {"type": "FeatureCollection", "features": features}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(collection, f)

    return {
        "output_path": output_path,
        "metadata": {
            "counts": counts,
            "areas": areas,
            "total": len(features),
            "model": params.model,
            "labels": params.labels,
            "mode": spec.mode,
            "tile_size": tile,
            "overlap": overlap,
            "gsd": round(gsd, 6),
            "area_unit": area_unit,
        },
    }
