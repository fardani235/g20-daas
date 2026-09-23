# 3D Reconstruction Plugin Specification

## Purpose

A user plugin that converts a completed task's ODM outputs (orthophoto, DSM,
DTM, LAZ point cloud, textured mesh) into one web-ready, georeferenced GLB for
the platform's 3D viewer, choosing the workflow from the inputs available and
sizing the result for a browser.

## Requirements

### Requirement: Input detection and workflow selection

The plugin SHALL accept any subset of the inputs `orthophoto`, `dsm`, `dtm`,
`point_cloud` and `model` (all optional) and, with `workflow: auto`, SHALL
choose `mesh` when a model is present, else `terrain` when a DSM or DTM is
present, else `point-cloud` when a point cloud is present; it SHALL fail with an
actionable message when none of those is available or when an explicitly
requested workflow lacks its required input.

#### Scenario: Auto with a full task

- **WHEN** a task with orthophoto, DSM, DTM, point cloud and model is run with `workflow: auto`
- **THEN** the `mesh` workflow optimises the existing model

#### Scenario: Auto without a model

- **WHEN** a task with DSM and orthophoto but no model is run with `workflow: auto`
- **THEN** the `terrain` workflow builds a textured heightfield mesh

#### Scenario: Orthophoto only

- **WHEN** only the orthophoto is selected
- **THEN** the run fails explaining that an elevation source or a model is required

### Requirement: Multi-source combination

Where more than one source is available the plugin SHALL combine them:
elevation holes are filled first from the other elevation raster (DTM under a
DSM or vice versa), then from the point cloud when gaps remain, then by
interpolating enclosed gaps of at most 100 m²; larger holes stay open. Texture
comes from the orthophoto, then point-cloud RGB, then shaded relief, each
filling where the previous has no data.

#### Scenario: DSM hole under a DTM

- **WHEN** the DSM has an enclosed nodata region and a DTM is selected
- **THEN** those cells take the DTM's elevation and the metadata counts them under `holes.from_donors`

#### Scenario: Outside the flight area

- **WHEN** the DSM's bounding box contains a large nodata region (outside the survey)
- **THEN** the mesh has no triangles there (`holes.left_open` > 0) rather than an invented surface

#### Scenario: No orthophoto

- **WHEN** a point cloud with RGB is the only colour source
- **THEN** the texture is built from the points' colours, with shaded relief where no point falls

### Requirement: Geospatial consistency

Output coordinates SHALL be in the task's projected CRS, Z-up, stored relative
to an origin recorded as the `CESIUM_RTC` centre, and the file SHALL carry
`asset.extras.webodm_georef` with EPSG, WKT, origin, absolute bounds and Z
range so absolute coordinates are `vertex + origin`. Geographic (lat/lon)
inputs SHALL be refused.

#### Scenario: Georeferenced output

- **WHEN** any workflow completes on a task in EPSG:32632
- **THEN** the GLB's extras declare `epsg: 32632`, an origin within the survey, bounds matching the surface, and the run records a map extent

#### Scenario: Model without origin

- **WHEN** the `mesh` workflow reads an OBJ archive or a GLB without `CESIUM_RTC` and with small coordinates
- **THEN** the output is produced in its local frame, marked `georeferenced: false`, with no map extent and a warning

### Requirement: Web-ready output

The plugin SHALL respect a quality profile (`web-lite`, `balanced`,
`high-detail`, or `auto` derived from the task's ODM options) that bounds
triangles, texture size, point count and total atlas pixels; SHALL emit unlit
materials, JPEG textures and, by default, Draco-compressed geometry decodable
by the platform viewer; and SHALL complete within the sandbox limits (2 GB
address space, single thread) for surveys of at least 10 M points / 170 MP
orthophoto at `high-detail`.

#### Scenario: Triangle budget

- **WHEN** the surface yields more triangles than the profile allows
- **THEN** quadric decimation reduces it to the budget and texture coordinates are recomputed from X/Y

#### Scenario: Atlas budget

- **WHEN** the `mesh` workflow reads a model with 50 MP of texture atlases and `quality: web-lite`
- **THEN** every atlas is shrunk uniformly until the total is within 16 MP and the geometry is unchanged

#### Scenario: Draco layout

- **WHEN** geometry is Draco-compressed
- **THEN** the glTF `attributes` map is derived from the encoded blob and matches what the viewer's Draco decoder reports

### Requirement: Job status, progress and errors

The plugin SHALL publish stage-level progress (`percent`, `message`) to the
runner's progress file, log every stage with its duration and peak memory to
stderr, report expected problems (missing inputs, invalid parameters, wrong
CRS) as one clear line with exit status 1, and include workflow, inputs used,
resolved parameters, mesh/texture/hole statistics and stage timings in the run
metadata.

#### Scenario: Progress visible

- **WHEN** a run is decimating
- **THEN** the run row shows `Running` with a percentage and the stage name

#### Scenario: User error

- **WHEN** `quality` is set to an unknown value
- **THEN** the run fails with `error: quality must be one of ...` and no traceback
