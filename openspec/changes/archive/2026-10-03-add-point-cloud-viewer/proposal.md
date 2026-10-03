## Why

Every completed task stores a `point_cloud` (ODM's `georeferenced_model.laz`), but the
platform can only display it indirectly — by running the 3D Reconstruction plugin to
decimate it into a GLB. There is no way to see the actual point cloud, its density,
elevation, classification or intensity, and multi-million-point LAZ files cannot be
loaded in a browser as-is. The geospatial service already reserves
`/pointcloud/to-potree` and `/pointcloud/export` for exactly this, and the 3D-viewer
roadmap lists "Point cloud (LAZ) viewer" as an open item.

## What Changes

- The geospatial service gains a working **LAZ/LAS → Potree 2.0 octree** conversion
  (`POST /pointcloud/to-potree`) and an authenticated streaming endpoint for the
  octree's three files (`metadata.json`, `hierarchy.bin`, `octree.bin`) with HTTP
  range support, reading from local storage or `s3://`.
- Frappe gains a `pointcloud` API: start/refresh an on-demand conversion, report its
  status, and serve the octree files through a same-origin, session-authed proxy
  (mirroring the existing raster tile proxy) so the browser never talks to the
  geospatial service directly.
- The task's point-cloud conversion state and summary (point count, bounds, CRS,
  attributes, octree size) are persisted on `WebODM Task`, reusing the
  child-row/refresh conventions established for raster metadata.
- The existing 3D viewer (`ModelView`) gains a **Point cloud** source alongside the
  ODM model and reconstruction runs, rendering the octree with an LOD point budget.
- Point-cloud viewing controls: camera presets and navigation, point size,
  background, color modes (RGB / elevation / intensity / classification), elevation
  and classification filtering, and measurement (distance, area, and volume where a
  surface model is available).
- Derived octrees are stored under the task's organization namespace
  (`orgs/<slug>/tasks/<task>/potree/`), served cache-first/storage-second, evicted
  when idle, and deleted with the task.

## Capabilities

### New Capabilities
- `point-cloud-viewer`: on-demand LAZ→Potree conversion, authenticated octree
  streaming, and in-viewer rendering/LOD/coloring/filtering/measurement of a task's
  point cloud.

### Modified Capabilities
- `object-storage`: the Potree octree joins the canonical derived artifacts stored
  under the organization-namespaced task prefix, and follows the same
  serving/cache/eviction/deletion lifecycle as other task outputs.

## Impact

- **Geospatial service** (`services/geospatial/`): implement the `pointcloud` router,
  add PotreeConverter to the image, scratch-dir + S3 output handling, and tests.
- **Frappe** (`webodm_core`): new `api/pointcloud.py`; conversion job/queueing;
  `WebODM Task` fields and a point-cloud metadata child (or JSON); storage prefix,
  cache/eviction, `on_trash` cleanup; a `before_request` or route hook for range
  streaming; hooks/permissions for the new endpoint.
- **Frontend** (`webodm_frontend`): new point-cloud loader dependency (Potree 2.0
  capable, verified against three 0.185); `ModelView` source switch; point-cloud
  controls in `ModelToolbar`; a measurement overlay; `lib/pointcloud.js` helpers and
  tests.
- **Infrastructure**: geospatial Dockerfile (PotreeConverter binary), compose and
  Helm geospatial image, CI geospatial tests; docs under `docs/`.
- **Compatibility**: additive. Tasks without a point cloud, and deployments with no
  object storage, behave as today; conversion is on-demand so no processing-time or
  storage cost is paid for tasks whose cloud is never opened.
- **Non-goals**: viewing point clouds from plugin-run outputs or arbitrary uploads;
  EPT/COPC or a server-side elevation profile; editing or re-exporting the cloud;
  converting the cloud eagerly at processing time.
