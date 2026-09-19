## 1. Geospatial — model management and validation

- [x] 1.1 Add a segmentation model loader that resolves a model and labels name inside the managed models directory, rejecting absolute paths, `..`, and symlink escapes; verify with unit tests for each rejected form and a valid name.
- [x] 1.2 Inspect a loaded segmentation session and reject models without a single image input and a supported per-class mask output; verify with a tiny fixture model and an incompatible-model test.
- [x] 1.3 Validate that the label count matches the model's class count and return a clear error; verify with a mismatched-labels unit test.
- [x] 1.4 Define the curated known-models list (model, labels, recommended parameters) for the catalog; verify the list is returned by the op's catalog entry.

## 2. Geospatial — tiled inference and mask stitching

- [x] 2.1 Implement tile-window generation with overlap (pixels and ground-metre inputs via GSD), matching the detection op's tiling semantics; verify unit tests for coverage, overlap bounds, and metre conversion.
- [x] 2.2 Run per-tile mask inference and map tile-local class predictions back to raster coordinates; verify with a fixture raster producing a known class region.
- [x] 2.3 Stitch tile predictions into a single whole-extent class mask using a deterministic overlap-resolution rule; verify a region spanning two tiles appears once and contiguously.
- [x] 2.4 Confirm full-extent coverage on a raster larger than one tile; verify regions are produced outside the first tile.

## 3. Geospatial — post-processing and vectorization

- [x] 3.1 Apply morphological cleanup and resolve per-class overlaps using model probabilities; verify isolated speckle is removed while a larger region survives.
- [x] 3.2 Drop connected regions below `min_segment_area`; verify a sub-threshold region is absent from output.
- [x] 3.3 Polygonize the merged mask and reproject to EPSG:4326, optionally simplifying by `simplify_tolerance`; verify output geometry is valid and georeferenced.
- [x] 3.4 Emit a GeoJSON `FeatureCollection` whose features carry `class` and `area` (plus confidence/score when available) and whose metadata reports per-class area and counts; verify an empty mask yields an empty collection with zero counts.
- [x] 3.5 Apply the class filter so only requested classes appear; verify a filtered run excludes other classes.

## 4. Geospatial — operation registration and parameters

- [x] 4.1 Define the pydantic params model (`model`, `labels`, `threshold`, `tile_size`, `overlap`, `min_segment_area`, `simplify_tolerance`, `classes`, optional metre-based tiling) with documented defaults and bounds; verify the generated JSON Schema exposes the defaults.
- [x] 4.2 Reject invalid parameters (threshold outside 0–1, overlap not smaller than tile size) before a run is created; verify with invalid-input tests.
- [x] 4.3 Register the `semantic-segmentation` op with `output_kind: vector`, `render_kind: segmentation`, `inputs: orthophoto`, and an operation-declared timeout; verify it appears in `GET /analysis` with the correct fields.
- [x] 4.4 Wire the op's pre-run validator into `POST /analysis/{op_id}/validate`; verify a missing/incompatible model rejects before a run is created.

## 5. Provisioning and packaging

- [x] 5.1 Provision a permissive-licensed default segmentation model and labels into the managed models directory from a pinned source with a checksum; verify the checksum check passes and the model loads.
- [x] 5.2 Confirm no new runtime dependency class is introduced (onnxruntime/rasterio/shapely/scipy already present) and the image builds; verify the image builds and the op's catalog entry is reachable after start.
- [x] 5.3 Document the default model, its license, and how operators add models to the directory.

## 6. Frappe integration

- [x] 6.1 Verify catalog sync persists the new op (identifier, params schema, `render_kind`, `timeout_seconds`) and platform-enables it; verify by syncing and reading the `WebODM Plugin` row.
- [x] 6.2 Verify run eligibility resolves the `orthophoto` input and rejects a run when the organization has not enabled the op or the platform disabled it.
- [x] 6.3 Verify a completed run stores the GeoJSON as a private vector output, is downloadable, and is permission-checked per organization.

## 7. Frontend

- [x] 7.1 Render `render_kind: segmentation` as per-class filled overlays with a legend; verify on the map with a completed run.
- [x] 7.2 Enforce the vector feature cap: above the limit, skip the overlay with an explanatory message while keeping the output downloadable; verify with a synthetic dense output.
- [x] 7.3 Offer the curated model list in the params form with a "Custom" option and prefill recommended parameters; verify selecting a model fills the fields.

## 8. End-to-end verification

- [x] 8.1 Run a segmentation run end to end against a real orthophoto fixture and confirm regions, metadata counts, overlay, and download; verify against the spec's scenarios.
- [x] 8.2 Run `openspec validate add-semantic-segmentation-plugin --strict` and confirm the change validates with no errors.
