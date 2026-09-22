# Semantic Segmentation Plugin Specification

## Purpose

A user plugin (`plugins/semantic-segmentation`) that segments a task's
orthophoto, DSM and/or DTM with configurable models and returns a class mask
(or class polygons) through the existing plugin execution and output
mechanisms. Complements `user-plugins` (packaging, sandbox) and
`plugin-outputs` (storage, tiles, download), which apply unchanged.

## Requirements

### Requirement: Dataset selection

The plugin SHALL declare orthophoto, DSM and DTM as optional inputs so the
user can select any non-empty subset of the task's datasets for a run.

#### Scenario: Single dataset

- **WHEN** a user selects only the DSM (or only the DTM, or only the orthophoto)
- **THEN** the run executes with a model compatible with that dataset

#### Scenario: All three datasets

- **WHEN** a user selects orthophoto, DSM and DTM
- **THEN** the run uses the orthophoto model with height above ground derived
  from DSM minus DTM

### Requirement: Configurable models

Models SHALL be described by model cards (id, backend, required/optional
inputs, classes, recommended parameters) so a model can be added or replaced
without changing plugin code. `auto` SHALL choose the highest-priority model
compatible with the selected inputs; an explicit incompatible choice MUST be
refused with the inputs it needs.

#### Scenario: Automatic choice

- **WHEN** `model` is `auto`
- **THEN** an orthophoto yields the aerial land-cover model, a DSM without
  orthophoto yields height classes, a DTM alone yields geomorphon landforms

#### Scenario: Incompatible explicit model

- **WHEN** the user requests the RGB model with only a DSM selected
- **THEN** the run fails before inference with a message naming the required inputs

#### Scenario: Missing model weights

- **WHEN** an ONNX card's weight file is not in the package
- **THEN** the card is skipped with a warning and `auto` falls back to the next model

### Requirement: Alignment of heterogeneous inputs

Inputs with different resolutions, extents or coordinate reference systems
SHALL be resampled onto one processing grid (CRS of the finest input, extent
of the intersection of the model's primary inputs) before inference.

#### Scenario: Different CRS

- **WHEN** the DTM is in EPSG:4326 and the DSM in a UTM zone
- **THEN** heights are computed pixel-for-pixel on the UTM grid

#### Scenario: No overlap

- **WHEN** the selected inputs do not overlap
- **THEN** the run fails with a message saying so

### Requirement: Segmentation mask as primary result

The plugin SHALL write a single-band uint8 GeoTIFF of class ids with nodata
255, an embedded colour table and the class table in its tags; a polygons
variant of the package SHALL write a GeoJSON FeatureCollection (EPSG:4326)
with `class`, `class_id` and `area` per feature.

#### Scenario: Mask rendered on the map

- **WHEN** a completed mask run is shown
- **THEN** tiles are coloured with the mask's own palette

### Requirement: Status and errors

The plugin SHALL log every stage and tile progress to stderr, record stages,
warnings, inputs used/ignored, grid and per-class statistics in the run
metadata, and fail with a single clear line for expected problems (missing or
unreadable inputs, incompatible model, invalid parameter, unavailable model).

#### Scenario: Unreadable input

- **WHEN** a selected raster cannot be opened
- **THEN** the run is Failed with an error naming the dataset and no output is written
