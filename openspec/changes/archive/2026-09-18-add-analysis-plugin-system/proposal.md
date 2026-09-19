## Why

WebODM presents a plugin system (`Plugins.vue`, `WebODM Plugin` DocType, `/plugins`
route) but it is a facade: the page renders a hardcoded array, the DocType has a
`pass` controller, and nothing loads or runs. At the same time, a completed task
already holds an orthophoto, DSM, DTM and point cloud, yet users have no way to
derive anything from them (contours, NDVI, hillshade, change detection). This
change turns the facade into a working, curated analysis plugin system.

## What Changes

- **Geospatial analysis catalog.** The geospatial service gains a registry of
  first-party analysis operations (id, label, params schema, output kind,
  handler), a `GET /analysis` catalog endpoint, and `POST /analysis/{op_id}/run`.
- **Catalog sync + enablement.** Frappe syncs the geospatial catalog into
  `WebODM Plugin` rows, and stores per-organization enablement + settings with a
  platform-admin kill switch (mirrors the Preset system/org scope split).
- **Plugin runs.** A new `WebODM Plugin Run` record (org-scoped like Task) tracks
  each execution asynchronously via an RQ job: validate → resolve input path →
  call geospatial → save output File → update status. New `webodm_core.api.plugins`
  endpoints list the catalog, run, and report status.
- **Outputs.** Extend the tiles proxy to resolve raster outputs attached to a
  Plugin Run, and render plugin outputs in the MapView: raster outputs as tile
  layers, vector outputs as GeoJSON overlays, plus downloads.
- **Frontend.** Replace the mock `Plugins.vue` with live catalog + enable/config
  UI, add a task-view plugin panel whose parameter form is generated from the
  op's params schema, and make MapView overlays data-driven.
- **BREAKING:** the `WebODM Plugin` DocType is reworked (catalog vs per-org
  enablement split, naming change). It currently holds no meaningful data, so the
  migration impact is negligible.

**Non-goals (explicit):** third-party or uploaded plugin code, sandboxing and a
marketplace; an ML runtime (Object Detection, Tree Counting, Plantation Health
are deferred); per-user plugin configuration; processing-pipeline/ODM-option
plugins.

## Capabilities

### New Capabilities
- `plugin-catalog`: how analysis plugins are defined in the geospatial service,
  synced into a Frappe system catalog, and enabled/configured per organization
  with a platform kill switch.
- `plugin-execution`: how a user runs an enabled analysis plugin against a
  completed task, including validation, async job lifecycle, run records, and
  status reporting.
- `plugin-outputs`: how plugin results are stored, served (raster tiles / vector
  overlays), and downloaded.

### Modified Capabilities
- (none — no existing specs)

## Impact

- **`webodm-geospatial`**: new analysis registry/router; new operations
  (contours, NDVI, hillshade, colormap, change detection) replacing
  `export.py` stubs; catalog endpoint.
- **`webodm_core`**: reworked `WebODM Plugin` DocType plus new per-org setting
  and `WebODM Plugin Run` DocTypes; new `api/plugins.py`; new RQ job runner and
  scheduler hook; extended `api/tiles.py` resolver; hooks for catalog sync.
- **`webodm_frontend`**: `Plugins.vue` rewritten to use the API; task-view plugin
  panel; MapView overlay generalization + new GeoJSON vector layer; API helpers.
- **Database**: new DocTypes and an in-place change to `WebODM Plugin`.
- **Operations**: catalog sync and plugin execution depend on the geospatial
  service being reachable; failure must degrade gracefully (no plugin list, runs
  fail visibly).
