# Plugin Outputs Specification

## Purpose

Stores, serves, and displays the artifacts produced by analysis plugin runs.

## Requirements

### Requirement: Output persistence

A completed run's output artifacts SHALL be stored as files associated with the
run and its task, with raster outputs retaining georeferencing metadata.

#### Scenario: Raster output stored

- **WHEN** an operation succeeds and produces a raster
- **THEN** the raster is stored and its extent, coordinate reference system, and
  render kind are recorded

#### Scenario: Vector output stored

- **WHEN** an operation succeeds and produces vector data
- **THEN** the vector data is stored in a web-renderable form with its extent
  recorded

#### Scenario: Failure discards partial output

- **WHEN** an operation fails after producing partial output
- **THEN** no output is attached for that run

### Requirement: Output access control

Output artifacts SHALL be readable only by members authorized for the owning
organization and its task.

#### Scenario: Authorized read

- **WHEN** a member requests output for a run in their organization
- **THEN** the artifact or its tiles are returned

#### Scenario: Unauthorized read

- **WHEN** a member requests output for a run in another organization
- **THEN** the request is rejected and no artifact is returned

### Requirement: Raster output rendering

Raster outputs SHALL be served as map tile layers through the same
permission-checked proxy used for task rasters.

#### Scenario: Tiles served

- **WHEN** a map requests tiles for a raster plugin output
- **THEN** permission-checked tiles are returned for the run's raster

#### Scenario: Metadata for map placement

- **WHEN** a completed raster output is displayed
- **THEN** the map can determine its bounds from the recorded georeferencing

### Requirement: Vector output rendering

Vector outputs SHALL be convertible to GeoJSON and displayed as map overlays when
a task or run is viewed.

#### Scenario: Vector overlay shown

- **WHEN** a completed vector output is viewed on the map
- **THEN** its geometries render as an overlay

#### Scenario: Toggle visibility

- **WHEN** a user toggles a vector overlay
- **THEN** the overlay is shown or hidden without affecting other layers

### Requirement: Downloadable outputs

Output artifacts SHALL be downloadable by authorized members.

#### Scenario: Download

- **WHEN** an authorized member downloads a completed run's output
- **THEN** the artifact is delivered as a file
