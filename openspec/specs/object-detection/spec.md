# Object Detection Specification

## Purpose

Defines how an ONNX object-detection model is run over a task's orthophoto and
how its detections are parameterized, produced, and presented to users.

## Requirements

### Requirement: Detection operation availability

The system SHALL expose object detection as a catalog operation that consumes a
task's orthophoto and produces geospatial detections, discoverable and
runnable through the existing plugin catalog and enablement flow.

#### Scenario: Operation is listed

- **WHEN** a client requests the analysis catalog
- **THEN** object detection appears with its identifier, description, parameter
  schema, and required `orthophoto` input

#### Scenario: Operation unavailable

- **WHEN** object detection is not enabled for the caller's organization, or is
  disabled platform-wide
- **THEN** a run request is rejected as it is for any other plugin

### Requirement: Model configuration

The detection operation SHALL use an ONNX model file and a matching label set
supplied by configuration, with a platform default that an organization may
override, and MUST reject a run when the model or labels are missing,
unreadable, or inconsistent. The platform default SHALL be configurable so a
deployment can select a domain-appropriate model (for example an
aerial-trained model for top-down imagery).

#### Scenario: Default model used

- **WHEN** no model is configured for the organization
- **THEN** the platform default model and labels are used

#### Scenario: Aerial model selected

- **WHEN** a run or the platform default selects an aerial-trained model and its
  labels
- **THEN** detection uses that model and reports its classes

### Requirement: Detection model families

The operation SHALL support detectors with different output conventions — at
least a YOLO-style single tensor and a torchvision-style
`boxes`/`scores`/`labels` output — inferring the family when it is not
specified, and SHALL allow the class-index offset to be configured for models
whose labels do not start at zero.

#### Scenario: YOLO-family model

- **WHEN** the model exposes a YOLO-style `(N, 4 + classes, anchors)` output
- **THEN** its detections are decoded from that tensor

#### Scenario: Torchvision-family model

- **WHEN** the model exposes `boxes`, `scores` and `labels` outputs
- **THEN** its detections are decoded from those outputs

#### Scenario: Unrecognized model rejected

- **WHEN** the model exposes neither supported output shape
- **THEN** the run is rejected before it is created with a clear reason

#### Scenario: Label offset applied

- **WHEN** a model's labels do not start at the index of the first line in the
  label file
- **THEN** the configured offset maps model labels onto the label file

#### Scenario: Curated model list offered

- **WHEN** a client requests the catalog for an operation that declares known
  models
- **THEN** each entry carries its model file, labels, family, label offset, and
  recommended parameters so a UI can offer them without the user typing them

#### Scenario: Organization override used

- **WHEN** the organization has configured a model and labels
- **THEN** that model and its labels are used for that organization's runs

#### Scenario: Missing model rejected

- **WHEN** the configured model or label file cannot be read
- **THEN** the run is rejected before it is created and the reason is reported

### Requirement: Detection parameters

The operation SHALL accept a confidence threshold, an IoU threshold, a tile
size, a tile overlap, a maximum number of detections, and an optional class
filter. Omitted parameters MUST fall back to documented defaults and supplied
values MUST be validated before execution.

#### Scenario: Defaults applied

- **WHEN** a run omits detection parameters
- **THEN** the documented defaults are used

#### Scenario: Invalid parameter rejected

- **WHEN** a run supplies a value outside the permitted range (for example a
  confidence threshold outside 0–1)
- **THEN** the request is rejected and no run is created

#### Scenario: Class filter restricts output

- **WHEN** a run supplies a class filter
- **THEN** only detections whose class is in the filter appear in the output

#### Scenario: Class filter entered as a plain list

- **WHEN** a user enters the class filter as comma- or newline-separated class
  names
- **THEN** those names are used as the filter without requiring JSON syntax

#### Scenario: Tile size given in ground metres

- **WHEN** a run supplies a tile/overlap size in ground metres
- **THEN** the pixel tile and overlap are derived from the raster's ground
  sample distance, so object scale is consistent across resolutions

### Requirement: Tiled inference over large imagery

The operation SHALL process the entire orthophoto by tiles that overlap, and
MUST merge detections across tile boundaries so an object is reported once
regardless of where it falls relative to a tile edge.

#### Scenario: Object across a tile boundary

- **WHEN** an object spans the boundary between two tiles
- **THEN** it appears once in the output rather than duplicated per tile

#### Scenario: Full extent is covered

- **WHEN** the orthophoto is larger than one tile
- **THEN** detections are produced across the whole image extent, not only the
  first tile

#### Scenario: Padding and edge artifacts discarded

- **WHEN** a detection is centred in letterbox padding, or is clipped at an
  interior tile edge where a neighbouring tile sees it whole
- **THEN** it is not emitted as a detection

### Requirement: Detection output

The operation SHALL produce a GeoJSON `FeatureCollection` in which each feature
is a bounding box carrying its class label, class id, and confidence score, with
metadata reporting per-class counts and a total. Coordinates MUST be in
EPSG:4326. An image with no detections MUST yield an empty feature collection
with zero counts.

#### Scenario: Detections produced

- **WHEN** the model finds objects above the confidence threshold
- **THEN** the output is a valid GeoJSON `FeatureCollection` whose features have
  bounding-box geometry and `class`, `class_id`, and `confidence` properties

#### Scenario: Metadata reports counts

- **WHEN** a run completes
- **THEN** the run metadata includes per-class detection counts and a total

#### Scenario: No detections

- **WHEN** the model finds nothing above the threshold
- **THEN** the run completes with an empty feature collection and zero counts

### Requirement: Detections render and download

Completed detections SHALL be viewable as a task map overlay distinguished by
class and downloadable as GeoJSON. Dense detection sets MUST NOT block the
client: when the feature count is very large the overlay is not auto-drawn, the
user is told why, and the output remains downloadable.

#### Scenario: Class-distinguished overlay

- **WHEN** a completed detection output is shown on the map
- **THEN** its features render as an overlay with class-aware styling and a
  legend

#### Scenario: Dense output does not freeze the map

- **WHEN** a detection output exceeds the renderable feature limit
- **THEN** the overlay is skipped with an explanatory message and the output
  can still be downloaded
