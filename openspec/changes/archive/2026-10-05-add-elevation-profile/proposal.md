## Why

The point cloud viewer can measure a distance, an area or a volume, but not
answer the most common question about a site: what do the ground and
everything above it look like along a line? Road and drainage grades,
pipeline and power line corridors, vegetation clearance to a structure,
stockpile cross-sections and bridge decks over ground are all line
questions, and nothing in the platform answers them from the point cloud.

## What Changes

- The 3D viewer's measurement group gains an **Elevation profile** tool:
  pick 2–50 points on the cloud, finish, and a chart opens under the view
  with the ground, the top surface, the space between them, one band per
  classification layer, hatched gaps, polyline vertices, a hover readout
  linked to a marker in the scene, click-to-focus, width / step / vertical
  scale controls, a stats strip (length, elevation range, ascent / descent,
  steepest grade, heights, coverage), a legend that toggles layers, and a
  CSV export.
- The geospatial service gains a **server-side Potree 2.0 octree reader**
  (`app/utils/potree_octree.py`) and `POST /pointcloud/profile`
  (`app/utils/profile.py`): only the octree nodes under the corridor are
  read, by byte range, from the host cache or object storage; a point
  budget drops the deepest levels uniformly when exceeded and says so.
  Ground is the class-2 median for classified clouds and a
  morphologically filtered low envelope otherwise; noise classes are
  excluded; gaps stay gaps; ground under objects is interpolated and
  returned separately.
- Frappe gains `webodm_core.api.pointcloud.profile`: read permission on the
  task, input validation, resolution of the octree files (Ready state,
  ownership, cache-first / S3-second) and the proxy call. The browser never
  reaches the geospatial service.
- Operations: `PROFILE_MAX_POINTS` / `PROFILE_MAX_BINS` in compose, Helm and
  the image; CI runs the profile tests inside the geospatial image.

## Capabilities

### Modified Capabilities
- `point-cloud-viewer`: adds the *Elevation profile* requirement (server-side
  sampling of the whole cloud along a transect, classification layers,
  gaps, budget behaviour, permissions, 3D linkage).

## Impact

- **Geospatial service**: new reader and profile modules, endpoint, tests
  with a synthetic Potree writer and a real-converter check; no new system
  dependency (NumPy + SciPy, already present).
- **Frappe** (`webodm_core`): API method, `processing.potree.file_sources`,
  `plugins.geospatial.pointcloud_profile`, unit tests.
- **Frontend** (`webodm_frontend`): `lib/profile.js`, `ProfileChart.vue`,
  `ProfilePanel.vue`, the `profile` measurement kind, layer drawing, viewer
  wiring in `ModelView.vue`; no new dependency.
- **Compatibility**: additive. Tasks never opened in the viewer have no
  octree and the API says so; everything else is unchanged.
- **Non-goals**: profiles from the DSM/DTM rasters, several simultaneous
  profiles, saving profiles with the task, scene → chart hover, and
  Brotli-encoded octrees (the platform writes uncompressed ones).
