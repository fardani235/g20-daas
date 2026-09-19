## Context

See `proposal.md` for motivation. Current state that shapes the approach:

- The geospatial service (`webodm-geospatial`) is a stateless FastAPI service with
  `rasterio`, `rio-tiler`, `numpy`, `scipy`, `shapely`, `rio-cogeo`, and
  `gdal-bin`. It already exposes `/tiles/*` and `/export/cogify`, and has stubbed
  `/export/hillshade`, `/export/colormap`, and `/export/formula` endpoints. It
  never touches Frappe storage or auth.
- The Frappe image (`webodm-frappe`, `python:3.14-slim`) has **no** GDAL,
  rasterio, or numpy. It runs the web app, RQ workers, and the scheduler.
- `webodm_core` already runs async work with RQ (`processing/task_runner.py`) and
  polls status via a cron sweep. `api/tiles.py` resolves a task's raster to an
  absolute path with an explicit `check_permission("read")`.
- `WebODM Preset` establishes the multi-tenant scope precedent: `system=1` +
  `organization=NULL` rows are shared and platform-managed; otherwise rows are
  org-scoped via permission hooks.
- `MapView.vue` already renders raster tile overlays driven by task extent
  fields through `/api/method/webodm_core.api.tiles.serve`.
- `WebODM Plugin` is a `pass` DocType and `Plugins.vue` renders a hardcoded array.

## Goals / Non-Goals

**Goals:**

- A single place to define a first-party analysis operation: its algorithm, its
  parameter schema, and its output kind.
- Reuse the existing compute (geospatial), async (RQ), permission (org scope),
  and rendering (tiles proxy + MapView overlays) machinery rather than add a new
  runtime.
- Keep the geospatial service stateless and Frappe free of geospatial libraries.

**Non-Goals:**

- A plugin sandbox, interpreter, or third-party code loading.
- An ML runtime or model-serving pipeline.
- Streaming, fine-grained progress.
- Processing-pipeline/ODM-option plugins.

## Decisions

### D1: Analysis executes in the geospatial service

**Decision:** Each operation runs inside `webodm-geospatial`; Frappe orchestrates
and stores results.

**Rationale:** GDAL/rasterio/numpy/scipy already exist only there, and the
`/export/*` stubs show this is the intended home. Adding geospatial or ML deps to
the slim Frappe image would bloat every web/worker container and duplicate
libraries.

**Alternatives considered:** (a) run algorithms in the Frappe worker by adding
deps — rejected for image bloat and duplication; (b) a new dedicated analysis
service — deferred until ML is in scope, since no operation in v1 needs it.

### D2: The geospatial service owns the catalog; Frappe syncs it

**Decision:** Operations are registered in `webodm-geospatial` as a registry of
`{op_id, label, description, version, params_schema, output_kind, render_kind,
inputs, handler}`, exposed via `GET /analysis`. Each `inputs` entry names a task
dataset that can satisfy it (e.g. `{"name": "raster", "datasets": ["dsm", "dtm"]}`),
so Frappe can resolve the first available dataset and reject a run before it is
created when none is present. Frappe syncs this into persisted catalog rows.

**Rationale:** The algorithm and the schema that describes its parameters live
together, so adding a module is a single-repo change; Frappe and the frontend stay
generic. The schema drives both backend validation and the frontend form.

**Alternatives considered:** (a) manifest in Frappe referencing a geospatial op —
splits a module across two repos; (b) Python hook module in Frappe — splits
geospatial work across the stack.

### D3: Two-layer registry with a platform kill switch

**Decision:** `WebODM Plugin` is the platform-managed catalog (synced from the
geospatial registry, carrying `platform_enabled`). Per-organization enablement and
settings live in a separate org-scoped DocType. Effective availability =
`catalog.platform_enabled AND org_setting.enabled`.

**Rationale:** The catalog is code-derived and global; enablement is per-org. The
current `autoname=field:plugin_name` makes a single DocType unable to hold one row
per org. The Preset system/org split is the established pattern, and org scope is
enforced by the existing permission hooks + `tenancy.require_org()`.

**Alternatives considered:** one DocType with `system`/`organization` fields like
Preset — rejected because catalog entries are code-owned (not user-created) and
would coexist awkwardly with per-org config rows.

### D4: Runs are first-class records executed by an RQ job

**Decision:** A `WebODM Plugin Run` (org-scoped, linked to task + plugin) records
status, parameters, progress, outputs, and errors. A Frappe API validates
eligibility and creates the run, then enqueues an RQ job that calls the
geospatial operation synchronously and persists outputs.

**Rationale:** Mirrors the existing `WebODM Task` polling pattern and reuses RQ.
Keeping the HTTP call inside the worker preserves the service's statelessness.
A first-class run gives history, re-runs, audit, and org-scoped visibility.
Progress is coarse in v1: status moves Queued → Running → terminal, with no
percentage channel to build.

**Alternatives considered:** (a) geospatial-owned async jobs with Frappe polling —
would make a deliberately stateless service stateful; (b) attach outputs directly
to the Task with no run record — loses history and error/async tracking.

### D5: Outputs reuse the tiles proxy and generalize MapView overlays

**Decision:** Raster outputs are stored as Files on the run with extent/EPSG/render
kind recorded and served through the existing permission-checked tiles proxy,
extended to resolve a run's raster. Vector outputs are converted to GeoJSON and
rendered as new Leaflet overlays. MapView's raster overlay source becomes
data-driven (task fields or run outputs).

**Rationale:** Reuses permission-checked path resolution and the existing overlay
engine, so access control and map behavior stay consistent with task rasters.

### D6: Parameter forms are schema-driven

**Decision:** The frontend generates the task-view plugin form and the settings
form from the operation's `params_schema`; backend validation uses the same
schema.

**Rationale:** New operations require no bespoke frontend code, which is the point
of a curated catalog.

## Risks / Trade-offs

- [Geospatial service down] → catalog sync fails (keep last catalog intact, report
  failure) and runs fail visibly with an error; health checks and a retry on the
  next sync. Availability of the list never breaks because of a transient outage.
- [Long operations tie up an RQ worker] → run analysis on a dedicated queue with
  explicit timeouts; cancellation is best-effort and marked Cancelled.
- [Arbitrary raster outputs are hard to render] → operations declare an
  `output_kind` and render kind up front; unsupported kinds are still
  downloadable but not overlaid.
- [Cross-repo coordination] → the geospatial registry and Frappe sync contract
  must stay in lockstep; version the catalog response and fail sync on schema
  mismatch rather than silently dropping operations.
- [Permission leakage via output paths] → all output access goes through the
  permission-checked resolver (same discipline as `api/tiles.py`), never a raw
  path from the client.
- [Parameter schema drift] → a run stores the schema/version it executed under, so
  old runs remain interpretable after an operation changes.

## Migration Plan

1. Extend `webodm-geospatial` with the registry, `GET /analysis`, and
   `POST /analysis/{op_id}/run`, implementing the first operations.
2. Add Frappe DocTypes: rework `WebODM Plugin` into the catalog record (including
   `platform_enabled`), add the per-org enablement/settings DocType and
   `WebODM Plugin Run`; add permission hooks and organization stamping.
3. Add catalog sync (scheduled + on demand) and `api/plugins.py`
   (list/enable/configure/run/list-runs/status); add the RQ runner.
4. Extend `api/tiles.py` to resolve run rasters; add a vector→GeoJSON path.
5. Wire `Plugins.vue` to the API, add the task-view plugin panel, and generalize
   MapView overlays (raster + new vector layer).
6. **Rollback:** disable execution via the platform kill switch first; the new
   DocTypes and APIs are additive, and the reworked `WebODM Plugin` currently
   holds no meaningful data, so reverting the app is safe.

## Open Questions

- Exact RQ queue name, timeout, and worker concurrency for analysis.
- Transport detail for the run call (JSON vs multipart) as operations start
  producing non-trivial binary outputs.
- Final naming of the per-organization enablement/settings DocType.
