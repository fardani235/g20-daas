## Context

See `proposal.md` for motivation. Current state that shapes this design:

- The curated plugin system (change `add-analysis-plugin-system`) defines
  operations in `webodm-geospatial` (`app/analysis/registry.py`): each
  `AnalysisOp` declares `op_id`, `label`, `description`, `version`,
  `params_model` (pydantic, yielding the params JSON Schema), `output_kind`
  (`raster`/`vector`), `render_kind`, `inputs` (name → candidate task datasets),
  and a `handler`. `GET /analysis` exposes the catalog; `POST /analysis/{op_id}/run`
  validates params, resolves absolute input/output paths, and runs the handler.
- Frappe syncs the catalog into `WebODM Plugin` rows, enforces per-org
  enablement plus a platform kill switch, resolves inputs against the task,
  creates a `WebODM Plugin Run`, and an RQ worker calls the operation, storing
  the artifact as a private File. Vector outputs are served as GeoJSON overlays
  (canvas-rendered, capped at 5000 features) and are downloadable; raster
  outputs are served as tiles.
- Existing ops are classic (contours via `gdal_contour`, hillshade via numpy).
  The geospatial image has rasterio/numpy/scipy/shapely/gdal but **no** ML
  runtime. The prior change explicitly deferred ML.
- Timeouts today: the geospatial HTTP call defaults to 600 s, but the RQ job is
  enqueued with Frappe's default (~300 s) — too short for ML inference.
- A task's `orthophoto` is a georeferenced COG (EPSG recorded on the task).

## Goals / Non-Goals

**Goals:**

- Add an `object-detection` op that runs a configurable ONNX model (COCO
  default) over the orthophoto on CPU via `onnxruntime`.
- Detect across the full image via tiled inference with overlap and global NMS.
- Emit detections as GeoJSON bounding boxes with class/confidence, plus
  per-class counts, reusing the existing vector output/overlay/download path.
- Keep model files constrained to a platform-managed directory and keep the
  service stateless.

**Non-Goals:**

- Model training/fine-tuning, custom class datasets, GPU, video, instance
  segmentation, object tracking, or a separate inference microservice.
- Changing the plugin catalog/enablement/run-replace machinery beyond an added
  timeout field.

## Decisions

### D1: ONNX Runtime inside the geospatial service

**Decision:** Run inference in-process in the geospatial service using
`onnxruntime` (CPU) plus numpy/Pillow for preprocessing.

**Rationale:** A modest dependency that fits the existing stateless op pattern
(algorithm and parameter schema co-located), with no extra service to operate or
authenticate. **Alternatives:** a torch/Ultralytics service (much heavier image);
an external detection API (adds a network dependency and credentials, and was
rejected by the user's choice of local ONNX).

### D2: Tiled inference with overlap + global NMS

**Decision:** Slice the orthophoto into overlapping tiles of `tile_size` with
`overlap` pixels, run the model per tile, map each detection back to raster
coordinates via the tile origin, reproject boxes to EPSG:4326, then apply a
single non-maximum suppression pass over all boxes.

**Rationale:** Orthophotos are far larger than a detector's fixed input and can
exceed memory if resized whole; tiling covers the full extent and preserves small
objects, while overlap prevents clipping at tile edges and global NMS removes
cross-tile duplicates. **Alternatives:** whole-image resize (loses small
objects, high memory); tiling without overlap (edge losses). Edge tiles are not
square, so they are letterboxed with padding; detections centred in that padding,
or clipped at an interior tile edge (a neighbour tile sees them whole), are
discarded so edges do not emit fake or duplicate boxes.

### D3: Output is GeoJSON bounding boxes

**Decision:** Produce a GeoJSON `FeatureCollection` of rectangular polygons, each
with `class` (label), `class_id`, and `confidence` properties; metadata carries
per-class counts and a total. Coordinates are EPSG:4326. Empty result yields an
empty collection with zero counts.

**Rationale:** Reuses the entire vector path (File storage, `get_run_geojson`,
overlay, download) with no new output machinery. **Alternatives:** a bespoke
detection format (new endpoints/consumers); points instead of boxes (loses
extent).

### D4: Model and labels are configuration, constrained to a managed directory

**Decision:** The op reads an ONNX model and a one-class-per-line labels file.
A platform default is provisioned into a managed models directory (mounted into
the geospatial service, e.g. `/data/models`); the platform sets the default and
an organization may override the model **by name within that directory** only.
Model and label paths MUST resolve inside the directory; absolute paths, `..`,
and symlink escapes are rejected. At load, validate that the model exposes a
single image input and a compatible detection output, and that the label count
matches the model's class count.

Pre-run validation: an op MAY declare a validator, and the catalog advertises
`needs_validation`. The service exposes `POST /analysis/{op_id}/validate`, which
builds the params model and calls the validator (the detection validator loads
the model and labels and checks them). Frappe calls it before creating a run, so
a missing, unreadable, or label-mismatched model rejects the request up front
instead of failing a run after the fact.

Model choice is domain-driven: COCO (yolov8n) is trained on ground-level photos
and is weak on top-down orthophotos, so the image also provisions an
aerial-trained model (VisDrone). The platform default is configurable
(`OBJECT_DETECTION_DEFAULT_MODEL`/`_LABELS`) so a deployment can default to the
aerial model; the code fallback stays COCO.

**Rationale:** Keeps the model supply trusted and prevents an organization admin
from pointing the op at arbitrary files (information disclosure / DoS), while
still allowing per-org model choice, and satisfies the "rejected before a run is
created" requirement. **Alternatives:** arbitrary absolute paths (unsafe);
platform-only configuration (no per-org flexibility); validating only at run
time (creates then fails a run).

### D5: Parameters and defaults

**Decision:** Params are a pydantic model (so the schema drives the frontend form
and defaults): `model` (name in the models dir), `labels` (name), `confidence`
default `0.25` (0–1), `iou` default `0.45` (0–1), `tile_size` default `640`
(multiple of 32, bounded), `overlap` default `64` (≥0 and < `tile_size`),
`max_detections` default `5000` (>0), and `classes` (optional subset of labels).
`tile_size`/`overlap` are in raster pixels; optional `tile_size_m`/`overlap_m`
give the tile and overlap in ground metres, which the op converts using the
raster's ground sample distance so object scale is consistent across
resolutions.

**Rationale:** Reuses the existing schema-driven form and prefill; bounds are
validated before a run is created. **Alternatives:** free-form strings (poor UX,
unsafe).

String-list parameters (the class filter) are edited as a plain
comma/newline-separated textarea rather than JSON, so users do not need to write
`["car", "person"]`.

### D6: Operation-declared timeout

**Decision:** Add an optional `timeout_seconds` to the catalog entry (detection
declares e.g. 1800; others keep 600). Frappe persists it on `WebODM Plugin` and
uses it for both the RQ job timeout and the geospatial HTTP call. Catalog
`schema_version` is bumped (additive).

**Rationale:** ML inference exceeds the current ~300 s RQ default, which would
otherwise kill runs; scoping the longer timeout to the op avoids tying up workers
or raising limits for every plugin. **Alternatives:** one global larger timeout
(wastes worker capacity); no explicit timeout (jobs killed mid-run).

### D7: Class-aware rendering

**Decision:** The op's `render_kind` is `detections`. The map renders its vector
overlay on canvas with a per-class colour and a legend of the classes present,
and still enforces the existing feature cap: above the cap the overlay is skipped
with an explanation and the output remains downloadable.

**Rationale:** Bounding boxes need class distinction to be useful, and reusing
the cap prevents the freeze the vector path already guards against.

### D8: Input declaration

**Decision:** `inputs = [{"name": "raster", "datasets": ["orthophoto"]}]`.

**Rationale:** Detection needs RGB imagery, not a DEM; the existing eligibility
and missing-input checks then apply unchanged.

## Risks / Trade-offs

- [CPU inference is slow on large orthophotos] → configurable tile size/overlap,
  op-declared longer timeout, coarse progress preserved; document expected
  durations.
- [Model/output contract drift breaks parsing] → validate input/output tensor
  shapes and label count at load; fail with a clear message.
- [Untrusted model files or path traversal] → managed models directory +
  read-only mount + path validation; per-org override limited to names inside it.
- [Dense detections freeze the map] → reuse the vector feature cap; canvas
  rendering; skip-and-warn above the cap, output stays downloadable.
- [Cross-tile duplicate detections] → a single global NMS pass with configurable
  IoU.
- [Georeferencing errors at tile edges] → derive boxes from each tile's raster
  transform and reproject; verify against a georeferenced fixture.
- [Image growth from ONNX Runtime] → CPU wheel is moderate; keep large model
  files out of the image (mounted) or provision only a small default.

## Migration Plan

1. **Geospatial:** add `onnxruntime` (+ preprocessing) dependency; implement the
   op, model loader/validators, tiling/NMS, and GeoJSON output; provision the
   models directory and a default model; unit tests with a tiny fixture model.
2. **Frappe:** add the `timeout_seconds` catalog field, a `WebODM Plugin` field,
   and sync it; use it for the RQ job and HTTP timeouts. No change to run-replace
   semantics.
3. **Frontend:** handle `render_kind: detections` (class styling + legend),
   respecting the feature cap.
4. **Rollout:** platform enables the op; organizations opt in and optionally
   override the model. **Rollback:** the platform kill switch disables the op;
   existing runs and outputs are unaffected.

## Open Questions

- Which concrete default model artifact to provision (e.g. a small YOLOv8n ONNX
  export with COCO labels) and its size/licensing — an ops/provisioning detail,
  not a behavior change.
- Where operators mount additional models (path and mount) and how they are
  documented.
- Whether the legend is class-only or also shows confidence ranges (UI polish).
