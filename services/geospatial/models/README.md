# Detection models

The object-detection operation reads its ONNX model and label file from the
managed models directory (`OBJECT_DETECTION_MODELS_DIR`, default
`/opt/webodm/models`). Configuration only ever names an asset *within* this
directory; absolute paths and traversal are rejected.

## Default

The geospatial image provisions:

- `yolov8n.onnx` — YOLOv8n COCO detector, fetched at build time from the pinned
  Ultralytics release and verified by SHA-256 (`b2bc52f4…`).
- `coco.txt` — the 80 COCO class names, one per line.

Both are the defaults for the `object-detection` operation's `model` and
`labels` parameters; an organization may override them by name via plugin
settings.

## Adding models

Drop additional `.onnx` detectors and their label files into the models
directory (mount a volume there, or set `OBJECT_DETECTION_MODELS_DIR` to a
mounted path). The model must expose exactly one image input `(N, C, H, W)`
with `C` in `{1, 3}` and one YOLO-style detection output
`(N, 4 + num_classes, anchors)`; the label file must have exactly
`num_classes` lines.
