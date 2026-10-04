## 1. Model and card

- [x] 1.1 Inspect the HOT ONNX exports (tensor names/shapes, whether normalisation and activations are in the graph, size, sha-256) and record the findings in the card and design
- [x] 1.2 Write `models/hot-dinov3s-buildings.json` (file, sha256, default, class, onnx contract with per-channel mean/std and output channel roles + activations, recommended parameters, source/licence)
- [x] 1.3 `tools/fetch_models.py` downloads to `.part`, verifies sha-256, renames; `--list` shows licence and status

## 2. Plugin code

- [x] 2.1 `bfplugin/registry.py`: card validation with actionable messages, catalog with unavailable cards, default/auto selection
- [x] 2.2 `bfplugin/params.py`: typed parameters, 0 / -1 / "auto" sentinels resolved against the card, range checks
- [x] 2.3 `bfplugin/grid.py`: orthophoto checks, processing grid at `resolution_m`, overview-level `WarpedVRT` reads with validity mask, pixel budget failing early with a sufficient suggested resolution
- [x] 2.4 `bfplugin/tiling.py`: window layout, Gaussian weights, row-band `PlaneBlender`
- [x] 2.5 `bfplugin/inference.py`: file presence + sha-256, ORT session (single-thread fallback), graph-vs-card validation, per-window prediction with activations and output-shape check
- [x] 2.6 `bfplugin/instances.py`: per-component seeding (local maxima / extended h-maxima), watershed, in-place ids, stats
- [x] 2.7 `bfplugin/vectorize.py`: exterior outlines, simplify, min area, guarded MRR regularisation, scores, GeoJSON 4326
- [x] 2.8 `bfplugin/pipeline.py` + `main.py`: stages with progress file, metadata, one-line failures
- [x] 2.9 `plugin.json` (vector / segmentation, 12 parameters) and `tools/update_manifest.py`, `tools/build.sh`

## 3. Tests

- [x] 3.1 Synthetic fixtures: 1×1-conv ONNX graphs with the real contract (plus bad-shape and 2-channel variants), a scene whose pixels encode mask and per-building distance (touching pair, L-shape, speck, rotated rectangle)
- [x] 3.2 Unit tests: params, registry, tiling/blender, instances (touching split both seeding paths, rippled crest stays one, drop tiny, h controls merging), vectorize (exterior ring, regularise + overlap tolerance, 4326 properties)
- [x] 3.3 End-to-end through `main.py`: separation, areas, regularised flags, parameters, budget failure + sufficient suggestion, one-line failures for missing/corrupt/mismatched models and bad inputs, manifest ⇄ cards, no weights tracked
- [x] 3.4 Real data through the sandbox CLI (RLIMIT_AS 2 GB): two ODM orthophotos (EPSG:32632, 5 cm), preview rendered and inspected

## 4. Platform and docs

- [x] 4.1 `services/plugin-runner/requirements.txt` += scipy, scikit-image; runner README and user-plugin-guide library lists
- [x] 4.2 CI `test-plugin-runner` runs the plugin's tests; `.gitignore` for fetched weights
- [x] 4.3 `docs/plugins/building-footprints.md`, plugin README, repository README tables, AGENTS.md phase entry, this change + `openspec/specs/building-footprints/spec.md`
