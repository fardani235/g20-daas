# Semantic Segmentation plugin

A **user plugin** (see the [user plugin guide](user-plugin-guide.md)) that
classifies a completed task's drone-mapping outputs — orthophoto, DSM, DTM, or
any combination — into a **class mask** (GeoTIFF, primary result) or **class
polygons** (GeoJSON, optional variant). Models are pluggable: each is described
by a *model card* and can be replaced or added without touching the plugin
code. Source: [`plugins/semantic-segmentation/`](../../plugins/semantic-segmentation/).

| | |
|---|---|
| Inputs | `orthophoto`, `dsm`, `dtm` — all optional, pick any subset in the run dialog (at least one) |
| Output (mask package) | single-band uint8 GeoTIFF of class ids, nodata 255, embedded colour table and class table tags; rendered with its own colours on the map |
| Output (polygons package) | GeoJSON `FeatureCollection` (EPSG:4326), one polygon per classified region with `class`, `class_id`, `area` (m²) |
| Models shipped | FLAIR U-Net (aerial RGB, default), SegFormer-B0 satellite land cover (RGB), nDSM height classes (DSM ± DTM ± RGB), geomorphon landforms (DTM or DSM) |
| Runs in | the plugin sandbox (`services/plugin-runner`), which provides numpy, rasterio, shapely and onnxruntime |

---

## 1. Install

```bash
cd plugins/semantic-segmentation

# One-off: fetch + export the FLAIR weights (98 MB, not committed). Needs torch.
python3 -m venv /tmp/exportvenv
/tmp/exportvenv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
/tmp/exportvenv/bin/pip install segmentation-models-pytorch onnx
/tmp/exportvenv/bin/python tools/fetch_models.py

# Build the two packages (mask + polygons)
tools/build.sh            # -> plugins/semantic-segmentation-1.0.0.zip
                          #    plugins/semantic-segmentation-polygons-1.0.0.zip
```

Upload each zip on the **Plugins** page (organization owner) or with the API:

```bash
BASE=https://your.webodm.host
curl -c cj.txt -X POST "$BASE/api/method/login" -F usr=you@example.com -F pwd=secret
CSRF=$(curl -b cj.txt -s "$BASE/api/method/webodm_core.api.csrf.get_token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')
curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -F "file=@../semantic-segmentation-1.0.0.zip" \
     "$BASE/api/method/webodm_core.api.plugins.upload_plugin"
```

The plugin is installed as `<org-slug>.semantic-segmentation` and enabled for
your organization. Re-uploading a package with a higher `version` **upgrades**
it in place (settings and run history are kept). Skipping `fetch_models.py`
still gives a working package: the FLAIR card is reported as unavailable and
`auto` falls back to the SegFormer model.

---

## 2. Run it

Open a completed task on the map, choose *Semantic Segmentation* in the
analysis panel. The run dialog shows one selector per input (Orthophoto, DSM,
DTM) listing the datasets the task has, plus the parameters. Leave **Model** on
`auto` to let the plugin pick the best model for the datasets you selected, or
choose one explicitly. The run's status (Queued → Running → Completed/Failed),
its log and the error message (if any) are shown in the panel; the result is
added as a map layer and can be downloaded.

The same runs from the command line. `inputs` names which task dataset feeds
each plugin input; leave one out (or send `null`) to exclude it.

```bash
run() {  # $1 = plugin id, $2 = inputs JSON, $3 = params JSON
  curl -s -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
       -d "{\"plugin\":\"$1\",\"task\":\"$TASK\",\"inputs\":$2,\"params\":$3}" \
       "$BASE/api/method/webodm_core.api.plugins.run_plugin"
}
P=acme.semantic-segmentation
```

### Orthophoto only

Land cover from RGB. `auto` → **FLAIR U-Net** at its native 0.2 m.

```bash
run $P '{"orthophoto":"orthophoto","dsm":null,"dtm":null}' '{}'
# explicit model / coarser & faster:
run $P '{"orthophoto":"orthophoto"}' '{"model":"flair-rgb-resnet34-unet","resolution_m":0.3}'
```

Classes: building, pervious_surface, impervious_surface, bare_soil, water,
coniferous, deciduous, brushwood, vineyard, herbaceous_vegetation,
agricultural_land, plowed_land, swimming_pool, snow (+ rarely predicted
clear_cut, mixed, ligneous, greenhouse, other).

### DSM only

Height above ground from the surface model alone. `auto` → **height-classes**;
the terrain is estimated from the DSM by a morphological opening
(`ground_window_m`, default 40 m — make it larger than your widest building).

```bash
run $P '{"dsm":"dsm"}' '{}'
run $P '{"dsm":"dsm"}' '{"low_threshold_m":0.3,"tall_threshold_m":3,"ground_window_m":60}'
```

Classes: ground, low_object (0.5–2.5 m), high_object (> 2.5 m). The run
metadata carries the warning that the ground was estimated.

### DTM only

Landforms from the terrain model. `auto` → **geomorphons** at 1 m.

```bash
run $P '{"dtm":"dtm"}' '{}'
run $P '{"dtm":"dtm"}' '{"geomorphon_search_m":30,"geomorphon_flatness_deg":2,"resolution_m":2}'
```

Classes: flat, peak, ridge, shoulder, spur, slope, hollow, footslope, valley,
pit. On nearly flat sites raise `geomorphon_flatness_deg` (2–5°) or the
lookup distance to suppress micro-relief.

### Orthophoto + DSM + DTM

The full multi-source case. `auto` → **FLAIR U-Net with height fusion**: the
RGB posterior is re-weighted by the exact height above ground (DSM − DTM),
which removes "buildings" on bare fields and "trees" on lawns and vice versa.

```bash
run $P '{"orthophoto":"orthophoto","dsm":"dsm","dtm":"dtm"}' '{}'
# stronger height influence, custom elevated threshold
run $P '{"orthophoto":"orthophoto","dsm":"dsm","dtm":"dtm"}' '{"height_weight":1.5,"elevated_threshold_m":2.5}'
# interpretable rule model on the same three inputs (vegetation vs structure by height + greenness)
run $P '{"orthophoto":"orthophoto","dsm":"dsm","dtm":"dtm"}' '{"model":"height-classes"}'
# polygons instead of a mask
run acme.semantic-segmentation-polygons '{"orthophoto":"orthophoto","dsm":"dsm","dtm":"dtm"}' '{"min_segment_area_m2":5,"simplify_tolerance_m":0.3}'
```

Other combinations work the same way: orthophoto + DSM (height fusion with an
estimated ground), DSM + DTM (exact nDSM height classes), DSM + orthophoto with
`height-classes` (splits low/high objects into vegetation vs. structures).

### Local, without the platform

```bash
cd services/plugin-runner && python -m app.cli ../../plugins/semantic-segmentation \
    --input orthophoto=/data/odm_orthophoto.tif --input dsm=/data/dsm.tif --input dtm=/data/dtm.tif \
    --param model=auto --output /tmp/mask.tif --timeout 1800
```

---

## 3. Which model for which inputs

| Model (`model=`) | Needs | Uses if selected | Method |
|---|---|---|---|
| `flair-rgb-resnet34-unet` (auto default with an orthophoto) | orthophoto | dsm, dtm → height fusion | U-Net/ResNet-34 trained on 0.2 m aerial RGB (IGN FLAIR), ONNX |
| `landcover-segformer-b0` | orthophoto | dsm, dtm → height fusion | SegFormer-B0 fine-tuned on satellite imagery (7 classes), ONNX; lighter, weaker on drone imagery |
| `height-classes` (auto default with a DSM and no orthophoto) | dsm | dtm (exact nDSM), orthophoto (vegetation split) | nDSM thresholds + Excess Green index |
| `geomorphons` (auto default with only a DTM) | dtm **or** dsm | — | Jasiewicz & Stepinski landforms |

`auto` chooses the compatible model with the highest priority (FLAIR >
SegFormer > height classes > geomorphons). An explicit model that cannot run on
the selected inputs is refused with a message naming what it needs; inputs a
model does not use are reported as ignored in the run metadata.

---

## 4. Parameters

| Parameter | Default | Applies to | Meaning |
|---|---|---|---|
| `model` | `auto` | all | model card id or `auto` |
| `resolution_m` | 0 = model's recommended (0.2 / 0.3 / 0.25 / 1.0 m) | all | processing GSD; never finer than the finest input; coarsened automatically above 60 Mpx |
| `tile_size`, `overlap` | model default (512/64 ONNX, 1024/0 height, 512/0 geomorphons) | all | tiling; overlap is blended with a Hann window |
| `min_segment_area_m2` | 2 | all | regions smaller than this are merged into neighbours (sieve); polygons below it are dropped |
| `smoothing_radius_px` | 1 | all | majority filter radius, 0 = off |
| `class_filter` | — | all | comma-separated class names to keep; others become background |
| `height_weight` | 1.0 | RGB models with a DSM | strength of height fusion, 0 = off |
| `elevated_threshold_m` | model's (2.0 m) | RGB models with a DSM | height separating elevated from ground classes |
| `ground_window_m` | 40 | DSM without DTM | opening window of the ground estimate |
| `low_threshold_m`, `tall_threshold_m` | 0.5, 2.5 | height-classes | class boundaries |
| `vegetation_index_threshold` | 0.06 | height-classes + orthophoto | ExG above → vegetation |
| `geomorphon_search_m`, `geomorphon_flatness_deg` | 20 m, 1° | geomorphons | lookup distance L and flatness t |
| `simplify_tolerance_m` | 0.25 | polygons package | Douglas–Peucker tolerance |

Organization defaults for any of these are saved from the Plugins page (gear
icon) and applied to every run unless overridden.

---

## 5. Results and how to access them

- **Mask** — `GET /api/method/webodm_core.api.plugins.download_run_output?run_name=…`
  downloads the GeoTIFF; `…tiles.serve_run?run_name=…&z={z}&x={x}&y={y}` serves
  map tiles coloured by the embedded palette. Class names, ids and colours are
  in the file's `SEGMENTATION_CLASSES` tag and in the run metadata (`legend`).
- **Polygons** — `…plugins.get_run_geojson?run_name=…` returns the GeoJSON (the
  map styles it by class with a legend); the download endpoint gives the file.
- **Metadata** (`get_run` → `output_metadata`): model, inputs used/ignored,
  `height_mode` (`dsm-dtm`, `dsm-estimated-ground` or none), processing grid
  (EPSG, resolution, size), tile count, effective parameters, per-class pixel
  counts / areas / fractions, warnings, stage timings, and the runner's extent
  and bounds.
- **Failures** — the run is `Failed` with a one-line reason: no inputs
  selected, inputs that do not overlap, an unreadable raster, an incompatible
  model/inputs pair, a bad `class_filter` name, a missing model file, or a
  timeout. The stderr tail (stage log) is attached.

---

## 6. How it works

1. **Inputs.** Each selected dataset is opened and checked (CRS present, an
   orthophoto has ≥ 3 bands, elevation rasters have 1). Nodata, alpha bands and
   ODM's −9999 sentinel become "no data".
2. **Processing grid.** The CRS of the finest input used by the model, the
   intersection of the model's primary inputs, at the requested/recommended
   resolution. Every input is read through a rasterio `WarpedVRT` on that grid
   (from the matching overview level), so different resolutions, extents and
   CRSs line up pixel for pixel. Optional inputs contribute nodata where they
   have no coverage.
3. **Height.** With DSM + DTM the nDSM is exact. With only a DSM the terrain is
   estimated by a grey-scale morphological opening in ground metres.
4. **Tiled prediction.** Fixed tiles with overlap; per-tile class scores are
   blended with a Hann window and accumulated per tile row, so memory is flat
   for any raster size. ONNX models get exactly their static input size
   (small rasters are reflect-padded); geomorphons read a halo of the lookup
   distance around each tile.
5. **Fusion.** For RGB models with a DSM, the posterior is multiplied by a
   per-class height likelihood (naive-Bayes late fusion, see §7).
6. **Post-processing.** Majority filter → sieve by minimum area → optional
   class filter.
7. **Output.** Mask GeoTIFF with palette and tags, or polygons via
   `rasterio.features.shapes` → shapely simplify → EPSG:4326.

---

## 7. Models, enhancements and references

### Models

- **FLAIR U-Net/ResNet-34** — IGN, [`IGNF/FLAIR-INC_rgb_15cl_resnet34-unet`](https://huggingface.co/IGNF/FLAIR-INC_rgb_15cl_resnet34-unet), Etalab-2.0 licence.
  Trained on 512×512 patches of 0.2 m RGB aerial orthophotos over France
  (FLAIR #1, 19-class nomenclature; mIoU 59 on the 15 evaluated classes).
  Chosen because very-high-resolution nadir aerial RGB is the same domain as
  drone orthophotos; on our test site it separates buildings, paved surfaces,
  lawns and trees where the satellite model does not. Exported with
  `tools/export_flair_unet.py` (normalisation baked in, softmax output).
  *Garioud, A. et al. (2023). FLAIR: a Country-Scale Land Cover Semantic
  Segmentation Dataset From Multi-Source Optical Imagery. NeurIPS 2023 Datasets
  & Benchmarks. arXiv:2310.13336.*
- **SegFormer-B0 satellite land cover** — [`Pranilllllll/segformer-satellite-segementation`](https://huggingface.co/Pranilllllll/segformer-satellite-segementation), MIT.
  Kept as a small (15 MB) fallback and as a second example of the ONNX
  contract. Fine-tuned on satellite imagery (Kathmandu valley); expect domain
  shift on drone imagery. *Xie, E. et al. (2021). SegFormer: Simple and
  Efficient Design for Semantic Segmentation with Transformers. NeurIPS.*
- **Height classes** — rule model: nDSM thresholds (ground / low / high) and,
  with RGB, the Excess Green index to split vegetation from structures. This
  is the classical object-extraction baseline from photogrammetric surface
  models. *Weidner, U. & Förstner, W. (1995). Towards automatic building
  extraction from high-resolution digital elevation models. ISPRS J. 50(4).*
  *Rottensteiner, F. et al. (2014). Results of the ISPRS benchmark on urban
  object detection and 3D building reconstruction. ISPRS J. 93.* *Woebbecke,
  D.M. et al. (1995). Color indices for weed identification under various soil,
  residue, and lighting conditions. Trans. ASAE 38(1).*
- **Geomorphons** — deterministic landform classification from one elevation
  raster, as in GRASS `r.geomorphon` / WhiteboxTools. *Jasiewicz, J. &
  Stepinski, T.F. (2013). Geomorphons — a pattern recognition approach to
  classification and mapping of landforms. Geomorphology 182, 147–156.*

### Enhancements for drone/geospatial data (and why)

- **Height fusion (nDSM late fusion).** RGB models confuse classes that look
  alike from above but differ in height (flat roof vs. road, tree crown vs.
  lawn). The multimodal remote-sensing literature adds the normalized DSM
  either as a network input or by fusing predictions; Audebert et al. show on
  the ISPRS Vaihingen/Potsdam benchmarks that late fusion is the more robust
  form. Since the shipped RGB models were not trained with height, the plugin
  does decision-level fusion: `P(c|rgb,h) ∝ P(c|rgb)·P(h|c)^w`, where `P(h|c)`
  is a logistic step around the elevated threshold for classes the card marks
  `elevated`/`ground` (floored so height never vetoes a confident RGB
  prediction) and `w` is `height_weight`. Model cards can also declare an
  `ndsm` input channel for networks trained with height (early fusion) — no
  code change needed. *Audebert, N., Le Saux, B. & Lefèvre, S. (2018). Beyond
  RGB: Very high resolution urban remote sensing with multimodal deep networks.
  ISPRS J. 140, 20–32.* *Gerke, M. (2014). Use of the Stair Vision Library
  within the ISPRS 2D Semantic Labeling Benchmark (Vaihingen).*
- **Ground estimation without a DTM.** Grey morphological opening of the DSM
  with a window in ground metres (Weidner & Förstner 1995; the first stage of
  Zhang et al.'s progressive morphological filter). *Zhang, K. et al. (2003).
  A progressive morphological filter for removing nonground measurements from
  airborne LIDAR data. IEEE TGRS 41(4).* Limitation: on steep terrain the
  estimate lags the slope; select the DTM when you have one.
- **Hann-window tile blending.** Overlapping tiles blended with a raised-cosine
  window remove seams and duplicated boundaries at tile edges. *Pielawski, N.
  & Wählby, C. (2020). Introducing Hann windows for reducing edge-effects in
  patch-based image segmentation. PLOS ONE 15(3).* *Huang, B. et al. (2018).
  Tiling and stitching segmentation output for remote sensing: basic
  challenges and recommendations. arXiv:1805.12219.*
- **GSD-independent parameters.** Everything spatial (search distance, opening
  window, minimum area, tolerance) is in ground metres and the processing
  resolution defaults to the model's training GSD, so behaviour does not depend
  on the flight altitude.

### Assumptions and limits

- Inputs are georeferenced rasters (ODM's outputs are); geographic CRSs are
  handled with an approximate metres-per-degree at the scene centre.
- The processing grid is capped at 60 megapixels (coarsened with a warning)
  so runs stay inside the sandbox's 2 GB address space; a 5 cm orthophoto of
  37 Mpx processed at 0.2 m is ~2.3 Mpx and takes ~40 s on a CPU.
- No model here is trained on your site. FLAIR is trained on France at 0.2 m;
  its land-cover semantics (e.g. "pervious surface") are IGN's. Validate on a
  few tasks before relying on class areas; the polygons package and the class
  filter make spot checks easy.
- ONNX Runtime must be present in the sandbox image (it is in
  `services/plugin-runner`); otherwise ONNX cards report a clear error and the
  rule-based models still work.

---

## 8. Adding or replacing a model

Drop a card into `models/` (and, for ONNX, the `.onnx` file next to it), then
list its id in the `model` enum of `plugin.json`, bump `version`, rebuild and
re-upload. Card fields:

```json
{
  "id": "my-model", "label": "…", "description": "…",
  "backend": "onnx",                          // onnx | height | geomorphon
  "auto_priority": 50,                        // higher wins for model=auto
  "inputs": { "required": ["orthophoto"], "optional": ["dsm", "dtm"] },
  "onnx": {
    "file": "my-model.onnx", "sha256": "…",
    "input": "pixel_values", "output": "logits", "output_type": "logits",   // probabilities | logits | labels
    "channels": ["red", "green", "blue", "ndsm"],                          // early fusion: add ndsm/elevation
    "scale": "unit", "mean": [0.485, 0.456, 0.406, 0.0], "std": [0.229, 0.224, 0.225, 1.0],
    "input_size": 512
  },
  "classes": [ { "id": 0, "name": "background", "color": "#000000", "background": true }, … ],
  "height_fusion": { "elevated_threshold_m": 2.0, "classes": { "building": "elevated", "road": "ground" } },
  "recommended": { "resolution_m": 0.2, "tile_size": 512, "overlap": 64 }
}
```

The graph must be NCHW with a static square input; outputs are per-class
scores (NCHW, resized to the tile if the model emits a lower resolution) or a
label map. `tests/` show how to unit-test a card. New backends are one class in
`segplugin/backends/` plus a line in `build_backend`.
