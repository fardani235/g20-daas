# Detection models

The object-detection operation reads its ONNX model and label file from the
managed models directory (`OBJECT_DETECTION_MODELS_DIR`, default
`/opt/webodm/models`). Configuration only ever names an asset *within* this
directory; absolute paths and traversal are rejected.

## Default

The geospatial image provisions:

- `yolov8n.onnx` — YOLOv8n COCO detector (80 classes), fetched at build time
  from the pinned Ultralytics release and verified by SHA-256.
- `visdrone-yolov11s.onnx` — YOLO11s VisDrone detector (10 aerial classes:
  pedestrian, people, bicycle, car, van, truck, tricycle, awning-tricycle, bus,
  motor), fetched at build time from the pinned source and verified by SHA-256.
- `deepforest.onnx` — DeepForest tree-crown detector (1 class `tree`), an
  ONNX export of the MIT-licensed DeepForest RetinaNet model, pinned by SHA-256.
- `coco.txt` / `visdrone.txt` / `tree.txt` — the matching class labels.

## Choosing a model

- **Ground-level / general**: `yolov8n.onnx` + `coco.txt` (default; weak on
  nadir imagery).
- **Aerial vehicles/people**: `visdrone-yolov11s.onnx` + `visdrone.txt`.
- **Trees (airborne RGB)**: `deepforest.onnx` + `tree.txt`. This is a
  torchvision-style model, so set `family = torchvision` (or leave `auto`),
  `label_offset = 0`, and `tile_size = 256` (its native input). Score
  thresholds are low for tree crowns — use `confidence` around 0.2–0.4.

The default platform model is configurable with
`OBJECT_DETECTION_DEFAULT_MODEL` / `OBJECT_DETECTION_DEFAULT_LABELS`.

Recommended aerial parameters: `tile_size_m`/`overlap_m` (ground metres) so
object scale is consistent across GSDs, `overlap_m` at least the largest object
size, and `confidence` around 0.4–0.55.

## Adding models

Drop additional `.onnx` detectors and their label files into the models
directory (mount a volume there, or set `OBJECT_DETECTION_MODELS_DIR` to a
mounted path). The model must expose exactly one image input `(N, C, H, W)`
with `C` in `{1, 3}` and one YOLO-style detection output
`(N, 4 + num_classes, anchors)`; the label file must have exactly
`num_classes` lines.

