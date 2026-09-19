## Why

Users can currently detect objects as bounding boxes, but many geospatial questions
are about continuous land cover — building footprints, roads, water — where a
classified mask is the right output rather than discrete boxes. The analysis plugin
system has no semantic-segmentation operation, so this change adds one, reusing the
existing catalog, run lifecycle, and vector output path.

## What Changes

- Add a `semantic-segmentation` analysis operation to the geospatial service that
  runs a permissive-licensed ONNX segmentation model over a task's orthophoto.
- Process the orthophoto in overlapping tiles, stitch the per-tile class masks, and
  post-process (smoothing, small-segment removal) before vectorizing.
- Emit a GeoJSON `FeatureCollection` of class polygons carrying class, area, and a
  confidence/score where the model provides one, with per-class area and count
  metadata, reusing the existing vector output, overlay, and download path.
- Parameters: model, a class/label set, confidence or mask threshold, tile size,
  tile overlap, minimum segment area, and simplification tolerance. Omitted values
  fall back to documented defaults and supplied values are validated before a run
  is created.
- Constrain models to the existing managed models directory with the same
  path-traversal rejection and load validation used by object detection.
- Frontend: a fill-based segmentation render kind with per-class styling and a
  legend, respecting the existing vector feature cap for dense outputs.
- **Licensing:** implement natively using the permissive stack already present
  (onnxruntime, rasterio, shapely, numpy/scipy). Do not vendor or link geodeep,
  which is AGPL-3.0.
- **Non-goals:** instance segmentation (per-object masks), changes to the existing
  detector, model training/fine-tuning, GPU acceleration, and video/streams.

## Capabilities

### New Capabilities

- `semantic-segmentation`: running an ONNX semantic-segmentation model over a
  task's orthophoto, the parameters and model configuration that govern it, and
  the class-mask vector output users see.

### Modified Capabilities

- (none — no existing spec's requirements change; this reuses the catalog, run,
  and vector-output requirements as written)

## Impact

- **`webodm-geospatial`**: new operation and parameter model; segmentation model
  loading/validation; tiled mask inference and stitching; mask post-processing and
  polygonization; GeoJSON output with class/area metadata; one or more
  permissive-licensed segmentation models provisioned in the models directory.
- **`webodm_core`**: catalog sync picks up the operation automatically; run
  eligibility resolves the `orthophoto` input; vector output handling serves the
  polygons; plugin settings may carry model configuration. Uses the existing
  operation-declared timeout rather than a new field.
- **`webodm_frontend`**: segmentation overlay styling (fills), legend, feature-cap
  handling, and download, alongside the existing detection rendering.
- **Docker/ops**: the geospatial image gains a segmentation model file; no new
  runtime dependency class, since `onnxruntime` and `rasterio` are already used.
