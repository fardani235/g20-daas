# Point Cloud Viewer Specification

## Purpose

Lets a user open a completed task's georeferenced LAS/LAZ point cloud in the
existing 3D viewer as a progressively streamed Potree 2.0 octree, colour and
filter it by the attributes it actually has, and measure distance, area and
DSM-backed volume on it, and draw a transect to get an elevation profile of
the whole cloud (ground, top, classification layers, gaps) computed
server-side from the octree — converting lazily on first open, serving the
octree through the app's session-authenticated private files, and keeping
organization isolation everywhere.

## Requirements

### Requirement: Lazy, deduplicated, retriable conversion

The system SHALL convert a task's point cloud to a Potree 2.0 octree
(`metadata.json`, `hierarchy.bin`, `octree.bin`, uncompressed) with
PotreeConverter 2.x inside the geospatial service the first time the cloud
is opened, SHALL reuse the result on later opens, SHALL track the state on
the task as Queued | Running | Ready | Failed plus an error message, SHALL
start at most one conversion per task under concurrent opens, SHALL retry a
Failed conversion only on request, and SHALL restart a Queued/Running state
whose worker is gone.

#### Scenario: Two users open the same cloud

- **WHEN** two sessions call `potree_state(start=1)` for the same task at once
- **THEN** exactly one background job is enqueued and both see `Queued`

#### Scenario: Never opened

- **WHEN** a task completes and nobody opens its point cloud
- **THEN** no conversion runs and no octree is stored

#### Scenario: Retry after failure

- **WHEN** the state is Failed and the user clicks Retry (`retry=1`)
- **THEN** the state becomes Queued and a new job runs; without `retry=1` the Failed state is returned unchanged

### Requirement: Cleanup on re-process and delete

Re-processing a task SHALL discard the octree (state, asset rows, files,
blobs, bucket objects); deleting a task SHALL remove the octree files and
objects.

#### Scenario: Re-process

- **WHEN** `process_task` restarts a task with a Ready octree
- **THEN** `potree_status` is empty, no `potree_*` asset rows or File documents remain, the blobs are gone and `assets/potree/` has no objects

#### Scenario: Octree built from a previous cloud

- **WHEN** `potree_source` differs from the task's current `point_cloud`
- **THEN** the state reports as not converted and opening rebuilds it

### Requirement: Served as private files with byte ranges

The octree files SHALL be served by the app as private files of the task:
same-origin, session-authenticated, permission-checked through the task's
organization, with HTTP `Range` requests answered by `206 Partial Content`
and `Content-Range`. The browser SHALL never reach the geospatial service.

#### Scenario: Range request

- **WHEN** a member of the task's organization requests `bytes=10-19` of `octree.bin`
- **THEN** the response is `206` with `Content-Range: bytes 10-19/<size>` and exactly those bytes

#### Scenario: Outsider

- **WHEN** a user from another organization, or no session, requests an octree file
- **THEN** the response is `403`

### Requirement: Object storage layout and cache

When object storage is configured the octree SHALL be stored under
`orgs/<slug>/tasks/<task>/assets/potree/` and the host copies SHALL be cache
entries that can be evicted and refetched; the viewer SHALL be told when a
refetch is in progress. Deployments without object storage SHALL work with
host files only.

#### Scenario: Evicted host copy

- **WHEN** `octree.bin` was evicted and the cloud is opened
- **THEN** `potree_state` returns `Ready` with `cache: "warming"`, a dedup'd refill job runs, and the next poll returns `cache: "warm"`

### Requirement: Viewer

The viewer SHALL load the octree progressively with a Potree 2.0-capable
three.js loader whose file requests are rewritten to the app's URLs, SHALL
offer colour modes only for attributes present in the cloud (RGB with
non-zero colour, elevation always, intensity with a non-degenerate range,
classification with more than one class), SHALL filter by elevation range and
classification in every colour mode, SHALL let the user set point size,
background and a point budget, and SHALL keep the selected source
(`?source=pointcloud`) in the URL alongside the model and reconstruction runs
in the source switcher.

#### Scenario: Cloud without intensity

- **WHEN** the octree's `intensity` attribute has `min == max`
- **THEN** no Intensity colour mode is offered

#### Scenario: Elevation filter

- **WHEN** the user narrows the elevation range while colouring by RGB
- **THEN** points outside the range are not drawn

### Requirement: Measurements

Distance and area SHALL be picked on the rendered points (distance = 3D
polyline length, area = horizontal polygon area, in the cloud's CRS units).
Volume SHALL be computed by the existing DSM volume endpoint from the picked
polygon expressed in the cloud's CRS, and SHALL be unavailable (never
estimated) when the task has no DSM.

#### Scenario: Volume with a DSM

- **WHEN** the user closes a volume polygon on a task with a DSM
- **THEN** `api.tiles.volume` is called with the polygon in the octree's recorded projection (`polygon_crs`) and the fill / cut / net result is shown

#### Scenario: Volume without a DSM

- **WHEN** the task has no DSM
- **THEN** the Volume tool is disabled and any direct call is refused

### Requirement: Elevation profile along a transect

The viewer SHALL let a user draw a transect (a polyline of 2–50 vertices
picked on the point cloud) and SHALL show an elevation profile along it
computed on the server from the **whole** point cloud — never from the
points currently loaded in the browser — reading only the octree nodes
under the corridor, from the host cache or object storage. The profile
SHALL show the ground, the top surface and the height between them; where
the cloud is classified it SHALL show one layer per classification code
(excluding unclassified and noise codes), and where it is not it SHALL
still show ground and top. Bins with no points SHALL be shown as gaps,
never as values. When the points under the corridor exceed the configured
budget the service SHALL drop the deepest octree levels uniformly and
SHALL report the sampling actually used. The chart SHALL present length,
elevation range, ascent and descent, steepest grade, heights and coverage,
and pointing at the chart SHALL mark the corresponding spot in the 3D
scene. The request SHALL require read permission on the task and SHALL be
refused for a task whose octree is not Ready.

#### Scenario: Classified cloud

- **WHEN** a transect crosses a building and a tree row in a cloud with ASPRS classes
- **THEN** the ground is the median of class-2 points per bin, the building and the vegetation are separate layers with their heights above ground, ground under the building is interpolated and marked as such, and noise classes are excluded

#### Scenario: Unclassified cloud

- **WHEN** the cloud carries no classification (or only codes 0/1)
- **THEN** the profile shows ground and top only, the ground is a filtered low envelope labelled as such, and the UI points at the `pc-classify` option

#### Scenario: Gap

- **WHEN** a run of bins along the line contains no points
- **THEN** every series is `null` there, the run is listed in `gaps` and drawn as a hatched column, and summary numbers never bridge it

#### Scenario: Very large cloud

- **WHEN** the nodes under the corridor hold more points than `PROFILE_MAX_POINTS`
- **THEN** whole octree levels are dropped deepest-first until the budget fits, the step is widened to the sampled spacing, and `sampling.full_resolution` is false with the spacing reported

#### Scenario: Octree in object storage

- **WHEN** the host copy of `octree.bin` has been evicted
- **THEN** the profile is read from the `s3://` object by byte range (mixed with cached local files) and a background refill is queued

#### Scenario: Outsider or unprepared cloud

- **WHEN** a user from another organization calls the profile API, or the task has no Ready octree
- **THEN** the call is refused with a permission error, or with a message saying the point cloud must be opened in the viewer first
