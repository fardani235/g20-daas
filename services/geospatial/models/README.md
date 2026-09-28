# Analysis models

The semantic-segmentation operation reads its ONNX model and label file from
the managed models directory (`OBJECT_DETECTION_MODELS_DIR`, default
`/opt/webodm/models`; the variable name predates object detection moving out
of this service and is kept so existing deployments keep working).
Configuration only ever names an asset *within* this directory; absolute
paths, parent traversal, and symlink escapes are rejected.

Object detection is no longer a system operation. It is a **user plugin**
(`plugins/object-detection/`, docs in `docs/plugins/object-detection.md`) that
each organization uploads with its own detector models, so no detector weights
are provisioned here.

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
`{1, 3}` and a rank-4 per-class mask output `(N, num_classes, H, W)` whose
`num_classes` matches the label file. The mask must be at the model's input
resolution; models that emit a downsampled mask (for example base SegFormer at
`H/4`) must upsample before export, as `segformer-satellite-landcover.onnx`
does.

Label files have one class per line, in class-id order; the count must match the
model's class count.
