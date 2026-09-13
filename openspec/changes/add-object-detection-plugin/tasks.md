## 1. Geospatial: dependencies and model management

- [x] 1.1 Add `onnxruntime` and image-preprocessing (`Pillow`) to `requirements.txt` and verify `python -c "import onnxruntime, PIL"` succeeds in the geospatial venv/image
- [x] 1.2 Add a managed models-directory resolver that rejects absolute paths, `..`, and symlink escapes, and verify unit tests reject traversal and accept a valid file inside the directory
- [x] 1.3 Implement a model + labels loader that validates a single image input and a compatible detection output and that the label count matches the model's class count, and verify tests cover valid, malformed, and mismatched-label cases
- [x] 1.4 Provision a default COCO ONNX detector and its labels into the models directory and verify the operation loads it and reports the expected class names
- [x] 1.5 Provision an aerial-trained detector (VisDrone, 10 classes) and its labels, make the platform default model/labels configurable, and verify a real orthophoto yields aerial classes instead of COCO's

## 2. Geospatial: detection operation

- [x] 2.1 Define the detection `params_model` (model, labels, confidence, iou, tile_size, overlap, max_detections, classes) with defaults and bounds, and verify schema generation and range-validation tests
- [x] 2.2 Implement tiled inference with overlap, mapping each detection's pixel box to raster coordinates, and verify a test using a synthetic ONNX fixture detects a known box in a fixture orthophoto
- [x] 2.3 Implement a single global non-maximum-suppression pass over merged detections and verify an object spanning a tile boundary yields exactly one detection
- [x] 2.4 Emit a GeoJSON `FeatureCollection` of bounding boxes (EPSG:4326) with `class`, `class_id`, `confidence` and metadata with per-class counts and a total, and verify structure plus the empty-result case
- [x] 2.5 Register the operation (`op_id: object-detection`, `output_kind: vector`, `render_kind: detections`, `inputs: orthophoto`, and its timeout) and verify the catalog exposes it with params schema, inputs, and timeout
- [x] 2.6 Discard detections centred in letterbox padding or clipped at an interior tile edge, and verify with unit tests on the mapping helper
- [x] 2.7 Support ground-metre tiling (`tile_size_m`/`overlap_m`) resolved from the raster GSD, and verify with a unit test on the tiling resolver
- [x] 2.8 Support a second detection family (torchvision `boxes`/`scores`/`labels`, ImageNet preprocessing, optional batch dim, configurable label offset), provision a tree model (DeepForest) and labels, and verify with a fixture model and a live run

## 3. Catalog and run timeout contract

- [x] 3.1 Add optional `timeout_seconds` to the op catalog entry and bump the catalog schema version, and verify a catalog contract test includes the field while existing ops keep the default
- [x] 3.2 Add a `timeout_seconds` field to `WebODM Plugin`, sync it from the catalog, and verify `bench migrate` applies and sync stores the value
- [x] 3.3 Use the plugin's `timeout_seconds` (with a sensible fallback) for both the RQ enqueue timeout and the geospatial HTTP call, and verify a test asserts the longer timeout is passed for a detection-style plugin
- [x] 3.4 Add a pre-run validate endpoint (`POST /analysis/{op_id}/validate`) with an op-level validator and a `needs_validation` catalog flag, have Frappe call it before creating a run, and verify a missing/invalid model rejects the request with no run created

## 4. Frappe: detection integration

- [x] 4.1 Verify the `orthophoto` input is resolved from the task and that a run is rejected when the task has no orthophoto, with a test covering both
- [x] 4.2 Verify detection runs persist as vector output (GeoJSON file, `output_extent` optional) and are downloadable, with a test asserting Completed and a retrievable output
- [x] 4.3 Verify model/labels surface as plugin parameters with the platform default and can be overridden per organization, with a test covering default and override

## 5. Frontend: detections rendering

- [x] 5.1 Add class-aware styling and a legend for detections (a helper mapping classes to colours/labels) and verify it with a unit test
- [x] 5.2 Ensure detections respect the vector feature cap (skip + explanatory message + still downloadable) and verify the cap path is covered by a unit test or the existing cap test
- [x] 5.3 Render detections in the Layers panel with the same toggle/opacity behaviour as other vector overlays, verified in the running app
- [x] 5.4 Let string-list parameters (the class filter) be entered as comma- or newline-separated text, and verify with a unit test for the parser and formatter

## 6. End-to-end verification

- [x] 6.1 Verify the full vertical: enable the plugin for an organization, run it on a completed task with an orthophoto, confirm a detections GeoJSON with per-class counts, see the class-styled overlay and legend, and download the output
- [x] 6.2 Verify robustness/security: an out-of-directory or missing model is rejected before a run is created, a failed inference records an error, and a dense detection set does not freeze the map
