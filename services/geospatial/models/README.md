# Analysis models

The analysis operations read their ONNX models and label files from the managed
models directory (`OBJECT_DETECTION_MODELS_DIR`, default `/opt/webodm/models`).
Configuration only ever names an asset *within* this directory; absolute paths,
parent traversal, and symlink escapes are rejected.

## Object detection — default

The geospatial image provisions:

- `yolov8n.onnx` — YOLOv8n COCO detector (80 classes), fetched at build time
  from the pinned Ultralytics release and verified by SHA-256.
- `visdrone-yolov11s.onnx` — YOLO11s VisDrone detector (10 aerial classes:
  pedestrian, people, bicycle, car, van, truck, tricycle, awning-tricycle, bus,
  motor), fetched at build time from the pinned source and verified by SHA-256.
- `deepforest.onnx` — DeepForest tree-crown detector (1 class `tree`), an
  ONNX export of the MIT-licensed DeepForest RetinaNet model, pinned by SHA-256.
- `coco.txt` / `visdrone.txt` / `tree.txt` — the matching class labels.

### Choosing a detector

- **Ground-level / general**: `yolov8n.onnx` + `coco.txt` (default; weak on
  nadir imagery).
- **Aerial vehicles/people**: `visdrone-yolov11s.onnx` + `visdrone.txt`.
- **Trees (airborne RGB)**: `deepforest.onnx` + `tree.txt`. This is a
  torchvision-style model, so set `family = torchvision` (or leave `auto`),
  `label_offset = 0`, and `tile_size = 256` (its native input). Score
  thresholds are low for tree crowns — use `confidence` around 0.2–0.4.

The default platform detector is configurable with
`OBJECT_DETECTION_DEFAULT_MODEL` / `OBJECT_DETECTION_DEFAULT_LABELS`.

Recommended aerial parameters: `tile_size_m`/`overlap_m` (ground metres) so
object scale is consistent across GSDs, `overlap_m` at least the largest object
size, and `confidence` around 0.4–0.55.

## Semantic segmentation — default

The image provisions:

- `segformer-satellite-landcover.onnx` — SegFormer-B0 semantic segmentation
  exported to ONNX from
  [`Pranilllllll/segformer-satellite-segementation`](https://huggingface.co/Pranilllllll/segformer-satellite-segementation)
  (**MIT**), verified by SHA-256. The exported graph takes `[0,1]` RGB
  (`[1, 3, 512, 512]`), image-net-normalises internally, and returns per-class
  probabilities at input resolution (`[1, 7, 512, 512]`).
- `satellite-landcover.txt` — the 7 classes (line order matches the model's
  class ids): `background`, `residential_area`, `road`, `river`, `forest`,
  `unused_land`, `agricultural_area`.

`background` is treated as the background class by the operation and is not
emitted as a region; the other six classes appear as filled polygons.

> **Domain and licensing note.** This model was fine-tuned on satellite imagery
> over Kathmandu Valley, so its accuracy is best on similar urban/suburban
> scenes and it should be validated before production use elsewhere. The model
> repo declares the MIT licence, which permits commercial use. Most other free
> aerial segmentation models are **not** commercially usable — for example
> Ramp/`model_ramp_baseline` is CC BY-NC 4.0, LoveDA and xView are
> CC BY-NC-SA 4.0, and geodeep / `vhr-buildings` are AGPL-3.0. Check the licence
> before adding any model.

The default segmentation model is configurable with
`SEGMENTATION_DEFAULT_MODEL` / `SEGMENTATION_DEFAULT_LABELS`.

Recommended parameters: `threshold` around 0.5, `tile_size` matching the
model's input (512 here), `overlap` around 64, and `min_segment_area` to
suppress mask speckle. `tile_size_m`/`overlap_m` set the tile by ground size so
region scale is consistent across GSDs; `simplify_tolerance` smooths polygon
boundaries.

## Adding models

Drop additional `.onnx` models and their label files into the models directory
(mount a volume there, or set `OBJECT_DETECTION_MODELS_DIR` to a mounted path),
then select them by name in the plugin settings or run parameters.

The model must expose exactly one image input `(N, C, H, W)` with `C` in
`{1, 3}` and one supported output:

- **Detection**: a YOLO-style output `(N, 4 + num_classes, anchors)`, a
  torchvision-style `boxes`/`scores`/`labels` triple, or both (detection must be
  disambiguated with `family`).
- **Segmentation**: a rank-4 per-class mask output `(N, num_classes, H, W)`
  whose `num_classes` matches the label file. The mask must be at the model's
  input resolution; models that emit a downsampled mask (for example base
  SegFormer at `H/4`) must upsample before export, as
  `segformer-satellite-landcover.onnx` does.

Label files have one class per line, in class-id order; the count must match the
model's class count.
