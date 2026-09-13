## 1. Geospatial analysis registry

- [x] 1.1 Define an analysis-op registry entry shape (`op_id`, `label`, `description`, `version`, `params_schema`, `output_kind`, `render_kind`, `inputs`, `handler`) and verify a unit test registers a dummy op and lists it
- [x] 1.2 Add an analysis router with `GET /analysis` returning the registered catalog, and verify the response matches registered ops exactly (contract test)
- [x] 1.3 Add `POST /analysis/{op_id}/run` that validates params against the schema, invokes the handler, and returns the output path/geojson/metadata; verify success, invalid-params (422), and unknown-op (404) tests
- [x] 1.4 Implement the contours operation (`gdal_contour` → GeoJSON/GPKG) and verify a test on a fixture DEM produces expected contour features and extent
- [x] 1.5 Implement one raster operation (NDVI from orthophoto, or hillshade from DSM) and verify the output is a COG with recorded extent/EPSG
- [x] 1.6 Register the operations with correct `output_kind`/`render_kind` and verify the catalog exposes them

## 2. Frappe data model

- [x] 2.1 Rework `WebODM Plugin` into the catalog record (`plugin_id`, `label`, `version`, `description`, `params_schema`, `output_kind`, `render_kind`, `platform_enabled`, `available`) and verify `bench migrate` applies and the DocType loads
- [x] 2.2 Add the per-organization enablement/settings DocType (`organization`, `plugin`, `enabled`, `settings`) and verify create + organization stamping
- [x] 2.3 Add the `WebODM Plugin Run` DocType (`plugin`, `task`, `organization`, `status`, `parameters`, `progress`, outputs, `error`, timestamps) and verify create + status transitions
- [x] 2.4 Add permission hooks and query conditions for the new org-scoped DocTypes and verify cross-organization access is denied in tests
- [x] 2.5 Add `before_insert` organization stamping for the run and setting DocTypes and verify each row is stamped with the caller's organization

## 3. Catalog synchronization

- [x] 3.1 Implement sync that fetches `GET /analysis` and upserts catalog rows (create new as platform-enabled, mark disappeared as unavailable, never duplicate, preserve run history) and verify new/removed/unreachable tests
- [x] 3.2 Hook sync to the scheduler (and a manual trigger) and verify it runs and leaves the previous catalog intact when the service is unreachable

## 4. Run API and worker

- [x] 4.1 Implement `api/plugins.py` listing that merges catalog, organization enablement, and the platform kill switch and verify a member sees enabled vs unavailable states correctly
- [x] 4.2 Implement enable/configure endpoints restricted to organization admins with schema validation and verify invalid settings are rejected and valid settings are stored
- [x] 4.3 Implement run eligibility validation (org-enabled, platform-enabled, task in org, task completed, required inputs present) and verify each rejection scenario produces no run record
- [x] 4.4 Implement run parameter merge (organization defaults plus validated overrides) and verify default, override, and invalid cases
- [x] 4.5 Create the run record and enqueue the RQ job on a dedicated analysis queue and verify the run moves from Queued and is picked up
- [x] 4.6 Implement the worker (permission-checked input resolution → geospatial call → output persistence → status update) and verify Completed with outputs, and Failed with an error on operation failure
- [x] 4.7 Implement cancellation and verify the run transitions to Cancelled with no output attached

## 5. Output serving

- [x] 5.1 Extend `api/tiles.py` to resolve a plugin run's raster by render kind and verify authorized tile requests succeed and cross-organization requests are rejected
- [x] 5.2 Add a vector→GeoJSON endpoint for a run output and verify GeoJSON is returned and unauthorized access is rejected
- [x] 5.3 Add a download endpoint for run outputs and verify an authorized member can download the artifact

## 6. Frontend

- [x] 6.1 Add API helpers for listing, enabling, configuring, running, and listing runs, and verify they are covered by unit tests
- [x] 6.2 Rewrite `Plugins.vue` to load the catalog from the API and support admin enable/config via a schema-driven form, verifying the hardcoded array is gone
- [x] 6.3 Add the task-view plugin panel with a schema-driven parameter form and Run action, and verify a run starts and its status updates
- [x] 6.4 Generalize MapView raster overlays to include plugin-run outputs and verify a plugin raster renders as a tile layer
- [x] 6.5 Add a GeoJSON vector overlay layer with a visibility toggle and verify vector output renders and toggles independently
- [x] 6.6 Add download links for run outputs and verify a download succeeds from the UI

## 7. End-to-end verification

- [x] 7.1 Verify the full vertical: enable a plugin for an organization, run it on a completed task, see both a raster tile overlay and a vector overlay, and download the outputs
- [x] 7.2 Verify negative/security paths: cross-organization run and output access denied, disabled plugin rejected, missing required input rejected, and clear failure when the geospatial service is down
