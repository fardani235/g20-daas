## Why

Semantic segmentation gives a *building* class as one merged mask: a terrace
of five houses, or a dense settlement, becomes one blob and one polygon.
Cadastral-style uses (counting buildings, per-building area, change
detection, OSM-style mapping) need **one footprint per building**, including
where buildings touch. Object detection gives one box per building but boxes
are not footprints. The analysis plugin system has no instance-level
footprint extraction, so this change adds one as a user plugin.

## What Changes

- New user plugin `plugins/building-footprints/` (`output_kind: vector`,
  `render_kind: segmentation`, so the existing per-class polygon rendering
  draws it — no frontend work).
- Model-card driven like the other plugins: `models/hot-dinov3s-buildings.json`
  describes the HOT DINOv3-S building export (file, sha-256, tensor names,
  per-channel normalisation, what each output channel means and which
  activation to apply, recommended parameters, licence). Weights (220 MB,
  Apache-2.0) are fetched and verified at build time, never committed.
- Pipeline: processing grid at a configurable resolution (default 0.25 m)
  with a hard pixel budget that fails early with the resolution to use;
  overlapping 256 px windows with Gaussian-weighted stitching; mask
  threshold; marker-controlled watershed on the negative signed-distance
  plane (local-maxima seeds for small blobs, extended h-maxima seeds for
  large blobs) to separate touching buildings; per-instance exterior
  polygon, simplification, minimum-rotated-rectangle regularisation guarded
  by a neighbour-overlap tolerance, minimum area; GeoJSON 4326 with class,
  score (mean mask probability) and area, plus run metadata.
- Parameters: model, resolution_m, tile_size, overlap, confidence,
  seed_min_distance, large_blob_area_px, h_maxima_depth, min_area_m2,
  simplify_m, regularize_area_threshold, regularize_overlap_tol_m2; 0 / -1 /
  "auto" fall back to the card.
- Plugin-runner image gains **scipy** and **scikit-image** (connected
  components, distance transforms, watershed, morphological reconstruction).
- Failure style as the other plugins: no pre-run validation; a missing file,
  checksum mismatch, graph/card mismatch or oversize grid fails the run with
  one actionable line.
- **Non-goals:** height/DSM fusion, roof-type classification, building
  heights, training or fine-tuning, GPU, frontend changes, the 1.4 GB ViT-L
  export (exceeds the package limit; a card edit on a deployment with raised
  limits).

## Capabilities

### New Capabilities

- `building-footprints`: instance-level building footprint extraction from an
  orthophoto as a user plugin — model cards, grid/tiling/stitching, watershed
  instance separation, vectorisation and the GeoJSON output users see.

### Modified Capabilities

- `user-plugins`: the sandbox library set grows by scipy and scikit-image
  (documented in the guide and the runner README).

## Impact

- **`plugins/building-footprints/`**: new package (manifest, entrypoint,
  `bfplugin/`, model card, tools, 75 pytest tests on synthetic fixtures).
- **`services/plugin-runner/`**: `requirements.txt` += scipy, scikit-image;
  README library list. The image must be rebuilt/pulled before the plugin can
  run.
- **CI**: `test-plugin-runner` runs the plugin's tests.
- **Docs**: `docs/plugins/building-footprints.md`, user-plugin-guide library
  list, README tables, AGENTS.md phase entry.
- **`webodm_core` / `webodm_frontend`**: no changes.
