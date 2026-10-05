## Context

See `proposal.md`. The full design record — the options weighed, the
architecture, every sampling and summary definition, the API contracts,
decisions with their evidence, operations, testing and follow-ups — is
`docs/point-cloud/elevation-profile.md`; this file is the short form.

Current state that shaped the approach: every cloud shown in the viewer has
a Potree 2.0 octree (uncompressed, three files) kept as a task asset on the
host and in object storage; the geospatial service is stateless and reads
local paths or `s3://` URIs; Frappe proxies every geospatial call with the
task permission check; the viewer picks points in the cloud's native CRS.

## Goals / Non-Goals

**Goals:** a profile of the *whole* cloud along a user-drawn line, with
ground / top / height and per-class layers, gaps shown as gaps, usable on
very large clouds and on clouds in object storage, permission-checked,
linked to the 3D view, with useful numbers.

**Non-Goals:** raster-based profiles, multiple or persisted profiles,
scene → chart hover, Brotli octrees, re-classifying the cloud.

## Decisions

### D1. Compute on the server from the Potree octree

Rejected: browser-side sampling of loaded nodes (reflects the view, not the
cloud), DSM/DTM rasters (2.5D, no classes), scanning the LAZ (no spatial
index; full download from S3). The octree is a spatial index of the full
cloud with every point stored once; a corridor touches few nodes, read by
byte range. Cost scales with the line, not the cloud. Trade-off: the cloud
must have been opened once (the API says so).

### D2. Own Potree 2.0 reader, NumPy only

Mirrors potree-core's loader (22-byte hierarchy records, proxy chunks,
child-box bit order, interleaved attributes). Validated against a synthetic
writer and real PotreeConverter output (all 4.86 M points read once;
corridor counts match a laspy scan to the 1 mm quantisation).

### D3. Budget by dropping whole levels, deepest first

Known from the hierarchy before reading; degrades uniformly along the line
and is reported (`sampling.full_resolution`, spacing). The bin step is never
finer than the sampled spacing.

### D4. Ground: class-2 median, else filtered low envelope

Classified cloud → median of class-2 points per bin. Otherwise the 5th
percentile with a 1-D progressive morphological filter (windows up to 20 m,
30 % slope, 0.3 m noise, fringe growth) so roofs and canopies are not
"ground". Interpolated ground under objects / small holes is returned as
`ground_fill`, drawn dashed. Noise classes 7/18 excluded everywhere.

### D5. Robust summary numbers

Ascent / descent with a 0.25 m hysteresis over a 3-bin running median;
steepest grade over 2 m windows that contain no gap; heights from top minus
(measured or interpolated) ground; coverage reported.

### D6. UI: fourth measurement kind, SVG chart, chart → scene linkage

The tool draws like a distance and hands the vertices to the page; the dock
is an SVG chart with explicit gap handling and a 1 : 1 mode; hovering the
chart marks the spot in 3D, clicking focuses the camera; one profile at a
time; settings remembered.

## Risks / Trade-offs

- Unclassified clouds (ODM default) get a heuristic ground; the UI labels it
  and points at `pc-classify`.
- Terrain steeper than 30 % or features wider than 20 m are kept as ground
  by the filter — the usual trade-off; `ground_window` is an API parameter.
- Dense clouds with wide corridors hit the point budget; reported, and the
  budget is a deployment setting (~30 bytes of RAM per point).
