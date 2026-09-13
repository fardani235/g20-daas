## Why

The curated analysis plugin system currently ships only classic raster
operations (contours, hillshade) and explicitly deferred ML. Users need to turn
imagery into structured features — vehicles, people, and other objects on the
orthophoto — rather than just lines and relief. This change adds the first ML
operation using ONNX Runtime with a configurable model (COCO default), producing
detections as map features.

## What Changes

- Add an `object-detection` analysis operation to the geospatial service,
  running an ONNX model via `onnxruntime` on the `orthophoto` input.
- Tile the input raster and merge results with non-maximum suppression, since a
  full orthophoto cannot be fed to a detector in one pass.
- Emit a GeoJSON `FeatureCollection` of bounding boxes, each carrying its class
  label, class id, and confidence; include per-class counts in metadata.
- Parameters: model path, labels path, confidence threshold, IoU threshold,
  tile size, tile overlap, max detections, and an optional class filter.
- List-valued parameters (the class filter) are entered as plain
  comma/newline-separated text rather than JSON.
- Default model + labels are configurable platform-wide and overridable per
  organization through the existing plugin settings; a general pretrained model
  (COCO classes) is the default.
- Detections reuse the existing run pipeline and vector output path: catalog
  sync, per-org enablement, run/replace semantics, GeoJSON serving, download,
  and map overlays.
- **Non-goals:** model training or fine-tuning, custom class datasets, GPU
  acceleration, video/streams, and instance segmentation (bounding boxes only).

## Capabilities

### New Capabilities
- `object-detection`: running an ONNX object-detection model over a task's
  orthophoto, the parameters and model configuration that govern it, and the
  detection output (bounding boxes with class + confidence) that users see.

### Modified Capabilities
- (none — no existing main specs to modify)

## Impact

- **`webodm-geospatial`**: new operation, `onnxruntime` + image-preprocessing
  dependencies, model/labels loading and validation, tiled inference + NMS,
  detection GeoJSON output and metadata.
- **`webodm_core`**: catalog sync picks up the op automatically; run eligibility
  resolves the `orthophoto` input; worker output handling covers vector output;
  larger default timeout for ML runs; plugin settings carry model configuration.
- **`webodm_frontend`**: detection overlays need class-aware styling and a
  legend, and must respect the vector feature cap for dense scenes.
- **Docker/ops**: the geospatial image grows (ONNX Runtime) and a default model
  file must be provisioned; model path/labels are configurable.
