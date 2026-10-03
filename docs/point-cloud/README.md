# Point cloud (LAZ) viewer

Open a completed task's georeferenced point cloud directly in the 3D viewer:
orbit it, colour it by RGB / elevation / intensity / classification, filter by
elevation and class, change point size, budget and background, and measure
distance, area and (with a DSM) volume. The cloud is streamed as a Potree 2.0
octree that is built on demand the first time someone opens it.

| Document | For whom |
|---|---|
| [User guide](#user-guide) | Anyone using the viewer |
| [Architecture](#architecture) | Developers |
| [Decisions](#decisions) | Developers / reviewers |
| [Operations](#operations) | Operators |
| [Testing](#testing) | Developers |
| [`openspec/specs/point-cloud-viewer/spec.md`](../../openspec/specs/point-cloud-viewer/spec.md) | Requirements and scenarios |

## User guide

**Opening.** From the project map, select a task and click **Point cloud**
(shown when the task produced `georeferenced_model.laz`), or pick
*`<task>` · Point cloud* in the 3D viewer's source switcher. The selected
source is kept in the URL (`/model?source=pointcloud`), so links can be shared.
The switcher also lists the task's textured model and its 3D reconstruction
runs, and the point clouds / models of the other tasks in the project.

**First open.** The first time a cloud is opened the server converts it into
a streamable octree ("Preparing point cloud…"). This takes seconds for a few
million points and a few minutes for very large clouds; every later open is
instant. If the conversion fails, the error is shown with a **Retry** button.

**Navigation** is the same as for models (Rotate / Pan / Zoom modes, scroll,
double-click to focus, presets 1–4, `R` reset, `G` grid, `F` fullscreen,
`?` help). The scene is Z-up with north along +Y.

**Point cloud panel** (top right):

- *Colour by* — RGB, Elevation, Intensity, Classification. Only the modes
  whose attribute exists in this cloud are shown: no RGB in the LAZ → no RGB
  button; an intensity that is zero everywhere or a single classification
  value is not offered either.
- *Elevation* — min/max sliders over the cloud's elevation range; points
  outside are hidden in every colour mode. *Reset* clears the filter.
- *Classes* — one checkbox per class present (ASPRS names, Potree's colours).
  Hidden classes disappear in every colour mode. Shown only when the cloud has
  more than one class.
- *Point size* (1–8 px), *Point budget* (0.5–10 M points drawn at once; the
  list is capped on phones and low-memory devices) and *Background*. These
  three are remembered in the browser.

**Measuring** (toolbar, point cloud only):

- *Distance* — click points on the cloud; the readout is the 3D length of the
  polyline. Finish with Enter or a double-click.
- *Area* — click 3+ points; the readout is the horizontal (projected) area.
  Close by clicking the first point, Enter or double-click.
- *Volume* — same drawing as area; the polygon is sent to the task's DSM and
  the fill / cut / net volume comes back from the same algorithm the map's
  volume tool uses (base method as selected on the map). The button is
  disabled when the task has no DSM — a volume is never estimated from the
  points.
- Esc cancels the shape being drawn (a second Esc leaves the tool); `×` on a
  label removes that measurement; the bin icon clears all.

**Download** in the header gives the original LAZ.

## Architecture

```
browser ──(session cookie)──▶ Frappe ──▶ geospatial service (backend network only)
   │  potree_state(start=1)        │  POST /pointcloud/to-potree  ──▶ PotreeConverter 2.x
   │  ◀── Queued | Running | Ready │  ◀── {files, points, bbox, attributes…}
   │                               │
   │  GET /private/files/<task>_potree_{metadata.json,hierarchy.bin,octree.bin}
   │      Range: bytes=a-b  ──▶ 206 Partial Content (werkzeug conditional send_file)
   ▼
potree-core (three.js r185) ── LOD streaming, picking, measurements
```

### Conversion (lazy, deduplicated, retriable)

- `webodm_core.api.pointcloud.potree_state(task_name, start, retry)` is what
  the viewer calls. With `start=1` it runs `processing/potree.ensure()`:
  the task row is locked (`SELECT … FOR UPDATE`), the state is read, and a
  job is enqueued only when there is none — two people opening the same
  cloud produce one conversion. The job uses `job_id="webodm:potree:<task>"`
  with `deduplicate=True`, so the queue de-duplicates as well.
- State lives on `WebODM Task`: `potree_status` (`Queued | Running | Ready |
  Failed`), `potree_error`, `potree_source` (the `point_cloud` URL the octree
  was built from), `potree_summary` (points, bounds, spacing, projection,
  attribute ranges, output sizes, storage kind) and `potree_updated`.
- `convert_job` (queue `long`, 2 h + margin) resolves the LAZ, calls
  `plugins.geospatial.to_potree`, attaches the three files to the task as
  private files named `<task>_potree_<file>`, records `WebODM Task Asset`
  rows (`potree_metadata` / `potree_hierarchy` / `potree_octree`) and writes
  `Ready`; any exception writes `Failed` + the message.
- `Failed` is only retried on request (`retry=1`). A `Queued`/`Running`
  state older than the job timeout (worker died) is restarted on the next open.
- A `Ready` octree whose `potree_source` no longer matches `point_cloud`
  reports as not converted and is rebuilt on open.
- Re-processing (`api.task.process_task` → `storage.assets.reset_outputs`)
  clears the state, the asset rows, the File documents and blobs, and the
  bucket objects. Deleting a task removes the files (`WebODMTask.on_trash` →
  `potree.clear_files`, then Frappe's attachment cleanup) and the task's
  bucket prefix. Tasks whose cloud is never opened cost nothing.

### Geospatial service

`POST /pointcloud/to-potree {path, output_path, projection?, name?}` — see
`services/geospatial/README.md`. Both paths are local (shared `sites` volume)
or both `s3://` URIs; an S3 source is downloaded to `COG_SCRATCH_DIR`,
converted there and the three files uploaded under the prefix, so nothing
touches the host disk. The handler is synchronous (the Frappe job holds the
request, as for cogify) and bounded per worker by `POTREE_MAX_CONCURRENT`.
`GET /pointcloud/converter` and `/health` (`potree_converter: true|false`)
report whether the binary is installed.

`app/utils/pointcloud.py` parses the LAS public header + VLRs itself (point
count, format, bounds, WKT or GeoTIFF-key EPSG) — no PDAL / laspy — and runs
the converter into a temporary sibling directory that is renamed into place
only when all three files exist and `metadata.json` parses. The projection
is written into `metadata.json` by Python because the converter's own
`--projection` embeds WKT unescaped and produces invalid JSON.

### Serving

The octree files are ordinary private files attached to the task, served by
Frappe's `/private/files/<name>` route: the session cookie authenticates,
`File.has_permission` delegates to the task (organization isolation), and
werkzeug's conditional `send_file` answers `Range` requests with
`206 Partial Content` + `Content-Range` (and `416` past the end). This is
asserted by `api/test_pointcloud.TestServing` through the WSGI app. Caddy
only ever proxies `/private/files/**` to Frappe; the geospatial service stays
on the backend network.

With object storage, the files live at
`orgs/<slug>/tasks/<task>/assets/potree/{metadata.json,hierarchy.bin,octree.bin}`
and the host copies are cache entries (asset rows with a `storage_key`), so
`storage.cache.evict` can drop them and `serving.materialize_private_file`
refills them on request like any other output. `potree_state` reports
`cache: "warming"` and enqueues the refill when any file is cold; the viewer
waits for `"warm"` so the first byte-range request never blocks on a
multi-GB download inside a web worker.

### Frontend

- `lib/potree.js` — pure helpers: request manager (virtual
  `potree://<task>/metadata.json` → private URLs, cookies and `Range`
  preserved), attribute detection (`availableColorModes`,
  `classificationsPresent` from the converter's histogram), settings
  persistence, measurement math, the backend calls.
- `composables/pointCloudLayer.js` — potree-core: load, per-frame LOD
  update, material settings (colour mode, size, budget, elevation clip box,
  class LUT), GPU picking, measurement geometry, screen projection.
- `composables/useModelViewer.js` — one renderer/camera/controls for models
  and clouds; `loadPointCloud`, `setPointCloudOption`, the measurement state
  machine (click = vertex, Enter / double-click / first-vertex click =
  finish, Esc = cancel) and screen-space labels refreshed after each render.
- `components/PointCloudPanel.vue`, `components/ModelToolbar.vue` (measure
  chip), `pages/ModelView.vue` (source switch, conversion polling, retry,
  volume via `api.tiles.volume` with `polygon_crs`).

## Decisions

**PotreeConverter 2.1.1, built from source in the geospatial image.** No
Linux package exists and upstream binaries are Windows-only. 2.1.1 is the
latest tag that compiles with Ubuntu 24.04's GCC 13; 2.1.5 needs GCC 14 and
still fails on `nlohmann::json` assignment overloads. The tag is an `ARG`
(`POTREE_CONVERTER_REF`) so it can be bumped. Output is
`--encoding UNCOMPRESSED` (the spec target; Brotli is a loader-dependent
follow-up, as is COPC).

**potree-core 2.0.15 as the loader**, after proving it against three
`0.185.1` in headless Chrome (software WebGL). Its `RequestManager` is the
URL-rewrite seam the spec asked for. Three defects were found and are patched
at runtime on the material (`pointCloudLayer.patchMaterial`), not by forking:

1. On the Potree 2 code path the vertex shader assigns `vColor = rgba`
   regardless of the colour type, so elevation / intensity / classification
   never applied. The define block is rewritten.
2. Hidden classes (LUT alpha 0) were only culled while colouring by
   classification; the guard is widened so the class filter works in every
   colour mode.
3. The default sRGB→linear encoding pass turns Potree 2 colours white on
   ANGLE; both encodings are set to linear (data and canvas are both sRGB).

Elevation filtering uses a clip box (public API) rather than clipping planes,
whose shader define potree-core forgets to set. Fallback if potree-core ever
breaks: the format is three files with documented layouts; a small loader is
feasible, but was not needed.

**Z-up point cloud scene.** Potree colours and clips by world Z, so the
cloud keeps ODM's native axes instead of the model viewer's Y-up rotation.
`presetPosition(…, up='z')` gives the same presets; the grid is rotated.
Picked coordinates are `local + origin`, i.e. the cloud's native CRS, which
is what measurements and the volume call use.

**Serve as private files, not a new streaming endpoint.** Reuses the session
auth, the org permission chain through the task, the cache/eviction
machinery and Frappe's own range support — confirmed with a test before the
UI relied on it.

**Volume via the DSM endpoint.** `/volume` (geospatial) and
`api.tiles.volume` gained `polygon_crs` (`EPSG:4326` default, `"native"` =
the DSM's CRS, or any WKT / `EPSG:n`); the viewer sends the octree's recorded
projection (or `native`). Without a DSM the tool is disabled and the API
refuses, so no number is ever guessed from a point set.

**Attribute presence = present and informative.** RGB needs a non-zero max;
intensity needs `max > min`; classification needs more than one class in
the converter's histogram. Elevation is always available.

**Coarse state only.** Queued / Running / Ready / Failed plus an error; the
converter does not report progress and the spec does not need it.

## Operations

### Geospatial service

| Variable | Default | Meaning |
|---|---|---|
| `POTREE_CONVERTER_BIN` | `PotreeConverter` | Converter binary (absolute path or on `PATH`). |
| `POTREE_MAX_CONCURRENT` | `1` | Conversions per uvicorn worker (`WEB_CONCURRENCY=2` → 2 at most). |
| `POTREE_TIMEOUT_SECONDS` | `7200` | Kill the converter after this long; the task's octree is marked Failed. |
| `COG_SCRATCH_DIR` | `/tmp` (`/scratch` in compose/helm) | Also holds the LAZ + octree of an S3 → S3 conversion (~3× the LAZ). |

Memory: the converter is out-of-core but still wants roughly 1 GB per 50 M
points. The compose service limit is 2 GB and the Helm default has no
request; raise `geospatial.resources` / `geospatial.potree.*`
(`values.yaml`) for large surveys. Compose exposes
`POTREE_MAX_CONCURRENT` / `POTREE_TIMEOUT_SECONDS` as `.env` overrides.

IAM: the geospatial identity may now also `PutObject` under
`assets/potree/*` (`infra/aws/iam/storage-geospatial-policy.json`). Apply the
updated policy when upgrading a deployment that uses object storage,
otherwise conversions fail with `object storage: upload failed`.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "The point cloud could not be prepared — PotreeConverter is not installed on the geospatial service" | The geospatial image predates this feature or `POTREE_CONVERTER_BIN` is wrong. `GET /pointcloud/converter` on the service shows what it sees. |
| Conversion Failed with `exceeded 7200 s` | Very large cloud or starved CPU; raise `POTREE_TIMEOUT_SECONDS`, then Retry in the viewer. |
| Failed with `upload failed` | Geospatial IAM policy lacks `assets/potree/*`. |
| "Preparing point cloud…" for a long time | `potree_status` on the task tells which stage; the RQ job id is `webodm:potree:<task>`. A state older than ~2 h 15 min is restarted automatically on the next open. |
| Octree Ready but the viewer says files are missing | Host copies were deleted outside the app without object storage; Retry rebuilds. |
| Colours look wrong / white in a new browser | See the shader patches above; check the console for a `THREE.WebGLProgram` error after a potree-core or three upgrade. |

### Frappe

Run `bench migrate` after upgrading: `WebODM Task` gains the `potree_*`
fields and `WebODM Task Asset.kind` the three `potree_*` options.

## Testing

- Geospatial: `cd services/geospatial && ./venv/bin/python -m pytest -q
  tests/test_pointcloud.py` — header parsing, validation, local and S3
  (moto) conversions. Conversion tests skip without the binary; CI runs them
  inside the built image (`geospatial-image` job). For a native run point
  `POTREE_CONVERTER_BIN` at a built binary (`docker cp` it out of the image
  together with `liblaszip.so` and `libtbb.so.12`).
- Frappe: `webodm_core.api.test_pointcloud` (19 tests: dedup, retry, stale
  restart, host and S3 modes, cache warming, reprocess/delete cleanup, org
  isolation, `206` serving through the WSGI app) plus `api.test_tiles`
  (`polygon_crs`). Worktree recipe: see AGENTS.md Phase 21.
- Frontend: `npm test` (`lib/potree.test.js`, `components/PointCloudPanel.test.js`,
  `lib/modelViewer.test.js`). Browser: `e2e/point-cloud.e2e.mjs` drives the
  real page in headless Chrome against a mocked backend that serves a
  converted octree with byte ranges — conversion states, colour modes,
  filters, measurements, the DSM volume call, the no-DSM guard and the retry
  path (`e2e/README.md`).

## Out of scope / follow-ups

Plugin-run point clouds, arbitrary uploads and non-LAS inputs; fine-grained
conversion progress; elevation profiles; exporting or reclassifying the
cloud; COPC / EPT output (likely next once loader support settles —
`/pointcloud/export` stays a 501 stub until then).
