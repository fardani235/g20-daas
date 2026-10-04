# Building Footprints plugin

A **user plugin** (see the [user plugin guide](user-plugin-guide.md)) that
extracts **one polygon per building** from a completed task's **orthophoto**
and returns the footprints as a GeoJSON layer. Touching or adjacent buildings
(terraces, annexes, dense settlements) come out as *separate* polygons: the
model predicts a signed distance to the nearest building edge next to the
building mask, and a watershed on that distance plane splits a merged blob
along the shared walls. Source:
[`plugins/building-footprints/`](../../plugins/building-footprints/).

| | |
|---|---|
| Input | `orthophoto` (RGB, 8-bit; a fourth alpha band or nodata marks the area to skip) |
| Output | GeoJSON `FeatureCollection` (EPSG:4326), one polygon per building with `class` (`building`), `score` (mean building probability), `area` (m²), `regularized`, `instance_id`; `render_kind: segmentation` (filled class-coloured overlay + legend on the map, no frontend change) |
| Model | Model cards in `models/`; shipped card: **HOT DINOv3-S buildings** (`kshitijrajsharma/dinov3-hot-buildings`, Apache-2.0). Weights (220 MB) are **not committed**: `tools/fetch_models.py` downloads and sha-256-verifies them at build time |
| Runs in | the plugin sandbox (`services/plugin-runner`): numpy, scipy, scikit-image, rasterio, shapely, onnxruntime (CPU). **scipy and scikit-image were added to the image for this plugin** — rebuild/pull the plugin-runner image before uploading it |

---

## 1. Install

```bash
cd plugins/building-footprints
python3 tools/fetch_models.py      # 220 MB from Hugging Face, verified against the card's sha256
tools/build.sh                     # -> plugins/building-footprints-1.0.0.zip (regenerates the model enum first)
```

Upload the zip on the **Plugins** page (organization owner) or with the API:

```bash
BASE=https://your.webodm.host
curl -c cj.txt -X POST "$BASE/api/method/login" -F usr=you@example.com -F pwd=secret
CSRF=$(curl -b cj.txt -s "$BASE/api/method/webodm_core.api.csrf.get_token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')
curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -F "file=@../building-footprints-1.0.0.zip" \
     "$BASE/api/method/webodm_core.api.plugins.upload_plugin"
```

The plugin is installed as `<org-slug>.building-footprints` and enabled for
your organization. A package built without fetching the weights is still
valid: a run then fails with *model file models/dinov3s_buildings.onnx is not
in the package (run tools/fetch_models.py before tools/build.sh …)*.

The sandbox image must contain **scipy** and **scikit-image**
(`services/plugin-runner/requirements.txt`). On an image without them a run
fails with one line saying so and asking the operator to update the image.

### Licence

- `hot-dinov3s-buildings` — Humanitarian OpenStreetMap Team's building model,
  **Apache-2.0** (decoder, heads and the DINOv3 encoder weights alike). Trained
  on ~58 000 OpenAerialMap chips labelled from OpenStreetMap worldwide, so it
  generalises reasonably across roof styles; it is a ViT-**S** export (the
  1.4 GB ViT-L export exists but exceeds the 1 GB extracted-package limit).

---

## 2. Run it

Open a completed task on the map and choose *Building Footprints* in the
analysis panel. Leave everything at its default (0 / -1 / auto = *use the
model card's recommended value*) or tune, and run. Progress (windows
predicted, blobs separated) shows on the run row; the result is drawn as a
filled layer with a legend and can be downloaded as GeoJSON.

From the command line (`$TASK` is a completed task with an orthophoto):

```bash
run() {  # $1 = plugin id, $2 = params JSON
  curl -s -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
       -d "{\"plugin\":\"$1\",\"task\":\"$TASK\",\"params\":$2}" \
       "$BASE/api/method/webodm_core.api.plugins.run_plugin"
}
run myorg.building-footprints '{}'                                   # card defaults (0.25 m, overlap 64)
run myorg.building-footprints '{"resolution_m": 0.3, "overlap": 128}' # faster grid, smoothest seams
run myorg.building-footprints '{"large_blob_area_px": 0, "h_maxima_depth": 0.15}'  # prominence seeding everywhere, merge more
```

### Local, without the platform

```bash
cd services/plugin-runner
python -m app.cli ../../plugins/building-footprints \
    --input orthophoto=/data/odm_orthophoto.tif --output /tmp/footprints.geojson \
    --param resolution_m=0.25
```

This runs the real sandbox code path (RLIMIT_AS, scrubbed environment).
Outside the container `PLUGIN_MAX_PROCESSES=8192` may be needed: the default
`RLIMIT_NPROC` of 128 counts every process of *your* user, and ONNX Runtime's
thread pool fails to start when it is exceeded (the plugin then retries
single-threaded with a warning).

---

## 3. Parameters

Numeric parameters default to "use the model card": `0` where zero is not a
meaningful setting, `-1` where it is (`overlap`, `min_area_m2`, `simplify_m`,
`regularize_overlap_tol_m2`); the string `auto` works for any of them. The
HOT card's values are in the third column.

| Parameter | Default | Card | Meaning |
|---|---|---|---|
| `model` | card marked `default` | `hot-dinov3s-buildings` | Model card to run; the enum is generated from `models/*.json` by `tools/update_manifest.py` |
| `resolution_m` | 0 | 0.25 m | Ground sample distance of the processing grid. The orthophoto is warped onto it (from the matching overview). Coarser = faster and more area within the memory budget |
| `tile_size` | 0 | 256 px | Window side. Fixed by the HOT graph; a different value is ignored with a warning |
| `overlap` | -1 | 64 px | Overlap between windows (stride = 256 − overlap). 128 gives the smoothest seams at ~2.3× the windows |
| `confidence` | 0 | 0.5 | Building-probability threshold for the mask |
| `seed_min_distance` | 0 | 5 px | Small blobs: minimum spacing of distance maxima that count as separate buildings |
| `large_blob_area_px` | 0 | 2000 px | Blobs at least this large are seeded with h-maxima (prominence) instead of plain local maxima. `0`-as-value is "card"; set it to 1 to use prominence seeding everywhere |
| `h_maxima_depth` | 0 | 0.1 | Large blobs: a maximum must rise this much (on the −1…1 distance scale) above the saddle to its neighbour to seed a building. Higher merges, lower splits |
| `min_area_m2` | -1 | 4 m² | Drop footprints (and blobs, before any work) smaller than this; 0 keeps all |
| `simplify_m` | -1 | 0.3 m | Douglas-Peucker tolerance for outlines; 0 keeps the pixel staircase |
| `regularize_area_threshold` | 0 | 0.85 | Replace a footprint by its minimum rotated rectangle when it fills at least this fraction of it; 1 disables |
| `regularize_overlap_tol_m2` | -1 | 1 m² | …but only if the rectangle adds at most this much *new* overlap with neighbouring footprints |

---

## 4. Results and how to access them

Each feature is one building (exterior ring only — courtyards are not holes)
in EPSG:4326 with:

```json
{ "class": "building", "score": 0.84, "area": 319.31, "regularized": false, "instance_id": 2 }
```

Run metadata (`output_metadata` on the run, shown in the panel) records the
card summary, the graph contract actually used (tensor names, normalisation,
output channels, thread mode), the processing grid (CRS, resolution, size,
bounds), the windows (count, overlap, Gaussian sigma, empty windows skipped),
requested and effective parameters, instance statistics (blobs, tiny blobs
dropped, blobs seeded with h-maxima, seeds, blobs split, buildings), vector
statistics (polygons, regularised, total/median area, mean score), the legend
and stage timings with peak RSS. The GeoJSON is downloadable from the run
panel or with `webodm_core.api.plugins.get_run_geojson`.

---

## 5. How it works

1. **Orthophoto → processing grid.** The orthophoto is opened with rasterio
   (must be georeferenced, ≥ 3 bands) and read on a grid at `resolution_m`
   in its own CRS through a `WarpedVRT` fed from the overview level closest
   to that resolution, so a 5 cm survey processed at 25 cm reads 1/25 of its
   pixels. If the grid would exceed the pixel budget (50 megapixels — about
   10 bytes per pixel are held at once: probability, distance, mask, labels)
   the run **stops before reading anything** with *"Use a coarser resolution:
   set resolution_m to 0.37 or more"* — the suggestion is checked against the
   real grid size so re-running at it fits. It is *not* coarsened silently
   like the segmentation plugin does, because instance separation needs the
   full planes in memory and the user should know what resolution the result
   is at.
2. **Model.** The card is resolved, the weights' sha-256 is verified, ONNX
   Runtime loads the graph and the contract is checked: NCHW RGB input whose
   static size equals the card's `input_size`, NCHW output with enough
   channels for the card's `outputs`. A session whose thread pool cannot be
   created in the sandbox is retried single-threaded.
3. **Windows + Gaussian stitching.** The grid is covered by 256 px windows
   with `overlap`; windows without a valid pixel are skipped. Each window's
   RGB ([0, 1], normalised with the card's mean/std — the HOT graph does not
   normalise internally) goes through the model; the plugin applies the
   card's activations (sigmoid on the mask logit, tanh on the distance) and
   keeps two planes, probability and signed distance (the boundary channel
   is declared on the card but unused). Planes are blended with 2-D Gaussian
   weights (σ = side/8, nnU-Net style) so each window's centre dominates and
   seams vanish; accumulation is per row band, so memory does not grow with
   the raster. Probability is stored as uint8, distance as float32 (float16
   would create exact plateaus that confuse the seeding).
4. **Instances.** `probability > confidence` gives blobs (8-connected,
   `scipy.ndimage.label`). Each blob is processed on its bounding box: blobs
   below `min_area_m2` are dropped; blobs ≥ `large_blob_area_px` are seeded
   with **extended maxima** (regional maxima of the h-maxima transform
   `R_f(f − h)`, `skimage.morphology.reconstruction` + `local_maxima`) so
   every dome rising ≥ `h_maxima_depth` above its saddle gives exactly one
   connected seed; smaller blobs are seeded with local maxima at least
   `seed_min_distance` apart (pixels within 0.02 of the maximum of their
   neighbourhood, labelled connected, so a crest is one seed). One seed → one
   building; several → `skimage.segmentation.watershed` on the negative
   distance, constrained to the blob, assigns every pixel to a seed.
   `skimage.morphology.h_maxima` itself is deliberately not used for seeds:
   it marks only the exact-top pixels of each dome, which on a rippled roof
   crest are many disconnected single pixels (and `peak_local_max` thins a
   crest to several coordinates) — either splits one building into pieces.
5. **Vectorise.** `rasterio.features.shapes` outlines each instance (largest
   polygon, exterior ring), Douglas-Peucker simplification (`simplify_m`),
   footprints below `min_area_m2` are dropped, then **regularisation**: a
   footprint filling ≥ `regularize_area_threshold` of its minimum rotated
   rectangle becomes that rectangle unless this adds more than
   `regularize_overlap_tol_m2` of new overlap with neighbours (measured
   against the original outlines, so the order of processing does not
   matter). Score = mean probability over the instance's pixels
   (`scipy.ndimage.mean`).
6. **Write.** Geometries are reprojected to EPSG:4326 (7 decimals ≈ 1 cm)
   and written with class, score, area; metadata as above.

No pre-run validation exists for user plugins by design: a missing or corrupt
model, a graph that does not match its card, or a grid over budget fails the
run with one actionable line in the run panel.

### Assumptions and limits

- RGB only; alpha / nodata define the valid area. 16-bit imagery is scaled to
  [0, 1] by its dtype range.
- Trained on ~0.15–0.3 m OpenAerialMap imagery: run between 0.2 and 0.3 m. At
  0.25 m one 256 px window is 64 m on the ground.
- CPU inference: ~2.6 s per window inside the sandbox on a 4-core host (the
  UperNet decoder dominates, not the ViT-S encoder). Measured on a 14718 ×
  11640 px, 5 cm ODM orthophoto (≈ 43 ha): 6.85 Mpx grid at 0.25 m, 180
  windows at overlap 64 (51 skipped as nodata), 473 s of inference, 1.5 s for
  instances and vectors, 477 s in total; 128 blobs → 125 instances (31 blobs
  split) → 119 footprints after the 4 m² minimum. The manifest allows an
  hour; coarsen `resolution_m` or lower `overlap` for larger surveys.
- Memory: the sandbox caps address space at 2 GB. Peak RSS on that run was
  762 MB (653 MB of it the ONNX session); the 50 Mpx grid cap keeps the
  planes under ~500 MB on top of that.
- Regularisation: on European pitched-roof villages the model's outlines
  (annexes, garages, dormers included) fill 0.63–0.75 of their rotated
  rectangle (quartiles over 118 footprints), so the 0.85 default replaces
  only clearly rectangular buildings (4 of 118 there; 13 at 0.8). Lower it
  for simple-roof areas, raise it (or set 1) where L- and T-shapes dominate.
- Large flat industrial roofs with little texture get patchy probabilities
  and come out fragmented; lowering `confidence` to ~0.35 helps there.
- Package: 256 MB zipped / 1 GB extracted. The ViT-S weights are 220 MB
  (ONNX compresses little), so the zip is ~190 MB — within the limit, with no
  room for a second model of that size in the same package.
- Instance quality follows the distance head: buildings under a shared roof
  with no visible wall are not separated; very small structures (< 4 m²) are
  dropped by default.

---

## 6. Adding or replacing a model

Everything model-specific is a card, so another export — the ViT-L model on a
deployment with raised package limits, a fine-tuned decoder, your own
mask/distance network — is a card edit, not a code change.

1. Export to ONNX with a static square RGB input and one NCHW output whose
   channels include a building logit/probability and a signed distance
   (positive inside); a boundary channel is optional.
2. Put `my-footprints.onnx` in `models/` and add `models/my-footprints.json`:

   ```json
   {
     "id": "my-footprints",
     "label": "My footprints — fine-tuned for metal roofs",
     "description": "What it was trained on, expected GSD, known weaknesses.",
     "file": "my-footprints.onnx",
     "sha256": "<sha256sum my-footprints.onnx>",
     "default": false,
     "class": { "name": "building", "color": "#d7301f" },
     "onnx": {
       "input": "image", "output": "logits", "input_size": 512,
       "channels": ["red", "green", "blue"], "scale": "unit",
       "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
       "outputs": {
         "mask":     { "channel": 0, "activation": "sigmoid" },
         "distance": { "channel": 1, "activation": "tanh" }
       }
     },
     "recommended": { "resolution_m": 0.3, "overlap": 64, "confidence": 0.5, "seed_min_distance": 5,
                      "large_blob_area_px": 2000, "h_maxima_depth": 0.1, "min_area_m2": 4,
                      "simplify_m": 0.3, "regularize_area_threshold": 0.85, "regularize_overlap_tol_m2": 1 },
     "source": { "name": "in-house", "url": "https://…/my-footprints.onnx", "license": "proprietary" }
   }
   ```

   - `mean`/`std` are applied by the plugin after scaling to [0, 1] (or
     0–255 with `"scale": "255"`); set both to `null` when the graph
     normalises internally.
   - `activation` is what the plugin applies to the raw channel: `sigmoid`,
     `tanh` or `none` (already activated in the graph).
   - `recommended.resolution_m` is required; the other keys are needed only
     if runs leave that parameter at its sentinel.
   - `source.url` + `sha256` let `tools/fetch_models.py` download and verify
     the file for colleagues instead of committing it.
3. `tools/build.sh` (regenerates the `model` enum in `plugin.json`, packages
   the zip) and upload. `python -m pytest -q tests/test_registry.py
   tests/test_manifest.py` checks the card and the manifest.

---

## 7. References

- Siméoni, O. et al. (2025). *DINOv3.* arXiv:2508.10104. (Encoder.)
- Xiao, T. et al. (2018). *Unified Perceptual Parsing for Scene Understanding.*
  ECCV. (UperNet decoder.)
- Humanitarian OpenStreetMap Team, *fAIr* `dinov3_buildings` pipeline and
  `hotosm/vhr-building-segmentation` dataset; model card
  `kshitijrajsharma/dinov3-hot-buildings` (Hugging Face). (Training data,
  mask + boundary + distance heads, watershed recipe.)
- Beucher, S. & Meyer, F. (1993). *The morphological approach to segmentation:
  the watershed transformation.* In: Mathematical Morphology in Image
  Processing. (Marker-controlled watershed.)
- Soille, P. (2004). *Morphological Image Analysis*, §6.3 regional and extended
  maxima (h-maxima transform via reconstruction). (Seeding of large blobs.)
- Isensee, F. et al. (2021). *nnU-Net.* Nature Methods 18. (Gaussian-weighted
  sliding-window inference, σ = side/8.)
- Douglas, D. & Peucker, T. (1973). *Algorithms for the reduction of the
  number of points required to represent a digitized line.* (Simplification.)
