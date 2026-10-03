## Context

See `proposal.md` for motivation and the two spec deltas for behavior. Current
state that shapes the approach:

- A task's point cloud is one Attach (`WebODM Task.point_cloud`, ODM's
  `georeferenced_model.laz`), resolved like other outputs through
  `storage.assets` (cache-first, `s3://` second). The `WebODM Task Asset` child
  tracks ODM outputs by `kind` and is the eviction/deletion unit.
- The geospatial FastAPI service is stateless, multi-worker (`WEB_CONCURRENCY=2`),
  reads rasters by absolute path or `s3://`, and is intentionally not exposed to
  the browser: Frappe proxies tile/volume requests (`api/tiles.py`) so they are
  same-origin and session-authed.
- Object storage is optional. When configured it is the system of record: task
  outputs live under `orgs/<slug>/tasks/<task>/{raw,assets}/`, the host keeps a
  disposable cache (`storage/cache.py`), and a `before_request` hook refills an
  evicted `/private/files/<name>` from S3 (`storage/serving.py`).
- Frappe serves `/private/files/<name>` with `werkzeug.utils.send_file(...,
  conditional=True)` (`frappe/utils/response.py`), which supports HTTP range
  requests — the mechanism a Potree loader needs for progressive octree reads.
- The 3D viewer (`ModelView.vue` + `useModelViewer.js`) owns one three.js
  renderer/camera/OrbitControls, loads a GLB (task model or a reconstruction
  run's output) and already re-orients ODM's Z-up projected coordinates.
- The geospatial `pointcloud` router already reserves `/pointcloud/to-potree`
  and `/pointcloud/export` (both 501 stubs); the geospatial image has GDAL but
  no PDAL/Potree tooling.

## Goals / Non-Goals

**Goals:**

- Convert a task's LAZ to a Potree 2.0 octree on demand and view it in the
  existing 3D viewer with LOD, color, filtering and measurement.
- Keep large binaries off the browser's direct path: same-origin, session-authed,
  range-capable serving, reusing the existing private-file + cache/S3 machinery.
- Add no cost for tasks whose point cloud is never opened, and no ODM-processing
  change.

**Non-Goals:**

- Point clouds from plugin-run outputs, arbitrary uploads, or non-LAS formats.
- Fine-grained conversion progress (coarse Queued/Running/Ready/Failed is enough
  for v1); this is a deliberate UX trade-off, see Risks.
- Elevation profiles, volume by delaunay on the cloud itself (volume reuses the
  DSM path), point editing/classification, or export of the cloud.
- COPC/EPT (uncompressed Potree 2.0 is the target; COPC is the likely follow-up
  once loader support is settled).

## Decisions

### D1. Convert with PotreeConverter 2.0 in the geospatial image

Add the PotreeConverter 2.0 (BSD-2) Linux binary to the geospatial Dockerfile
(pinned version + checksum, `cmake` build or release binary) and implement
`POST /pointcloud/to-potree` to run it over a resolved LAS/LAZ path into a
scratch directory, returning the member files and a summary (point count,
bounds, EPSG/WKT, attributes, byte sizes). It emits exactly three files
(`metadata.json`, `hierarchy.bin`, `octree.bin`) with standard attributes
(elevation, RGB, intensity, classification) preserved.

- *Why*: matches the repo's stated direction (`TODO.md` "Potree format
  conversion", `SPEC.md` `/pointcloud/to-potree`, `TRD.md` `potree-core`) and
  gives format-v2 output a single directory/triple of files, which is what the
  loader ecosystem consumes. No compression means no binary format to police.
- *Alternatives considered*: PDAL `writers.copc` (one compressed file, but
  loader support for COPC in the three ecosystem is uneven); a pure-Python
  octree writer (large, risky); reusing the 3D Reconstruction plugin's decimated
  GLB (loses attributes and is a different feature).

### D2. Loader: a Potree 2.0-capable three.js loader with a URL hook

Adopt a loader that exposes Potree 2.0 and a relative-URL mapping hook —
`@pnext/three-loader` (v2 support) with the `potree-loader` (shiukaheng fork,
2.0 + WebGL2 + Vite) as a drop-in fallback. The hook maps the three member names
to their authenticated File URLs and rejects anything else.

- *Why*: the whole serving design hangs on being able to rewrite the loader's
  relative requests to session-authed same-origin URLs (`getUrl(relative)` is
  exactly that seam). Reusing one three.js renderer/scene is required for the
  "integrated into the existing viewer" decision.
- *Risk*: neither package declares three 0.185. A short loader spike verifies
  build and runtime against the repo's three version before committing; if both
  fail, fall back to a minimal loader over the documented metadata/hierarchy/
  octree format (three `BufferGeometry` + range fetches) rather than swapping the
  viewer stack.

### D3. On-demand, deduplicated conversion owned by a Frappe job

`GET/POST` a new `api/pointcloud.start(task_name)` endpoint, called when the
viewer opens a cloud whose `pointcloud_status` is `Not Converted`/`Failed`.
It flips status to `Queued` and enqueues `convert_point_cloud` on the `long`
queue with a `job_id`/dedup key (as `task_runner.enqueue_*` does). The job
resolves the LAZ via `assets` (cache-first/S3), calls the geospatial convert,
streams the three members into private files, records keys/summary, sets
`Ready`, and deletes the scratch dir. `start` is a no-op while
`Queued`/`Running`/`Ready` so concurrent opens are idempotent.

- *Why*: no processing-time or storage cost for unopened clouds, and the
  conversion is isolated from the single-writer `poll_task` path.
- *Alternative*: convert inside `_download_assets` at completion — rejected
  (pays for every task, couples the pipeline to an optional feature).
- *Progress*: the geospatial call is synchronous (like `cogify`); v1 shows an
  indeterminate Running state. A later change can add a progress file if needed.

### D4. Store octree members as private Files, tracked by a child table

Create one private `File` per member (via `plugins.files.save_private_file_from_path`,
attached to the task) so `/private/files/<name>` serves them with Frappe's
authenticated, range-capable handler; when storage is configured, upload each
to `orgs/<slug>/tasks/<task>/potree/` and record the key. Track the triple in a
new child `WebODM Point Cloud File` (`member`, `filename`, `file_url`,
`storage_key`, `file_size`, `content_type`). Add `potree` to
`TASK_OUTPUT_SUBPREFIXES` so reset and delete wipe the S3 prefix.

- *Why*: reuses the private-file permission model, the `materialize_private_file`
  S3 refill and the eviction unit instead of inventing a second binary-serving
  path. `getUrl` maps member names to these URLs, so hashed File names don't
  matter.
- *Serving cold*: extend `storage_key_for_file_url` (and `cache._candidates`)
  with a `WebODM Point Cloud File` branch so the existing refill/eviction logic
  treats octree blobs like task assets.
- *Alternatives*: a bespoke Frappe range-proxy endpoint (more code, re-derives
  what `/private/files/` already does); overloading `WebODM Task Asset.kind`
  (its `(parent, kind)` uniqueness and field-per-kind assumptions do not fit a
  three-file artifact).

### D5. Viewer integration: a source on the existing canvas

`ModelView` gains a point-cloud source (`?view=cloud` in the URL, alongside the
task model and `?run=`). `useModelViewer` is extended to hold an optional
`PointCloudOctree`: on cloud mode it adds the octree to the same scene, applies
the same Z-up→Y-up wrapper and recentring used for GLB, frames it with the
existing `framingFor`, and calls `potree.updatePointClouds(...)` in the render
loop, keeping render-on-demand active while the loader has pending nodes.
Point-cloud controls extend `ModelToolbar`, visible only in cloud mode.

- *Why*: one camera/controls/renderer, one source switcher, consistent framing
  and keyboard map — exactly the "integrated" decision.
- *Alternative*: a second viewer page/composable — duplicated renderer and
  controls, and a worse source switch.

### D6. Coloring, filtering and sizing map to Potree material state

Color modes set the material attribute/color type: RGB, elevation, intensity,
classification; modes whose attribute is absent in `metadata.json` are disabled.
Elevation range and classification visibility set the material's elevation
filter and classification mask. Point size and budget map to
`material.size`/`pointSizeType` and `potree.pointBudget`. The background uses the
existing scene clear color. Legends for elevation/classification derive from
`metadata.json` attribute ranges and the classes present.

### D7. Measurement is viewer-side, volume reuses the DSM path

Distance and area pick rendered points by raycast and compute in the cloud's
projected CRS (metric); filtered-out points are excluded. Volume sends the
picked polygon (reprojected to EPSG:4326) to the existing
`webodm_core.api.tiles.volume`, which already measures over the task's DSM; when
the task has no DSM the viewer says a surface is required instead of guessing.

- *Why*: distance/area need no server work, and volume is a solved, tested path
  that must not be reimplemented against a point set.

## Risks / Trade-offs

- **Loader/three compatibility** → Spike first; fallback is the fork or a
  minimal three-native loader. Do not start UI work before this passes.
- **Potree 2.0 is uncompressed** (`octree.bin` ~tens of bytes/point; 10 M points
  can be hundreds of MB) → on-demand conversion, range streaming, S3 as system of
  record and eviction bound local disk; COPC is the noted follow-up.
- **Long synchronous conversion ties up a geospatial worker and an RQ worker** →
  `long` queue, generous HTTP timeout, dedup, and retry; conversion is
  interruptible and resumable from scratch on restart. Coarse status only.
- **A gunicorn worker is held for the duration of a large streaming download** →
  range requests are small; Caddy streams without buffering. Note in deployment
  docs.
- **PotreeConverter binary supply chain** → pin a version and verify its
  checksum in the Dockerfile; build from source if no trusted binary.
- **Range serving correctness** → verified in Frappe source (`conditional=True`);
  add an integration test that asserts a 206 + `Content-Range` from
  `/private/files/` for an octree member.
- **Attribute coverage varies** (some LAZ lack RGB/intensity) → the loader-driven
  mode enablement (D6) makes missing attributes a disabled control, not an error.

## Migration Plan

1. Geospatial image: add PotreeConverter + `/pointcloud/to-potree` (+ tests);
   deploy.
2. Frappe: add the task fields, child doctype, `pointcloud` API, job, storage
   branches; `bench migrate` adds columns/child (additive, no backfill —
   existing tasks default to `Not Converted`). Deploy.
3. Frontend: ship the loader + viewer changes behind the source switch; tasks
   without a cloud simply show no cloud source.
4. Rollback: revert images. Extra columns/child and any `potree/` objects/Files
   are inert; deleting a task or re-processing removes them. No schema data
   migration to undo.

## Open Questions

None blocking. The loader choice (D2) is decided with a spike and a defined
fallback; conversion progress granularity (D3) is a deliberate v1 trade-off,
revisit only if users report it.
