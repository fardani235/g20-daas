# Object Detection — WebODM user plugin

Detects objects on a completed task's **orthophoto** with an ONNX detector and
returns their bounding boxes as a GeoJSON `FeatureCollection` (EPSG:4326) with
`class`, `class_id`, `confidence` and `area_m2` per feature, rendered on the
map with the `detections` style. Full documentation:
[`docs/plugins/object-detection.md`](../../docs/plugins/object-detection.md).

## Bring your own detector

Detectors are **model cards** in `models/` — a JSON file next to the `.onnx`
weights. No plugin code changes are needed to add one:

1. Export your model to ONNX. Supported layouts:
   - **YOLO family** (Ultralytics `yolo export format=onnx`): one image input
     `[1, 3, H, W]` in `[0, 1]`, one output `[1, 4 + classes, anchors]`.
   - **torchvision family** (Faster R-CNN / RetinaNet / DeepForest exports):
     image input `[3, H, W]` or `[1, 3, H, W]` (ImageNet-normalised), outputs
     `boxes[N, 4]`, `scores[N]`, `labels[N]`.
2. Copy the weights to `models/<your-model>.onnx` and write
   `models/<your-model>.json`:

   ```json
   {
     "id": "my-detector",
     "label": "My detector — what it finds",
     "file": "my-detector.onnx",
     "family": "auto",
     "classes": ["car", "truck"],
     "label_offset": 0,
     "recommended": { "confidence": 0.35, "iou": 0.45, "tile_size": 640, "overlap": 96 },
     "source": { "name": "who trained it", "license": "..." }
   }
   ```

   `classes` must be in class-id order and match the model's class count
   (`label_offset: 1` for standard torchvision detectors whose label 0 is
   background). `sha256` is optional and, when present, verified before a run.
3. Rebuild and upload:

   ```bash
   tools/build.sh          # refreshes the model enum in plugin.json, zips the package
   ```

   Upload `plugins/object-detection-<version>.zip` on the Plugins page.

The shipped cards (`deepforest-tree-crowns`, MIT; `visdrone-yolov11s`,
AGPL-3.0) name weights that are **not in git** — run `tools/fetch_models.py`
before building to include them, or `--skip` the ones you do not want.

A bad or mismatched model does not need a pre-run check: the run fails with a
message naming the problem (weights not packaged, checksum differs, class
count differs from the card, unsupported output layout).

## Layout

| Path | Purpose |
|---|---|
| `plugin.json` | Manifest: inputs, parameters (the `model` enum is generated from the cards), output kind `vector`, render kind `detections` |
| `main.py` | Sandbox entrypoint (`python main.py request.json`) |
| `detplugin/` | Tiling, letterboxing, YOLO / torchvision decoding, NMS, GeoJSON |
| `models/` | Model cards (`*.json`) and their weights (`*.onnx`, fetched) |
| `tools/fetch_models.py` | Download + verify the cards' weights |
| `tools/update_manifest.py` | Sync `plugin.json`'s `model` enum with the cards (`--check` in tests) |
| `tools/build.sh` | Package the zip |
| `tests/` | pytest suite with tiny deterministic ONNX fixtures (`tests/fixtures/make_fixtures.py`) |

Tests: `python -m pytest -q` from this directory (needs numpy, rasterio,
Pillow, onnxruntime — the sandbox's stack).
