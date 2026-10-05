# Point cloud elevation profile

Draw a line across a task's point cloud in the 3D viewer and get an
elevation profile along it: the ground, the top surface, the space between
them, and — where the cloud is classified — one layer per class (vegetation,
buildings, wires, towers, bridge decks…). The profile is computed from the
**whole** point cloud on the server, not from the points the browser happens
to have loaded, copes with billion-point clouds and with clouds in object
storage, shows gaps where there is no data, and is linked to the 3D view.

| Document | For whom |
|---|---|
| [User guide](#user-guide) | Anyone using the viewer |
| [The problem and the options](#the-problem-and-the-options) | Reviewers: why this design |
| [Architecture](#architecture) | Developers |
| [Sampling and summarising](#sampling-and-summarising) | Developers / surveyors: what every number means |
| [API](#api) | Integrators |
| [Decisions](#decisions) | Developers / reviewers |
| [Operations](#operations) | Operators |
| [Testing](#testing) | Developers |
| [Limits and follow-ups](#limits-and-follow-ups) | Everyone |
| [`openspec/specs/point-cloud-viewer/spec.md`](../../openspec/specs/point-cloud-viewer/spec.md) | Requirements and scenarios |

The viewer itself (conversion, streaming, colouring, filtering, the other
measurements) is documented in [`README.md`](README.md).

## User guide

1. Open a task's point cloud in the 3D viewer (**Point cloud** on the map
   card, or the viewer's source switcher).
2. In the toolbar's measurement group, pick **Elevation profile** (the
   line-chart icon). Click two or more points on the cloud to draw the
   transect — a road centreline, a corridor, a line through a stockpile —
   then press **Enter** or double-click. Esc cancels.
3. A chart opens under the view. The tool switches itself off so you can
   orbit; pick it again to draw a new line (a new line replaces the previous
   profile).

**The chart.** Distance along the line on the horizontal axis (polyline
vertices are marked), elevation on the vertical axis.

- The **brown line** is the ground. It is solid where the ground was
  measured and dashed where it is interpolated (under a roof, a dense canopy
  or a small hole in the data).
- The **green line** is the top surface (the highest non-noise point in
  each bin), and the light blue fill between them is the space above the
  ground.
- Each **classification layer** is a coloured band from the lowest to the
  highest point of that class in each bin (Potree's class colours, same as
  the viewer's legend). Click a legend entry to hide or show a layer. The
  legend shows each layer's maximum height above ground.
- The grey **envelope** is the full min–max range of all points in each bin.
- **Hatched columns** are gaps: bins with no points at all. Nothing is drawn
  there and the lines break; no value is invented.
- Point at the chart to read distance, ground, top, height and each layer's
  height above ground; the same spot is marked in the 3D view with a
  vertical bar from ground to top. Click the chart to move the camera there.

**Controls.** *Width* is the corridor width (0.25–10 m): points within half
the width on either side of the line are used. A wider corridor is smoother
and finds thin features (wires) more reliably; a narrower one is sharper.
*Step* is the bin size along the line (Auto ≈ 500 bins, never finer than the
sampled point spacing). *Scale* switches between a chart that fills the
panel and a true 1 : 1 shape (the exaggeration factor is printed in the
chart's corner). Width, step and scale are remembered in the browser.

**Numbers.** Length (horizontal, and along the ground), elevation range of
the ground with start and end, ascent and descent (changes under 0.25 m are
not counted, so noise does not add up), steepest grade over 2 m and the mean
grade, maximum and mean height above ground, and coverage (bins with data,
points used). A note under the chart says how the ground was derived and,
for an unclassified cloud, how to get classification layers (the
`pc-classify` processing option). If the cloud is so dense that the deepest
detail had to be skipped for this line, the note says at what spacing it
was sampled; shorten the line or narrow the corridor for full detail.

**CSV** downloads one row per bin (distance, position, ground with its
source, top, height, min, max, counts and the per-class columns).

## The problem and the options

A user needs the vertical structure of the site along a line: what the
ground does, what stands on it and how tall it is, with vegetation, wires and
structures told apart when the cloud knows them. Four places the work could
happen were considered.

**In the browser, from the loaded octree nodes.** Potree streams a point
budget chosen for the current view (0.5–10 M points), so what is in memory
is whatever level of detail the camera happens to demand. A profile built
from it would change with the zoom, miss the deepest levels along most of
the line and read nothing where the camera has not looked. The requirement
is explicit that the profile reflects the real cloud, so this was rejected
outright; it also would not scale (a 1 km transect across a dense cloud
needs millions of points the browser never has).

**From the DSM / DTM rasters.** Sampling the task's DSM and DTM along the
line is cheap and gives a ground and a top. It was rejected as the primary
source because it is 2.5D — a wire, a bridge deck over ground, a canopy's
underside and anything with a vertical extent collapse into one surface
value per pixel — and it carries no classification. Nothing stops a later
change from overlaying the DTM as a reference line.

**From the LAS/LAZ file on the server.** Correct and complete, but a LAZ
has no spatial index: every profile would decompress and scan the whole
file (seconds for 5 M points, minutes for a few hundred million), and with
object storage it would download the whole file first. Any interactivity
would need a spatial index built and cached per task — which the platform
already has.

**From the Potree octree on the server — chosen.** The viewer already
converts each cloud it shows into a Potree 2.0 octree (`metadata.json`,
`hierarchy.bin`, `octree.bin`, uncompressed) and keeps it as a task asset,
on the host and in object storage. That octree *is* a spatial index of the
full cloud: every point is stored exactly once, in the level of detail
where the converter's Poisson sampling placed it, and every node's point
count and byte range is in the hierarchy. A query for a thin corridor
therefore reads only the nodes whose boxes the corridor crosses, at every
level, by byte range — from disk or straight from S3 — and the cost scales
with the line, not with the cloud. The reader is ~300 lines of Python with
NumPy, needs no PDAL or laspy, and the artefact it reads is guaranteed to
exist for any cloud a user is looking at.

The trade-offs accepted with this choice: the profile is only available for
clouds that have been opened in the viewer at least once (the viewer builds
the octree on first open; the API says so clearly otherwise); and it reads
Potree's quantised coordinates (1 mm, the same the viewer shows) rather than
the LAZ's — verified to match the LAZ to within that quantisation.

## Architecture

```
browser ── pick 2..50 points on the cloud (native CRS) ──▶ Frappe
   │  POST webodm_core.api.pointcloud.profile          │  check_permission(read) on the task
   │       {task_name, line, width, step}              │  octree Ready? file rows attached to this task?
   │                                                   │  resolve each file: host cache path | s3:// URI
   │                                                   ▼
   │                                      geospatial  POST /pointcloud/profile
   │                                         open metadata.json
   │                                         hierarchy.bin: load root chunk, descend only into
   │                                           sub-chunks whose node boxes meet the corridor (waves)
   │                                         budget: keep whole levels, shallow first
   │                                         octree.bin: coalesced byte-range reads of the chosen nodes
   │                                         NumPy: corridor test, binning, per-bin / per-class stats,
   │                                           ground filter, gap fill, climb / grade / height summary
   │  ◀── JSON: distance, ground, ground_fill, top, height, min, max, count,
   │            classes[], gaps, stats, sampling
   ▼
ProfilePanel.vue (chart, legend, stats, CSV) ⇄ pointCloudLayer (transect, corridor, sampled lines, hover marker)
```

### Geospatial service

- `app/utils/potree_octree.py` — the Potree 2.0 reader. `RangeSource`
  abstracts byte-range reads over a local file (`os.pread`) or an S3 object
  (`GetObject` with `Range`, through the service's allow-listed
  `objectstore` client). `Octree.select_nodes(intersects)` parses the root
  hierarchy chunk, keeps the nodes whose boxes satisfy the predicate and
  expands proxy nodes (sub-hierarchy chunks) in *waves* — all proxies of the
  current frontier in one coalesced batch — so round trips to storage are
  bounded by the hierarchy depth, not the node count. `read_nodes` coalesces
  the chosen nodes' byte ranges (gap ≤ 1 MB, request ≤ 32 MB), reads them
  concurrently and views the bytes as a NumPy structured array built from
  `metadata.attributes` (interleaved records; `position` is `int32 × 3`
  scaled by `scale` plus `offset`).
- `app/utils/profile.py` — the algorithm (next section).
- `POST /pointcloud/profile` in `app/routers/pointcloud.py`; `GET
  /pointcloud/profile/limits` reports the budgets. The three file references
  may be any mix of absolute paths and `s3://` URIs, because the host cache
  can hold `metadata.json` and have evicted `octree.bin`.

### Frappe

- `webodm_core.api.pointcloud.profile(task_name, line, width, step,
  ground_window)` — `check_permission("read")` on the task, input
  validation (2–50 finite vertices, width 0.05–200 m, step ≥ 0.01 m), then
  `processing.potree.file_sources(task)`: requires a Ready octree for the
  task's *current* point cloud, proves each octree File is attached to this
  task (`file_doc_for_url`, the same guard the raster paths use) and
  resolves it cache-first / S3-second (`cache.resolve_source`, which also
  queues a background refill). The request is forwarded by
  `plugins.geospatial.pointcloud_profile`. The browser never reaches the
  geospatial service.

### Frontend

- `lib/potree.js` — `profile` is a fourth measurement kind: drawn like a
  distance, horizontally measured, minimum two vertices.
- `composables/useModelViewer.js` — finishing a `profile` hands the picked
  vertices to the page (`onProfile`) instead of keeping a measurement, and
  switches the tool off; `setProfile`, `setProfileMarker`, `focusOn`.
- `composables/pointCloudLayer.js` — draws the transect, the dashed
  corridor outline per segment, the sampled ground and top polylines from
  the result (so chart and scene show the same data) and the hover marker.
- `lib/profile.js` — the API call, scales (fit or 1 : 1), SVG paths that
  break at gaps, hover lookup, stat rows, CSV, remembered settings.
- `components/ProfileChart.vue` (SVG, no chart library) and
  `components/ProfilePanel.vue` (controls, legend, stats, states, CSV);
  `pages/ModelView.vue` owns the profile state and the bottom dock.

## Sampling and summarising

All definitions live in `services/geospatial/app/utils/profile.py`.

**Corridor.** The transect is a polyline of up to 50 vertices in the
cloud's CRS (zero-length segments are dropped). Each segment owns an
oriented rectangle of the chosen width. A point belongs to the corridor
when it lies within a segment's rectangle; near an interior vertex, where
two rectangles overlap, it is assigned to the segment it is closest to, so
it is counted once. Its distance along the line is the segment's start
distance plus its projection on the segment.

**Node selection.** A node is visited when its XY box intersects any
segment rectangle (separating-axis test; the node's Z extent is irrelevant,
the corridor is a vertical slab). Nodes with no points are skipped.

**Budget.** The number of points under the corridor is known from the
hierarchy before any point is read. If it exceeds `PROFILE_MAX_POINTS`
(default 5 M; ~30 bytes of RAM per point while processing), whole octree
levels are dropped, deepest first, until it fits — a coarser but uniform
sample along the entire line, never a truncated line. The response reports
`sampling.full_resolution`, the spacing actually sampled and the points
read/available; the UI shows a note. The bin step is never finer than the
sampled spacing, so a coarse sample does not appear as a row of false gaps.

**Bins.** Step = requested, else a 1/2/5 "nice" number giving ~500 bins;
raised to the sampled spacing and widened to respect `PROFILE_MAX_BINS`
(default 2000). Statistics per bin are computed with one sort
(`group_stats`: count, min, max, mean, nearest-rank percentiles), so a few
million points take about a second.

**Classification.** The cloud is "classified" when any point carries a
code other than 0/1 (never classified / unclassified). Codes 7 and 18 are
noise and are excluded from everything. Code 2 is ground. Every other code
present becomes a layer with per-bin count, min, max, median and height
above ground (its max minus the ground). Codes 0/1 are not layers: they are
the undifferentiated pool that the ground/top lines summarise.

**Ground.**
- Classified cloud with class-2 points in the corridor: the **median** of
  the class-2 points in the bin (a surface, so the median is right; the
  minimum would bias low). Bins without ground points — under a roof, a
  dense canopy — have no measured ground.
- Otherwise: the **5th percentile** of each bin is a robust low envelope
  (photogrammetric clouds have no returns below the surface but do have a
  few stray low points). On its own the envelope sits on roofs and canopies,
  so a one-dimensional **progressive morphological filter** (Zhang et al.
  2003) is run along the line: openings with windows growing 3, 5, 9, 17…
  bins up to `ground_window` (default 20 m) flag bins where the opening
  lowers the surface by more than a 30 % terrain slope could over the window
  growth (plus 0.3 m of noise); flagged runs are then grown outwards while
  the step down to the next bin is steeper than terrain can be, so object
  fringes (eaves, canopy edges) are caught too. Flagged bins have no
  measured ground. Terrain features wider than the window or steeper than
  30 % are kept — a terrace wall, for instance, is reported as terrain; the
  usual trade-off of this filter family. `ground_window=0` disables it.
- Bins with no measured ground are **interpolated** linearly between the
  nearest measured bins on both sides when the run is at most
  `ground_fill_max` (50 m) long, and returned separately as `ground_fill`
  so the chart can draw them dashed and the CSV can label them. The height
  above ground uses measured ground, else the fill, else nothing.

**Top** is the maximum non-noise elevation in the bin; **height** is top
minus ground (clamped at 0).

**Gaps.** A bin with no points has `null` in every series and is listed in
`gaps` as a distance range.

**Statistics.** Horizontal length and ground-following length; ground
start/end and min/max with their distances; the top's maximum and the
maximum and mean height above ground with their distances; **ascent and
descent** from a 3-bin running median of the ground with a 0.25 m
hysteresis (a change counts once it has accumulated to 0.25 m, as GPS track
tools do; otherwise per-bin noise sums to metres of fictitious climbing),
never across a gap; **steepest grade** over a 2 m window that contains no
gap, with its distance and sign, and the mean grade from first to last
ground sample; coverage (bins with data / bins) and points used.

## API

### `POST /pointcloud/profile` (geospatial)

```json
{
  "metadata": "/abs/.../t1_potree_metadata.json",
  "hierarchy": "/abs/.../t1_potree_hierarchy.bin",
  "octree": "s3://bucket/orgs/org/tasks/t1/assets/potree/octree.bin",
  "line": [[663490.0, 5328070.0], [663610.0, 5328190.0]],
  "width": 1.0,
  "step": null,
  "ground_fill_max": null,
  "ground_window": null
}
```

Response (arrays are parallel to `distance`, `null` = no data):

| Key | Meaning |
|---|---|
| `crs`, `line`, `vertices`, `width`, `step`, `length`, `bins` | Echo of the transect (zero-length segments dropped), cumulative distance of each vertex, bin size used |
| `distance`, `x`, `y` | Bin centres: distance along the line and position in the CRS (for the 3D marker) |
| `count`, `min`, `max` | Points per bin (non-noise), lowest and highest elevation |
| `ground`, `ground_fill`, `top`, `height` | Measured ground, interpolated ground, top, top − ground |
| `classes[]` | Per code: `code`, `points`, `bins`, `count`, `min`, `max`, `median`, `height`, `max_height`, `max_height_at` |
| `gaps` | `[start, end]` distances of empty runs |
| `classification` | `available`, `ground_method` (`class2_median` \| `low_percentile_filtered` \| `low_percentile`), `noise_excluded`, `ground_fill_max`, `ground_window`, `climb_threshold` |
| `sampling` | `points_read`, `points_available`, `points_in_corridor`, `nodes_read`, `nodes_available`, `bytes_read`, `requests`, `full_resolution`, `max_level`, `deepest_level`, `spacing`, `step_requested`, `point_budget` |
| `stats` | `length`, `length_3d`, `segments`, `bins`, `bins_with_data`, `coverage`, `points`, `ground_start/end/min/max(+_at)`, `top_max(+_at)`, `height_max(+_at)`, `height_mean`, `ascent`, `descent`, `max_grade_pct(+_at)`, `mean_grade_pct` |
| `duration_s` | Server time |

Errors: 400 (relative path / bucket not allowed), 404 (local file
missing), 422 (bad transect or parameters, unreadable octree — message
included), 502 (object storage), 507 (out of memory: narrow or shorten).
`GET /pointcloud/profile/limits` returns `max_points`, `max_bins`,
`max_vertices`, `width` range and `min_step`.

### `webodm_core.api.pointcloud.profile` (Frappe, POST, session)

`task_name`, `line` (JSON string or list of `[x, y]`, in the octree's
recorded projection — what the viewer picks), `width` (default 1),
`step` (omit / 0 = auto), `ground_window` (optional). Returns the
geospatial document. Throws a `ValidationError` with the reason when the
octree is not Ready ("open it in the 3D viewer first"), when the service
refuses the request, and a plain error when the service is unreachable.
Permission: read on the task; a member of another organization gets the
usual `PermissionError`, exactly like the octree files themselves.

## Decisions

**Read the octree, not the LAZ, and do it on the server.** See *The problem
and the options*. One artefact, already permissioned, cached and stored in
S3 like every other output; the profile costs nothing for clouds nobody
opens, and the reader needs only NumPy.

**Own Potree 2.0 reader rather than a point cloud library.** PDAL has no
Potree reader; the format is three files with a documented layout that
potree-core (the viewer's loader) implements in a few hundred lines. The
reader mirrors that code (22-byte records, proxy chunks, child-box bit
order, interleaved attributes) and is validated against both a synthetic
writer and real PotreeConverter output (every one of 4.86 M points read
once; corridor counts and per-bin maxima agree with a laspy scan of the LAZ
to the 1 mm quantisation). Brotli-encoded octrees are not supported — the
platform writes `UNCOMPRESSED` by decision of the viewer change.

**Level trimming for the budget, not spatial truncation.** Dropping the
deepest levels degrades uniformly along the line and is detectable (and
reported) before any byte is read; cutting the line short or reading a
random subset would not be. The budget is a memory bound, not a time bound:
reading from a warm host cache runs at ~0.3 s per 100 k corridor points.

**Ground definitions with a stated fallback.** A classified cloud uses
its ground class, full stop. An unclassified cloud — ODM's default output —
gets a filtered low envelope, labelled as such in the UI with the hint to
turn `pc-classify` on. The filter was added after a real survey showed the
raw low envelope climbing onto an 11 m roof (reported as a 546 % grade);
with it the same transect reads the terrace wall at the building's foot
(a real −88 % step) and nothing else.

**Interpolated ground is returned separately.** `ground` is measurement,
`ground_fill` is inference; the chart dashes it, the CSV labels it, and
height above ground uses either so a building's height can still be read.
Gaps longer than 50 m stay gaps.

**Hysteresis for ascent/descent, a 2 m window for grade.** Per-bin deltas
on a 0.5 m step over photogrammetric noise summed to 24 m of climbing on a
170 m transect with ~14 m of relief. With a 0.25 m hysteresis the same line
reports 4 m up / 5 m down. Grade windows never span a gap.

**Points are assigned to one segment of a polyline.** Near a corner the
corridors of two segments overlap; the nearest-segment rule counts each
point once and keeps the per-bin counts honest.

**SVG chart, no charting dependency.** A profile is a few hundred bins; a
hand-built SVG with explicit gap handling, class bands and a 1 : 1 mode was
smaller and more controllable than a library that would then need to be
taught about gaps and dashed segments. The frontend gained no dependency.

**One profile at a time; the tool releases itself.** Finishing a line
switches the tool off so the user can orbit and read the chart; a new line
replaces the previous profile. Keeping several profiles was not asked for
and would crowd the scene.

**Chart → scene linkage, not (yet) scene → chart.** Hovering the chart
marks the spot in 3D and clicking it moves the camera there. The reverse
(hovering the scene highlights the chart) would need a GPU pick per
pointer-move; it is a follow-up.

## Operations

| Variable (geospatial) | Default | Meaning |
|---|---|---|
| `PROFILE_MAX_POINTS` | `5000000` | Points read per request; deeper octree levels are dropped beyond it. Memory: ~30 bytes per point while processing (~150 MB at the default). |
| `PROFILE_MAX_BINS` | `2000` | Bins per response; the step is widened to respect it. |

Both are exposed in `docker-compose.yml` (`.env` overrides) and the Helm
chart (`geospatial.profile.maxPoints` / `maxBins`). The Frappe proxy times
the geospatial call out after 180 s.

With object storage, the geospatial identity needs `GetObject` on
`assets/potree/*` — already granted by `infra/aws/iam/storage-geospatial-policy.json`
for the conversion step, so no IAM change is required.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "the point cloud has not been prepared for viewing yet" | No Ready octree for the task's current point cloud. Open the cloud in the viewer (conversion on first open), then draw the profile. |
| "the octree is not available on this host" | Host copy evicted and no object storage copy (legacy host-only deployment). Retry in the viewer rebuilds the octree. |
| Note "Sampled at … spacing" under the chart | The corridor held more than `PROFILE_MAX_POINTS`; shorten the line or narrow the corridor, or raise the budget (and the service memory). |
| Profile is all gaps | The line is outside the data. Check `stats.points`; the chart shows hatched columns everywhere. |
| Steep "ground" steps on an unclassified cloud | Objects wider than `ground_window` (20 m) or real terrain steps. Widen the window via the API, or enable `pc-classify` on the task. |
| 507 from the service | A request wider than memory allows; lower `PROFILE_MAX_POINTS` or raise the service memory. |

## Testing

- Geospatial: `tests/test_profile.py` (31 tests) — `tests/potree_fixture.py`
  is a small Potree 2.0 *writer* (multi-level LOD, proxy chunks, interleaved
  records) that builds a synthetic survey with a known slope, a building
  without ground beneath it, a tree row, a wire, noise and a hole, so the
  profile's numbers are asserted exactly: reader completeness and pruning,
  slope and climb, building as a layer with interpolated ground, trees and
  wire as separate layers with noise excluded, gaps, the unclassified
  fallback with and without the morphological filter, polyline distances and
  single counting at a corner, level trimming under a budget, step/bins
  caps, validation, router status codes, and object storage with moto
  (mixed local/S3 sources). One test converts a LAS with the real
  PotreeConverter and checks the reader against it; it skips without the
  binary and runs in CI inside the geospatial image.
- Frappe: `webodm_core.api.test_profile_api` (23 tests with `test_tiles`) —
  validation, permission-before-anything, octree resolution (Ready state,
  ownership check, cache-first / S3-second, evicted without storage), the
  geospatial client's error mapping.
- Frontend: `lib/profile.test.js`, `components/ProfileChart.test.js`,
  `components/ProfilePanel.test.js` plus the profile kind in
  `lib/potree.test.js`. Browser: `e2e/point-cloud.e2e.mjs` gained a profile
  section (tool → line → dock, request contents in the cloud CRS, chart
  elements, hover readout, width re-request, remembered settings, backend
  refusal → retry, close).
- Real data: a 4.86 M-point ODM survey (`j1u49j8ore`, unclassified, 72 MB
  octree): a 170 m diagonal transect reads 700 k points (39 MB) in 0.25 s
  from the host cache; corridor point count matches a laspy scan of the LAZ
  (75 261 vs 75 313, the difference being 1 mm quantisation at the corridor
  edge); per-bin tops agree within 7 cm; gap bins agree exactly.

## Limits and follow-ups

- Requires the viewer's octree (built on first open). An explicit "prepare"
  from the map page could remove that step.
- Scene → chart hover, saving profiles with the task, comparing two epochs
  of the same site, overlaying the DTM/DSM as reference lines, and a PDF/PNG
  export of the chart are natural next steps.
- Unclassified clouds get a heuristic ground; running `pc-classify` (or an
  external classifier) is the real answer and the UI says so.
- Very wide corridors on very dense clouds hit the point budget and are
  reported as sampled; the budget is a deployment setting.
