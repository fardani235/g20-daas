## Context

See `proposal.md` for motivation and `specs/semantic-segmentation/spec.md` for the
behavior contract. Current state that shapes this design:

- The curated plugin system (`add-analysis-plugin-system`) defines operations in
  `webodm-geospatial` (`app/analysis/registry.py`): each `AnalysisOp` declares
  `op_id`, label, description, version, `params_model` (pydantic → params JSON
  Schema), `output_kind` (`raster`/`vector`), `render_kind`, `inputs`, and a
  handler. `GET /analysis` exposes the catalog; `POST /analysis/{op_id}/run`
  validates params, resolves input/output paths, and runs the handler.
- `add-object-detection-plugin` established the ML precedent: ONNX Runtime (CPU)
  inside the geospatial service, a managed models directory with path-traversal
  rejection, curated known-models list, pre-run validation via
  `POST /analysis/{op_id}/validate`, tiled inference with overlap, vector GeoJSON
  output, class-based map rendering, and an operation-declared
  `timeout_seconds` used for both the RQ job and the geospatial HTTP call.
- The geospatial image already has rasterio, numpy, scipy, shapely, GDAL, and
  `onnxruntime`; no ML runtime beyond ONNX Runtime, and no PyTorch.
- Completed vector outputs are stored as private Files, served as GeoJSON
  overlays (canvas-rendered, capped at 5000 features) and downloadable.
- geodeep (the upstream WebODM dependency) implements the same *idea*, but is
  AGPL-3.0 and accepts arbitrary/remote model paths; it is not adopted here.

## Goals / Non-Goals

**Goals:**

- Add a `semantic-segmentation` op that runs a permissive-licensed ONNX
  per-class mask model over the orthophoto on CPU, producing vectorized regions.
- Cover the full image via tiled inference with overlap, merging masks so regions
  are contiguous across tile seams.
- Emit class polygons (class + area) as GeoJSON, with per-class area/count
  metadata, reusing the existing vector output/overlay/download path.
- Keep models constrained to the managed directory and the service stateless.
- Add a fill-based segmentation render kind without disturbing detection
  rendering.

**Non-Goals:**

- Instance segmentation (per-object masks), detection changes, model
  training/fine-tuning, GPU, video, or a separate inference microservice.
- Semantic changes to catalog/enablement/run-replace machinery; the existing
  `timeout_seconds` catalog field is reused.
- Adopting or linking geodeep; only its high-level approach informs this design.

## Decisions

### D1: ONNX Runtime in-process, native implementation

**Decision:** Run mask inference in-process in the geospatial service using the
already-present `onnxruntime` (CPU), with numpy/scipy for mask processing and
shapely/rasterio for vectorization.

**Rationale:** Same architecture as the detection op: no new service to operate,
no GPU, algorithm and parameter schema co-located. **Alternatives:** vendoring or
linking geodeep (AGPL-3.0, and it accepts raw/remote model paths — both
unacceptable here); a PyTorch segmentation model (heavy image, rejected for
detection already).

### D2: Output is vectorized class polygons, not a raster mask

**Decision:** Polygonize class masks and emit a GeoJSON `FeatureCollection` of
polygons with `class` and `area` (plus confidence/score where the model provides
one). `output_kind` is `vector`, so storage, overlay, and download reuse existing
machinery.

**Rationale:** Reuses the entire vector path with no new output kind or tile
proxy, and gives users togglable, downloadable, class-styled regions.
**Alternatives:** a raster mask served as tiles (new output/render/storage
machinery, no per-class attributes); per-pixel points (unusable at scale).

### D3: Tiled inference with overlap, then global mask stitching

**Decision:** Slice the orthophoto into overlapping tiles, run the model per
tile, map each tile's class prediction back to raster coordinates, and merge
predictions into a single class mask over the whole extent before vectorizing.
Where tiles overlap, a deterministic rule resolves disagreement (for example the
higher-probability prediction, or highest confidence among overlapping tiles).
Vectorize once from the merged mask so no region is duplicated or split.

**Rationale:** Orthophotos exceed any segmentation model's fixed input;
stitching before vectorization avoids duplicate polygons and artificial seams.
**Alternatives:** vectorize per tile then dissolve (fragments and seam gaps);
whole-image resize (loses small regions, high memory).

### D4: Models and labels constrained to a managed directory

**Decision:** Reuse the detection op's managed models directory
(`OBJECT_DETECTION_MODELS_DIR`) with the same `resolve_asset` validation
(absolute paths, `..`, and symlink escapes rejected). A platform default is
provisioned; an organization may override the model **by name within the
directory**. At load, validate a single image input and a per-class mask output,
and that the label count matches the model's class count. The optional
pre-run `POST /analysis/{op_id}/validate` hook is used so a bad model rejects the
request before a run is created.

**Rationale:** Consistent with the detection precedent and avoids the unsafe
arbitrary-path model loading geodeep permits. **Alternatives:** a separate models
directory (duplicate provisioning, more mounts); no validation (fails runs late).

### D5: Parameters and defaults

**Decision:** A pydantic params model (so the schema drives the frontend form):
`model` (name in the models dir), `labels` (name), `threshold` default `0.5`
(0–1), `tile_size` default `512` (bounded, multiple of the model's divisor where
required), `overlap` default `64` (≥0 and < `tile_size`), `min_segment_area`
default `64` (raster pixels²), `simplify_tolerance` default `0` (raster pixels,
0 disables), and `classes` (optional subset). Optional `tile_size_m`/`overlap_m`
express tile and overlap in ground metres, converted via the raster's ground
sample distance.

**Rationale:** Reuses schema-driven forms, prefill, and pre-run validation.
**Alternatives:** free-form strings (poor UX, unsafe); hard-coded post-processing
(no control over noise vs. detail).

### D6: Mask post-processing before vectorization

**Decision:** After stitching, apply a small morphological cleanup (open/close)
to remove speckle, use the model's per-class probabilities to resolve overlaps,
drop connected regions below `min_segment_area`, and optionally simplify polygon
boundaries by `simplify_tolerance` before emitting.

**Rationale:** Raw per-pixel masks produce millions of tiny polygons; cleanup and
minimum area keep output meaningful and within render budgets. **Alternatives:**
no cleanup (feature explosion); aggressive smoothing (loses real small objects).

### D7: A distinct `segmentation` render kind

**Decision:** The op declares `render_kind: segmentation`. The map renders its
vector overlay as per-class **filled** regions with a legend, and still enforces
the existing feature cap: above the cap the overlay is skipped with an
explanation and the output remains downloadable.

**Rationale:** Fills distinguish areal coverage from detection boxes and reuse the
existing vector overlay/cap machinery. **Alternatives:** reuse `detections`
rendering (visually wrong for fills); a new raster render path (unnecessary).

### D8: Input declaration

**Decision:** `inputs = [{"name": "raster", "datasets": ["orthophoto"]}]`.

**Rationale:** Segmentation needs RGB imagery; the existing eligibility and
missing-input checks then apply unchanged.

### D9: Curated segmentation model list

**Decision:** Publish a curated list of known permissive-licensed segmentation
models (model, labels, recommended parameters) in the catalog; Frappe syncs it
and the UI offers a dropdown with a "Custom" option for arbitrary models inside
the managed directory. Initial candidates are building- and road-class models.

**Rationale:** Mirrors the detection model dropdown so users need not type
filenames. **Alternatives:** no curated list (poor UX); bundling many models
(image size).

## Risks / Trade-offs

- [Memory on large orthophotos] → tile processing with bounded windows for both
  inference and mask writing; keep the merged mask and vectorization within the
  service's memory budget; document expected sizes.
- [Seam artifacts from independent tile predictions] → overlap plus a
  deterministic resolution rule, and vectorize only after stitching.
- [Feature explosion from dense masks] → minimum segment area, optional
  simplification, and the existing vector feature cap (skip-with-message above
  it, output stays downloadable).
- [Model/licensing drift] → accept only permissively licensed ONNX models from
  pinned sources with checksums, provisioned like the detection models; do not
  link geodeep (AGPL-3.0).
- [Model/output contract drift] → validate input/output shapes and label count at
  load and reject before a run is created.
- [CPU inference time] → reuse the operation-declared `timeout_seconds` for the
  RQ job and HTTP call; multiple uvicorn workers with blocking work offloaded so
  long runs cannot stall the service.
- [Class threshold sensitivity] → expose `threshold` and class filter; document
  defaults and expected tuning.

## Migration Plan

1. **Geospatial:** implement the op, model loading/validation, tiled mask
   inference and stitching, post-processing and polygonization, and GeoJSON
   output; provision a permissive default model and labels in the managed
   directory; unit tests with a tiny fixture mask model.
2. **Frappe:** no schema change required — catalog sync picks up the op, its
   params schema, `render_kind`, and `timeout_seconds`; vector output handling
   already applies.
3. **Frontend:** handle `render_kind: segmentation` (filled, class-styled overlay
   + legend) respecting the feature cap.
4. **Rollout:** platform enables the op; organizations opt in and may override
   the model. **Rollback:** the platform kill switch disables the op; existing
   runs and outputs are unaffected.

## Open Questions

- The concrete default model artifact to provision (a small permissive building-
  or road-segmentation ONNX export), including its source, size, and license —
  an ops/provisioning detail that does not change behavior.
- Whether to expose a hard cap on emitted features in parameters or rely on the
  existing render cap plus minimum area — a tuning detail.
- Whether simplification should default to non-zero for large orthophotos — a
  default value, not a behavior change.
