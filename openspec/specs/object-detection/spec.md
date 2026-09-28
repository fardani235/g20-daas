# Object Detection Plugin Specification

## Purpose

A user plugin (`plugins/object-detection`) that runs an ONNX object detector
over a task's orthophoto and returns the detections as georeferenced bounding
boxes through the existing plugin execution and output mechanisms. Object
detection is no longer a system operation of the geospatial service; each
organization uploads the plugin with the detectors it wants. Complements
`user-plugins` (packaging, sandbox) and `plugin-outputs` (storage, map
overlay, download), which apply unchanged.

## Requirements

### Requirement: Packaged as a user plugin

Object detection SHALL be delivered as a user plugin package (manifest,
entrypoint, model cards) that an organization uploads, with `output_kind`
`vector` and `render_kind` `detections`, and SHALL NOT exist as a system
operation in the geospatial catalog.

#### Scenario: Plugin installed per organization

- **WHEN** an organization owner uploads the package
- **THEN** the plugin appears in that organization's catalog as
  `<org-slug>.object-detection`, consuming the task orthophoto

#### Scenario: System operation retired gracefully

- **WHEN** the geospatial catalog no longer lists `object-detection`
- **THEN** catalog sync marks the old row unavailable and its run history and
  outputs remain queryable

### Requirement: Model cards

Detectors SHALL be described by JSON model cards (id, label, ONNX file,
family, class names, label offset, normalisation, recommended parameters,
optional checksum, provenance and licence) so a detector can be added or
replaced by adding a card and its weights and rebuilding the package, without
changing plugin code. The manifest's `model` enum SHALL be generated from the
cards.

#### Scenario: Customer adds a detector

- **WHEN** a card and its `.onnx` file are added to `models/` and the package
  is rebuilt and uploaded
- **THEN** the new model is selectable in the run dialog and runs with its
  card's recommended settings

#### Scenario: Weights not packaged

- **WHEN** a run selects a card whose weights were not fetched or supplied
- **THEN** the run fails with a message naming the missing file and how to
  add it, while other models remain usable

#### Scenario: No AGPL weights committed

- **WHEN** the repository is inspected
- **THEN** no detector weights are tracked; large or AGPL-licensed models are
  fetched at build time by `tools/fetch_models.py` with their licence shown

### Requirement: Detection model families

The plugin SHALL support detectors with different output conventions — a
YOLO-style single tensor `(N, 4 + classes, anchors)` and a torchvision-style
`boxes`/`scores`/`labels` output — inferring the family when the card says
`auto`, and SHALL apply the card's label offset for models whose labels do
not start at zero.

#### Scenario: YOLO-family model

- **WHEN** the model exposes a YOLO-style output
- **THEN** its detections are decoded from that tensor and the class count is
  checked against the card

#### Scenario: Torchvision-family model

- **WHEN** the model exposes `boxes`, `scores` and `labels` outputs
- **THEN** its detections are decoded from those outputs with the card's
  label offset

### Requirement: Failures are reported by the run, not pre-validated

There SHALL be no pre-run validation step. A bad or mismatched model MUST fail
the run with a clear, actionable error visible in the run panel.

#### Scenario: Class count mismatch

- **WHEN** the model predicts a different number of classes than the card lists
- **THEN** the run fails naming both numbers

#### Scenario: Checksum mismatch

- **WHEN** the card carries a `sha256` and the packaged weights differ
- **THEN** the run fails naming the file and both digests

#### Scenario: Unsupported model

- **WHEN** the model exposes neither supported output layout
- **THEN** the run fails describing the outputs it found

### Requirement: Detection parameters

The plugin SHALL accept a confidence threshold, an IoU threshold, tile size
and overlap (in pixels or ground metres), a maximum number of detections and
a class filter given as comma-separated names. Values left at 0/-1 MUST fall
back to the selected card's recommended values, then to documented defaults;
supplied values MUST be validated against the manifest before the run is
created and again by the plugin.

#### Scenario: Card defaults applied

- **WHEN** a run leaves the thresholds and tiling at their defaults
- **THEN** the selected card's recommended values are used and recorded in the
  run metadata

#### Scenario: Tile size given in ground metres

- **WHEN** a run (or the card) supplies `tile_size_m` / `overlap_m`
- **THEN** the pixel tile and overlap are derived from the orthophoto's ground
  sample distance, so object scale is consistent across resolutions

#### Scenario: Class filter

- **WHEN** a run supplies class names
- **THEN** only detections of those classes appear; an unknown name fails the
  run listing the model's classes

### Requirement: Tiled inference over large imagery

The plugin SHALL process the entire orthophoto in overlapping tiles, skip
tiles outside the valid (alpha/nodata) area, drop boxes centred in letterbox
padding or clipped at an interior tile edge, and merge detections across
tiles with per-class NMS so an object is reported once.

#### Scenario: Object across a tile boundary

- **WHEN** an object spans two tiles
- **THEN** it appears once in the output

#### Scenario: Nodata collar

- **WHEN** tiles fall entirely outside the survey area
- **THEN** they are skipped and counted in the metadata

### Requirement: Detection output

The plugin SHALL produce a GeoJSON `FeatureCollection` in EPSG:4326 whose
features are bounding-box polygons carrying `class`, `class_id`, `confidence`
and `area_m2`, with metadata reporting per-class counts, a total, the model
used, the tiling and a legend. An image with no detections MUST yield an
empty collection with zero counts.

#### Scenario: Detections produced

- **WHEN** the model finds objects above the confidence threshold
- **THEN** the output is a valid `FeatureCollection` of bounding boxes with
  the properties above, rendered on the map with class-aware styling

#### Scenario: No detections

- **WHEN** nothing scores above the threshold
- **THEN** the run completes with an empty feature collection and zero counts
