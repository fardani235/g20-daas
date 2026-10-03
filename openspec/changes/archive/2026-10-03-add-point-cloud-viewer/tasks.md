## 1. Frontend loader spike

- [ ] 1.1 Add a Potree 2.0-capable three.js loader (`@pnext/three-loader` v2, else the `potree-loader` fork) to `frontend/package.json`; verify it installs, passes `vite build`, and loads a small Potree 2.0 fixture under three 0.185 — record the chosen package/version, or implement the minimal loader fallback if both fail
- [ ] 1.2 Verify the loader's worker/buffer usage is allowed by the CSP in `infra/caddy/Caddyfile` and `infra/helm/webodm/files/Caddyfile` (worker-src/connect-src), updating both together if needed, and note the result
- [ ] 1.3 Add `src/lib/pointcloud.js` pure helpers (available color modes from metadata attributes, elevation/intensity ranges, classification legend, filter clamping, unit formatting, member-name→URL mapping that rejects unknown names) and verify with `npm test`

## 2. Geospatial conversion

- [ ] 2.1 Add PotreeConverter 2.0 to `services/geospatial/Dockerfile` pinned with a checksum (release binary or cmake build) and verify `PotreeConverter --help` runs in the built image
- [ ] 2.2 Add `services/geospatial/app/utils/potree.py` (run the converter, parse its output for the point count, read `metadata.json` for bounds/attributes, report member paths and sizes) with pytest unit tests over a tiny committed LAZ fixture
- [ ] 2.3 Implement `POST /pointcloud/to-potree` in `services/geospatial/app/routers/pointcloud.py` accepting an absolute local path or `s3://` input plus a scratch output dir, with the same path/object validation as the raster endpoints; verify success and 400/404/422 cases with pytest
- [ ] 2.4 Return a conversion summary (point count, native + EPSG:4326 bounds, EPSG/WKT, attribute list, per-member bytes, converter version) from the endpoint and verify the JSON contract with pytest
- [ ] 2.5 Ensure the conversion is safe for large clouds (scratch cleanup on success and failure, bounded cache, no partial output on error) and verify a failing-input test leaves no members

## 3. Frappe data model and storage

- [ ] 3.1 Add `WebODM Task` fields (`pointcloud_status` Select Not Converted/Queued/Running/Ready/Failed, `pointcloud_error`, `pointcloud_summary` JSON, `pointcloud_attempts` Int, `pointcloud_files` table) and the `WebODM Point Cloud File` child doctype (`member`, `filename`, `file_url`, `storage_key`, `file_size`, `content_type`); verify with `bench migrate` and a test that inserts a row
- [ ] 3.2 Add `potree` to `TASK_OUTPUT_SUBPREFIXES` and verify `task_output_prefixes` includes it and `delete_task_objects` removes a seeded `potree/` prefix (test)
- [ ] 3.3 Add storage helpers to write the octree members as private Files attached to the task (`plugins.files.save_private_file_from_path`), record `WebODM Point Cloud File` rows, and upload to `orgs/<slug>/tasks/<task>/potree/` when storage is configured; verify with the fake-storage tests
- [ ] 3.4 Extend `cache.storage_key_for_file_url` and `cache._candidates` for `WebODM Point Cloud File`; verify eviction selects an idle octree blob and that `materialize_private_file` refills an evicted member (test)
- [ ] 3.5 Extend `assets.reset_outputs` so re-processing clears the octree Files/rows/keys and resets `pointcloud_status`, and make task `on_trash` delete the octree File docs; verify both with tests (previous octree gone, `inputs/` untouched)
- [ ] 3.6 Verify the org boundary is enforced on octree read/write/delete (a tampered/foreign key is refused) with a test

## 4. Frappe conversion API and job

- [ ] 4.1 Add `webodm_core/api/pointcloud.py` with `start(task_name)` (permission-checked, idempotent, sets Queued and enqueues) and `get_point_cloud(task_name)` (status, summary, member URLs); verify permission denial and idempotent double-start with tests
- [ ] 4.2 Add `processing/pointcloud.py` implementing `convert_point_cloud(task_name)` on the `long` queue with `enqueue_process`-style dedup: resolve the LAZ via `storage.assets`, call the geospatial service, write members, set Ready/Failed; verify the Queued→Running→Ready and Failed paths with tests
- [ ] 4.3 Include point-cloud status/summary/member URLs in `get_task_progress` (`_task_payload`) so the viewer receives them while polling; verify the payload shape with a test
- [ ] 4.4 Record `pointcloud_error`, bound `pointcloud_attempts`, and support an explicit retry of a Failed conversion; verify with tests

## 5. Serving verification

- [ ] 5.1 Verify `/private/files/<name>` serves an octree member with `206 Partial Content` and `Content-Range` over an authenticated session and refuses a user without task read access; capture the evidence (integration test or scripted check)
- [ ] 5.2 Verify the viewer's member-name mapping returns only the three known URLs and that a task with no octree returns none (unit test)

## 6. Viewer rendering integration

- [ ] 6.1 Extend `useModelViewer` to load an octree with the `getUrl` hook, apply the same Z-up→Y-up orientation and recentring as the GLB path, frame it with `framingFor`, and drive `potree.updatePointClouds` with render-on-demand while nodes are pending; expose point-cloud state in `snapshot()` and verify with vitest
- [ ] 6.2 Add the point-cloud source to `ModelView.vue` (`?view=cloud`), including the source switcher, automatic polling from Queued/Running to Ready, and the loading/empty/error/unsupported states; verify with component tests
- [ ] 6.3 Add a browser e2e check (Playwright, env-configured) that opens a fixture task's cloud, asserts it becomes ready and renders, and switches between model and cloud sources

## 7. Color, appearance and filtering

- [ ] 7.1 Extend `ModelToolbar` with point-cloud controls (color mode, point size, point budget, background, elevation range, classification filter) and wire them to material/`potree` state; verify with vitest component tests including modes disabled when their attribute is absent
- [ ] 7.2 Add elevation and classification legends derived from `metadata.json`; verify the legend data with unit tests
- [ ] 7.3 Extend the e2e check to exercise each color mode, the elevation/classification filters, and point-size/background changes

## 8. Measurement

- [ ] 8.1 Implement point picking (raycast) and distance/area geometry in the viewer, excluding filtered points; verify the geometry helpers with unit tests
- [ ] 8.2 Reuse `webodm_core.api.tiles.volume` for volume with the picked polygon (reprojected to EPSG:4326), and explain the requirement when the task has no DSM; verify the call shape and the no-DSM path with tests
- [ ] 8.3 Add the measurement overlay with clear/cancel and extend the e2e check to measure a distance, an area and (with a DSM) a volume

## 9. Infrastructure, CI and docs

- [ ] 9.1 Wire the conversion scratch path in compose so `frappe-worker` and `geospatial` share it (existing scratch volume or a dedicated one) and pass the env via `configure_site.py`/entrypoint as needed; verify a compose run converts and serves a real task's cloud
- [ ] 9.2 Update the Helm chart so the geospatial image carries PotreeConverter and the shared scratch/affinity is configured; verify with `scripts/check-helm.sh`
- [ ] 9.3 Add the new geospatial and frontend tests to CI (`build-and-push.yml`) and verify the workflow jobs run them green
- [ ] 9.4 Write `docs/pointcloud/README.md` (usage + operations) and update `README.md`, the `SPEC.md` API table and the `TODO.md` checkbox; verify the links resolve

## 10. End-to-end verification

- [ ] 10.1 On the local stack, convert a real task's LAZ, open the viewer, exercise color/filter/measure, re-process the task and delete it, confirming the octree is replaced then fully removed (objects, Files and rows)
- [ ] 10.2 Record conversion time and peak memory for a ~10M-point LAZ and confirm interactive orbiting with LOD on the viewer
