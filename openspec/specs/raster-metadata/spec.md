# Raster Metadata Specification

## Purpose

Gives every consumer of a task raster — the orthophoto viewer, tile layer
setup, segmentation / detection / GeoAI operations and raster analysis
plugins — the facts about the file (size, bands, data type, CRS, pixel size,
bounds, georeferencing, nodata, tiling, compression, overviews) without
opening it, by extracting them once when the raster becomes available and
storing them normalized on the task.

## Requirements

### Requirement: Header-only extraction

The geospatial service SHALL read raster metadata from the dataset header
only (`GET /raster/metadata?path=`), never loading pixel data, so cost is
independent of raster size. It SHALL report at least: driver, file size,
width, height, band count, data type, EPSG (or CRS WKT when no code exists),
CRS units, whether the raster is georeferenced, GDAL-order geotransform, pixel
size, native bounds, EPSG:4326 bounds/extent, nodata, block width/height,
tiled flag, compression, overview factors, COG validity and per-band colour
interpretation. Keys that do not apply MUST be present with a null value.

#### Scenario: Very large orthophoto

- **WHEN** metadata is requested for a 40 000 × 40 000 × 3 GeoTIFF
- **THEN** the response arrives in well under a few seconds and memory use is
  bounded by the header, not the pixel data

#### Scenario: Raster without a CRS

- **WHEN** the raster has a geotransform but no CRS
- **THEN** native bounds, pixel size and geotransform are reported,
  `is_georeferenced` is false and `epsg`, `crs_wkt`, `bounds_4326` are null

#### Scenario: Raster without nodata / with NaN nodata

- **WHEN** the raster declares no nodata value
- **THEN** `nodata` is null
- **WHEN** the raster's nodata is NaN
- **THEN** `nodata` is the string `"nan"` so the payload stays valid JSON

#### Scenario: Corrupt or unsupported file

- **WHEN** the path exists but is not a readable raster
- **THEN** the service answers 422 with the reader's reason and does not crash

### Requirement: Automatic extraction in the processing pipeline

When a task's orthophoto, DSM or DTM is downloaded from the processing node,
the platform SHALL extract its metadata (using the dict the COG conversion
already returns, or asking the service directly when that is unavailable) and
persist it. A metadata failure MUST NOT fail or delay the task; it SHALL be
logged and recorded so the failure reason is visible.

#### Scenario: Geospatial service down during download

- **WHEN** the service is unreachable while assets are being registered
- **THEN** the task still completes with its files attached, and each raster
  carries a metadata row whose `error` explains the failure

### Requirement: Normalized storage on the task

Metadata SHALL be stored as one `WebODM Raster Metadata` child row per dataset
on `WebODM Task`, with scalar, queryable columns (arrays split into fields;
nodata as text so absence and NaN are representable; file size as a 64-bit
integer). Re-extraction SHALL replace the existing row for that dataset. No
raw GDAL dump or tag blob SHALL be stored.

#### Scenario: Re-extraction

- **WHEN** metadata is extracted again for a dataset that already has a row
- **THEN** the task ends up with exactly one row for that dataset

### Requirement: Exposed through the backend API

`webodm_core.api.raster.get_metadata(task_name, dataset, refresh)` and
`list_metadata(task_name)` SHALL return the metadata with arrays reassembled
(`geotransform`, `pixel_size`, `bounds`, `bounds_4326`, `block_size`,
`overviews`, `color_interp`) and unknowns as null (EPSG 0 → null). Access
SHALL require read permission on the task; refresh SHALL require write. A
task processed before this feature existed SHALL have its metadata extracted
on first request. The task document (`raster_metadata` table) and the user
plugin request context (`context.task.raster_metadata`) SHALL carry the same
data.

#### Scenario: Member of another organization

- **WHEN** a user outside the task's organization requests its metadata
- **THEN** the request is denied

#### Scenario: Legacy task

- **WHEN** metadata is requested for a completed task that has none stored
- **THEN** it is extracted, stored and returned; a second request is served
  from storage
