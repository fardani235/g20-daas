# Raster Metadata Specification

## Purpose

Automatically extracts, normalizes, stores and exposes the header metadata of
the rasters a task produces (orthophoto, DSM, DTM) so the viewer, the tiler
and analysis plugins can reason about a raster without opening it.

## Requirements

### Requirement: Header-only extraction

The geospatial service SHALL read raster metadata from file headers only and
SHALL NOT load pixel data or compute statistics to do so, so extraction cost
does not grow with raster size.

#### Scenario: Large orthophoto

- **WHEN** metadata is requested for a multi-gigabyte GeoTIFF
- **THEN** the response arrives without reading pixel data and within a bounded
  time independent of the raster's area

#### Scenario: Extracted facts

- **WHEN** metadata is requested for a readable raster
- **THEN** the response includes width, height, band count, data type(s), CRS
  (EPSG code and/or WKT, units), pixel size, native bounds, geotransform,
  EPSG:4326 bounds and extent when fully georeferenced, nodata (or its
  absence), tiling and block size, compression, overview levels, COG validity,
  color interpretation per band, file size and driver

### Requirement: Graceful handling of imperfect rasters

Extraction SHALL distinguish georeferencing problems (which yield metadata with
a `georeference` status of `full`, `no_crs`, `no_transform` or `none`) from
unreadable files (missing, corrupt, not a raster), and a failure SHALL never
abort the processing pipeline.

#### Scenario: Raster without a CRS

- **WHEN** a raster has a geotransform but no CRS
- **THEN** metadata is returned with native bounds and pixel size, no EPSG:4326
  bounds, and `georeference: no_crs`

#### Scenario: Corrupt raster

- **WHEN** the file cannot be opened as a raster
- **THEN** the service answers with an error, the task keeps its outputs and
  status, and a metadata row with `status: Failed` and the error is stored

#### Scenario: Geospatial service unavailable

- **WHEN** the service cannot be reached while outputs land
- **THEN** the task still completes, the failure is logged and recorded on the
  metadata row, and a later request or refresh can extract it

### Requirement: Automatic capture and normalized storage

Metadata SHALL be captured automatically when an orthophoto, DSM or DTM becomes
available and stored as one normalized row per raster on the task
(`WebODM Raster Metadata`), replacing any previous row for that raster. Only
compact, typed values are stored; raw tag dumps are not.

#### Scenario: Outputs downloaded

- **WHEN** the processing pipeline stores a task's orthophoto
- **THEN** its metadata row exists on the task before the task is marked
  Completed, and the task's resolution is filled from the orthophoto's ground
  sampling distance when the user did not set one

#### Scenario: Stale row

- **WHEN** a stored row describes a different file than the task currently holds
- **THEN** it is re-extracted on the next metadata request

### Requirement: Exposure

Metadata SHALL be readable through the existing task API (as part of the task
document and via `webodm_core.api.task.get_raster_metadata`) and SHALL be
passed to user plugins as `context.task.rasters`, subject to the task's
permissions.

#### Scenario: Unauthorized read

- **WHEN** a user outside the task's organization requests its metadata
- **THEN** the request is rejected
