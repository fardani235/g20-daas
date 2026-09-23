# 3D Reconstruction Specification

## Purpose

Produces compact, georeferenced, web-ready 3D models (GLB) from a completed
task's outputs — DSM, DTM, orthophoto, LAS/LAZ point cloud and/or the existing
ODM textured model — as a user plugin, and lets the platform store, place and
display `model` outputs. Complements `user-plugins` (packaging, sandbox) and
`plugin-outputs` (storage, access control), which apply unchanged.

## Requirements

### Requirement: Model output kind

The plugin framework SHALL accept `output_kind: "model"` (render kind `glb`).
A model output MUST be a glTF 2.0 binary with at least one mesh; the runner
SHALL validate the container and read the producer's `extras.webodm_georef`
record (`epsg`, `origin`, `bounds`, `bounds_4326`) into the run's extent and
metadata, together with triangle/vertex/texture counts.

#### Scenario: Valid GLB with georeferencing

- **WHEN** a plugin writes a GLB whose root `extras.webodm_georef.bounds_4326`
  is set
- **THEN** the run completes with `output_extent` set to that footprint and
  `output_metadata.epsg` to the recorded CRS

#### Scenario: GLB without georeferencing

- **WHEN** a plugin writes a valid GLB without a georef record
- **THEN** the run completes, the model opens in the viewer, and no footprint is
  drawn on the map

#### Scenario: Not a GLB

- **WHEN** the output is not a glTF 2.0 binary or contains no meshes
- **THEN** the run fails with a message naming the problem

### Requirement: Task context for plugins

The sandbox request SHALL carry a `context` object with the task's name,
title, EPSG code and ODM processing options, plus the run and plugin ids.
Plugins MAY ignore it; it is never interpreted by the runner.

#### Scenario: Context forwarded

- **WHEN** a user plugin run is dispatched
- **THEN** `request.json["context"]["task"]["processing_options"]` equals the
  task's stored options and `context.run` is the run id

### Requirement: Workflow selection from available inputs

The 3D Reconstruction plugin SHALL detect which of its optional inputs were
supplied and choose a workflow: `optimize-model` when the existing ODM model is
present, otherwise `terrain` when a DSM, point cloud or DTM is present. The
user MAY force either workflow and MAY choose the surface and texture sources
explicitly; an impossible choice SHALL fail with a message naming the missing
input.

#### Scenario: DSM and orthophoto

- **WHEN** the run receives a DSM and an orthophoto
- **THEN** a textured terrain mesh is produced with heights from the DSM and the
  orthophoto as texture, clipped to the orthophoto's coverage

#### Scenario: Point cloud only

- **WHEN** the run receives only a coloured LAS/LAZ point cloud
- **THEN** the surface is binned from the cloud and textured with the cloud's
  colours, in the cloud's CRS (or the task's when the file carries none)

#### Scenario: DTM only

- **WHEN** the run receives only a DTM
- **THEN** a terrain mesh textured with a shaded relief is produced

#### Scenario: Existing model

- **WHEN** the run receives the ODM textured model (with or without other inputs)
- **THEN** the model is re-encoded with textures capped at the preset size and
  16-bit positions, its `CESIUM_RTC` centre preserved, and the CRS taken from an
  accompanying raster or the task context

#### Scenario: Nothing usable

- **WHEN** every input is absent
- **THEN** the run fails: "no usable input: select a DSM, DTM, point cloud or
  existing 3D model"

### Requirement: Source combination

When several sources are present the plugin SHALL use them together: DSM
no-data cells take DTM (or point-cloud) heights, interior holes are
interpolated (unless disabled), the mesh is clipped to the texture's coverage
(unless disabled), and the number of texture tiles follows the orthophoto's
resolution within the preset's limit.

#### Scenario: DSM hole filled from DTM

- **WHEN** the DSM has a no-data hole and a DTM is supplied
- **THEN** the hole takes the DTM height and the run metadata reports how many
  nodes were filled from the DTM

### Requirement: Geospatial consistency

Output meshes SHALL be in a local metric frame (X east, Y north, Z up) in a
projected CRS relative to a recorded origin; the origin SHALL be written to
`CESIUM_RTC.center` and, with the CRS and bounds, to `extras.webodm_georef`.
Geographic inputs SHALL be projected to the UTM zone of their centroid.

#### Scenario: Vertex positions match the DSM

- **WHEN** a terrain model is built from a DSM
- **THEN** every vertex, offset by the recorded origin, lies on a heightfield
  node with the DSM's height at that location

### Requirement: Web-ready size

Models SHALL respect the quality preset's triangle budget and texture size,
using error-driven (RTIN) simplification, 16-bit quantized positions and
normalized UVs, unlit materials (no normals) and JPEG textures, so that the
built-in viewer can display them without special decoders.

#### Scenario: Budget honoured

- **WHEN** the `web-light` preset is selected
- **THEN** the mesh has at most 150 000 triangles and one 2048 px texture

### Requirement: Viewer and map integration

A completed model run SHALL be openable in the 3D viewer from the run row and
from its footprint on the map; the viewer SHALL let the user switch between the
ODM model and completed reconstructions and SHALL show progress/failure states
for a run that is still running or failed.

#### Scenario: Open a reconstruction

- **WHEN** a member clicks *Open 3D* on a completed model run
- **THEN** the viewer opens with `?run=<run id>` and displays that run's GLB

#### Scenario: Run still running

- **WHEN** the viewer is opened for a Queued or Running model run
- **THEN** it shows an in-progress state and loads the model automatically when
  the run completes
