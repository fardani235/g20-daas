## Purpose

Defines how an ONNX semantic-segmentation model is run over a task's orthophoto
and how its classified regions are parameterized, produced, and presented to
users.

## ADDED Requirements

### Requirement: Segmentation operation availability

The system SHALL expose semantic segmentation as a catalog operation that consumes
a task's orthophoto and produces classified regions, discoverable and runnable
through the existing plugin catalog and enablement flow.

#### Scenario: Operation is listed

- **WHEN** a client requests the analysis catalog
- **THEN** semantic segmentation appears with its identifier, description,
  parameter schema, and required `orthophoto` input

#### Scenario: Operation unavailable

- **WHEN** semantic segmentation is not enabled for the caller's organization, or
  is disabled platform-wide
- **THEN** a run request is rejected as it is for any other plugin

### Requirement: Model configuration

The segmentation operation SHALL use an ONNX model file and a matching label set
supplied by configuration, with a platform default that an organization may
override, and MUST reject a run when the model or labels are missing, unreadable,
or inconsistent. Model and label references MUST resolve inside a
platform-managed models directory; absolute paths, parent-directory traversals,
and symlink escapes MUST be rejected.

#### Scenario: Default model used

- **WHEN** no model is configured for the organization
- **THEN** the platform default model and labels are used

#### Scenario: Organization override used

- **WHEN** the organization has configured a model and labels
- **THEN** that model and its labels are used for that organization's runs

#### Scenario: Model outside the managed directory rejected

- **WHEN** a run or setting references a model by an absolute path, a
  parent-directory traversal, or a symlink leaving the managed directory
- **THEN** the request is rejected and no run is created

#### Scenario: Missing model rejected

- **WHEN** the configured model or label file cannot be read
- **THEN** the run is rejected before it is created and the reason is reported

### Requirement: Segmentation model contract

The operation SHALL accept models that expose a single image input and a per-class
mask output, and MUST reject a model whose input/output shape or class count is
incompatible with its label set before a run is created.

#### Scenario: Compatible model accepted

- **WHEN** a model exposes one image input and a mask output whose class count
  matches the label file
- **THEN** the model is accepted and the run may proceed

#### Scenario: Unsupported model rejected

- **WHEN** a model exposes neither a supported mask output nor a compatible input
- **THEN** the run is rejected before it is created with a clear reason

#### Scenario: Label count mismatch rejected

- **WHEN** the number of labels does not match the model's class count
- **THEN** the run is rejected before it is created with a clear reason

### Requirement: Segmentation parameters

The operation SHALL accept a mask threshold, a tile size, a tile overlap, a
minimum segment area, a simplification tolerance, and an optional class filter.
Omitted parameters MUST fall back to documented defaults and supplied values MUST
be validated before execution.

#### Scenario: Defaults applied

- **WHEN** a run omits segmentation parameters
- **THEN** the documented defaults are used

#### Scenario: Invalid parameter rejected

- **WHEN** a run supplies a value outside the permitted range (for example a mask
  threshold outside 0–1, or an overlap not smaller than the tile size)
- **THEN** the request is rejected and no run is created

#### Scenario: Class filter restricts output

- **WHEN** a run supplies a class filter
- **THEN** only regions whose class is in the filter appear in the output

#### Scenario: Tile size given in ground metres

- **WHEN** a run supplies a tile or overlap size in ground metres
- **THEN** the pixel tile and overlap are derived from the raster's ground sample
  distance, so region scale is consistent across resolutions

### Requirement: Tiled segmentation over large imagery

The operation SHALL process the entire orthophoto in tiles that overlap, and MUST
merge the per-tile masks so that a classified region spanning a tile boundary is
reported once and remains contiguous.

#### Scenario: Full extent is covered

- **WHEN** the orthophoto is larger than one tile
- **THEN** classified regions are produced across the whole image extent, not only
  the first tile

#### Scenario: Region spans a tile boundary

- **WHEN** a classified region crosses the boundary between two tiles
- **THEN** it appears once in the output rather than duplicated or split per tile

#### Scenario: Seams do not create false boundaries

- **WHEN** adjacent tiles classify the same region continuously
- **THEN** the merged output does not contain an artificial boundary at the tile
  seam

### Requirement: Mask post-processing

Before vectorizing, the operation SHALL remove regions smaller than the minimum
segment area and smooth mask artifacts, so the output is not dominated by noise.

#### Scenario: Small regions removed

- **WHEN** a classified region is smaller than the configured minimum segment area
- **THEN** it does not appear in the output

#### Scenario: Noise smoothed

- **WHEN** masks contain isolated or speckled pixels around a larger region
- **THEN** the resulting output region is smoothed rather than emitted as many
  fragments

### Requirement: Segmentation output

The operation SHALL produce a GeoJSON `FeatureCollection` in which each feature is
a polygon representing a classified region and carrying its class and area, plus a
confidence or score where the model provides one, with metadata reporting
per-class area and counts. Coordinates MUST be in EPSG:4326. An image with no
classified regions MUST yield an empty feature collection with zero counts.

#### Scenario: Regions produced

- **WHEN** the model classifies regions above the mask threshold
- **THEN** the output is a valid GeoJSON `FeatureCollection` whose features have
  polygon geometry and `class` and `area` properties

#### Scenario: Metadata reports coverage

- **WHEN** a run completes
- **THEN** the run metadata includes per-class area and counts and a total

#### Scenario: No regions

- **WHEN** the model classifies nothing above the threshold
- **THEN** the run completes with an empty feature collection and zero counts

### Requirement: Segmentation model catalog

The operation SHALL publish a curated list of known segmentation models so a UI
can offer them, where each entry carries its model file, labels, and recommended
parameters; an arbitrary model inside the managed directory MUST remain
selectable.

#### Scenario: Curated list offered

- **WHEN** a client requests the catalog for the segmentation operation
- **THEN** each known model carries its model file, labels, and recommended
  parameters

#### Scenario: Custom model allowed

- **WHEN** a user selects a model inside the managed directory that is not in the
  curated list
- **THEN** it is accepted subject to the model-contract checks

### Requirement: Segments render and download

Completed segmentation SHALL be viewable as a task map overlay styled by class as
filled regions with a legend, and downloadable as GeoJSON. Dense outputs MUST NOT
block the client: when the feature count is very large the overlay is not
auto-drawn, the user is told why, and the output remains downloadable.

#### Scenario: Class-distinguished fill overlay

- **WHEN** a completed segmentation output is shown on the map
- **THEN** its features render as filled regions with class-aware styling and a
  legend

#### Scenario: Dense output does not freeze the map

- **WHEN** a segmentation output exceeds the renderable feature limit
- **THEN** the overlay is skipped with an explanatory message and the output can
  still be downloaded
