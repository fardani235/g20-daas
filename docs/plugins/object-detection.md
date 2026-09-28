# Object Detection plugin

A **user plugin** (see the [user plugin guide](user-plugin-guide.md)) that
finds objects — tree crowns, vehicles, people, whatever your detector was
trained for — on a completed task's **orthophoto** and returns their bounding
boxes as a GeoJSON layer. Detectors are pluggable: each is described by a
*model card* and can be replaced or added without touching the plugin code,
so an organization brings its own models. Source:
[`plugins/object-detection/`](../../plugins/object-detection/).

| | |
|---|---|
| Input | `orthophoto` (RGB, 8-bit; a fourth alpha band or nodata marks the area to skip) |
| Output | GeoJSON `FeatureCollection` (EPSG:4326), one bounding-box polygon per detection with `class`, `class_id`, `confidence`, `area_m2`; `render_kind: detections` (class-coloured overlay + legend on the map) |
| Models | Model cards in `models/`; shipped cards: DeepForest tree crowns (torchvision, MIT — default), VisDrone YOLO11s vehicles & people (YOLO, AGPL-3.0). Weights are **not committed**: fetched at build time, or supplied by you |
| Runs in | the plugin sandbox (`services/plugin-runner`): numpy, rasterio, Pillow, onnxruntime (CPU) |

This replaces the former *Object Detection* system operation of the geospatial
service. Existing runs of that operation stay visible: the catalog sync marks
the old row unavailable and keeps its run history.

---

## 1. Install

```bash
cd plugins/object-detection

# Fetch the weights the shipped cards describe (none are in git). Prints each
# model's licence; skip any you do not want in your package.
python3 tools/fetch_models.py                  # both models (~170 MB)
python3 tools/fetch_models.py --skip visdrone-yolov11s   # MIT only

tools/build.sh            # -> plugins/object-detection-1.0.0.zip
```

Upload the zip on the **Plugins** page (organization owner) or with the API:

```bash
BASE=https://your.webodm.host
curl -c cj.txt -X POST "$BASE/api/method/login" -F usr=you@example.com -F pwd=secret
CSRF=$(curl -b cj.txt -s "$BASE/api/method/webodm_core.api.csrf.get_token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')
curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -F "file=@../object-detection-1.0.0.zip" \
     "$BASE/api/method/webodm_core.api.plugins.upload_plugin"
```

The plugin is installed as `<org-slug>.object-detection` and enabled for your
organization. Re-uploading a package with a higher `version` upgrades it in
place (settings and run history are kept). A package built without fetching a
card's weights is still valid: that model is reported as *unavailable* when
selected, with the instruction to fetch or supply the file, and the other
models run.

### Licences

- `deepforest-tree-crowns` — [DeepForest](https://github.com/weecology/DeepForest)
  RetinaNet, **MIT**. Trained on 10 cm airborne RGB (NEON); good on woodland
  and orchards, weaker on dense urban canopy.
- `visdrone-yolov11s` — YOLO11s trained on VisDrone with the Ultralytics
  framework, so the weights are **AGPL-3.0**. Fetch them only if that licence
  suits your deployment; otherwise skip the card or replace it with a detector
  of your own. Nothing AGPL is committed to this repository.

---

## 2. Run it

Open a completed task on the map and choose *Object Detection* in the analysis
panel. Pick the **Model**, leave the thresholds and tile settings at their
defaults (0 / -1 = *use the model card's recommended values*) or tune them, and
run. The run's status (Queued → Running → Completed/Failed), its log and the
error message (if any) are shown in the panel; the result is added as a map
layer coloured by class, with a legend, and can be downloaded as GeoJSON.

From the command line (`$TASK` is a completed task with an orthophoto):

```bash
run() {  # $1 = plugin id, $2 = params JSON
  curl -s -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
       -d "{\"plugin\":\"$1\",\"task\":\"$TASK\",\"params\":$2}" \
       "$BASE/api/method/webodm_core.api.plugins.run_plugin"
}
run myorg.object-detection '{"model":"deepforest-tree-crowns"}'
run myorg.object-detection '{"model":"visdrone-yolov11s","confidence":0.5,"class_filter":"car,truck,bus"}'
```

### Local, without the platform

```bash
cd plugins/object-detection
python -m pytest -q                                    # 100+ tests, synthetic data
# one run through the real sandbox code path:
cd ../../services/plugin-runner
PLUGIN_PYTHON=$PY $PY -m app.cli ../../plugins/object-detection \
    --input orthophoto=/data/odm_orthophoto.tif --param model=visdrone-yolov11s \
    --output-kind vector --output /tmp/detections.geojson
```

---

## 3. Parameters

All numeric parameters default to "use the model card": `0` (or `-1` for the
overlaps) means *recommended by the selected model*.

| Parameter | Default | Meaning |
|---|---|---|
| `model` | first card marked `default` | Which model card to run. The enum in `plugin.json` is generated from `models/*.json` by `tools/update_manifest.py` (run by `build.sh`) |
| `confidence` | card / 0.25 | Minimum class confidence. Tree-crown detectors want 0.2–0.4; YOLO vehicle detectors 0.4–0.55 on nadir imagery |
| `iou` | card / 0.45 | Per-class NMS threshold used to merge duplicates across overlapping tiles |
| `tile_size` | card / model input | Tile edge in orthophoto pixels; every tile is letterboxed into the model input |
| `overlap` | card / 64 | Tile overlap in pixels; at least the largest object, so a box cut by one tile edge is whole in its neighbour |
| `tile_size_m`, `overlap_m` | card / unset | Tile and overlap in **ground metres**, converted to pixels from the orthophoto's GSD. Object scale then stays constant across 2 cm and 5 cm surveys; the shipped cards set them (DeepForest 40 m ≈ its 400 px training window at 10 cm) |
| `max_detections` | 5000 | Keep the most confident N boxes |
| `class_filter` | all | Comma-separated class names to keep (names from the card) |

---

## 4. Results and how to access them

Each feature is the detection's bounding box as a polygon in EPSG:4326 with:

```json
{ "class": "car", "class_id": 3, "confidence": 0.8123, "area_m2": 8.55 }
```

Run metadata (`output_metadata` on the run, shown in the panel) records the
model card summary, model family and input size, the orthophoto's size/GSD/CRS,
the tiling used (count, size, overlap, tiles skipped as nodata), effective
parameters, per-class `counts`, `total`, the number of raw `candidates` before
NMS, a `legend` (class → colour from the card) and stage timings/warnings.
The GeoJSON is downloadable from the run panel or with
`webodm_core.api.plugins.get_run_geojson`.

---

## 5. How it works

1. **Orthophoto** — opened with rasterio; must be georeferenced (boxes are
   reported in EPSG:4326). GSD in metres is derived from the CRS (projected
   units, or an approximation at the centre latitude for geographic CRSs).
2. **Model** — the card is resolved (unknown/unavailable models fail with a
   readable message), the weights are checksum-verified when the card carries
   a `sha256`, and the ONNX graph is inspected: exactly one image input, and
   either a YOLO tensor `(N, 4+classes, anchors)` or torchvision
   `boxes/scores/labels` outputs. A class count that differs from the card is
   an error naming both numbers.
3. **Tiling** — windows of `tile_size` (or `tile_size_m / GSD`) with overlap.
   Tiles whose valid-pixel fraction (alpha / nodata mask) is below 2 % are
   skipped. Each tile is letterboxed (aspect-preserving resize on a grey
   canvas) into the model input and normalised per family.
4. **Decoding & mapping** — boxes are decoded per family and mapped back to
   raster pixels through the letterbox scale/padding. Boxes centred in the
   padding, or clipped at an *interior* tile edge (their neighbour sees them
   whole), are dropped — these are the classic tiling artifacts.
5. **Merging** — greedy per-class NMS over all tiles removes duplicates in the
   overlaps; the result is capped at `max_detections` and written as GeoJSON.

No pre-run validation exists for user plugins by design: a bad or mismatched
model fails the run with a clear error the customer can read in the panel.

### Assumptions and limits

- Detectors see 8-bit RGB. A 16-bit orthophoto is clipped to 0–255 with a
  warning; convert it first for meaningful results.
- CPU inference. DeepForest (RetinaNet-ResNet50) takes ~4 s per 800 px tile;
  a 1 ha survey at 5 cm is a few dozen tiles. YOLO11s is ~0.5 s per tile. The
  manifest allows one hour per run.
- Memory: the sandbox caps address space at 2 GB; ONNX Runtime runs
  single-threaded here and the tiled reads keep GDAL's cache at 128 MB.
- Package size: Frappe accepts 256 MB zipped / 1 GB extracted. Both shipped
  models together are ~150 MB zipped.

---

## 6. Adding or replacing a model

1. Export to ONNX. Ultralytics: `yolo export model=best.pt format=onnx imgsz=640`
   (fixed input size preferred — dynamic H/W falls back to 640). Torchvision:
   `torch.onnx.export(model, image, ..., input_names=["image"], output_names=["boxes","scores","labels"])`.
2. Put `my-detector.onnx` in `models/` and add `models/my-detector.json`:

   ```json
   {
     "id": "my-detector",
     "label": "My detector — solar panels",
     "description": "What it was trained on, expected GSD, known weaknesses.",
     "file": "my-detector.onnx",
     "sha256": "<optional: sha256sum my-detector.onnx>",
     "family": "auto",
     "classes": ["panel"],
     "label_offset": 0,
     "normalize": "unit",
     "recommended": { "confidence": 0.4, "iou": 0.5, "tile_size_m": 25, "overlap_m": 5 },
     "colors": { "panel": "#1f77b4" },
     "source": { "name": "in-house", "license": "proprietary" }
   }
   ```

   - `classes` in class-id order, count equal to the model's classes.
   - `label_offset: 1` for standard torchvision detectors (label 0 =
     background); `0` for YOLO and DeepForest exports.
   - `normalize`: `unit` ([0, 1], YOLO default) or `imagenet` (torchvision
     default).
   - `default: true` on at most one card makes it the preselected model.
   - Adding `source.url` + `sha256` lets `tools/fetch_models.py` download it
     for colleagues instead of committing the weights.
3. `tools/build.sh` (regenerates the `model` enum in `plugin.json`, packages
   the zip) and upload. `python -m pytest -q tests/test_registry.py
   tests/test_manifest.py` checks the card and the manifest.

---

## 7. References

- Weinstein, B.G. et al. (2019). *Individual tree-crown detection in RGB imagery
  using semi-supervised deep learning neural networks.* Remote Sensing 11(11).
  (DeepForest.)
- Zhu, P. et al. (2021). *Detection and Tracking Meet Drones Challenge.* IEEE
  TPAMI. (VisDrone.)
- Jocher, G. et al. Ultralytics YOLO (AGPL-3.0) — export format for the YOLO
  family.
- Akyon, F.C. et al. (2022). *Slicing Aided Hyper Inference and Fine-tuning
  for Small Object Detection.* ICIP. (Tiled inference with overlap and merged
  NMS, as done here.)
