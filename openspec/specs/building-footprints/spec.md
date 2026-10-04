# Building Footprints Plugin Specification

## Purpose

A user plugin (`plugins/building-footprints`) that extracts one polygon per
building from a task's orthophoto with a mask + signed-distance model and a
watershed that separates touching buildings, returning GeoJSON footprints
through the existing plugin execution and output mechanisms. Complements
`user-plugins` (packaging, sandbox) and `plugin-outputs` (storage, overlay,
download), which apply unchanged.

## Requirements

### Requirement: Packaged as a user plugin

Building footprint extraction SHALL be delivered as a user plugin package
(`plugins/building-footprints`) with `output_kind` `vector` and
`render_kind` `segmentation`, consuming the task orthophoto, so the existing
per-class polygon rendering draws the result without frontend changes.

#### Scenario: Plugin installed per organization

- **WHEN** an organization owner uploads the package
- **THEN** the plugin appears as `<org-slug>.building-footprints`, taking the
  task's orthophoto as its only input, and its runs are drawn as filled
  class-coloured polygons with a legend

### Requirement: One footprint per building

The plugin SHALL emit one polygon per building and SHALL separate touching or
adjacent buildings into distinct polygons by a marker-controlled watershed on
the model's signed-distance plane, seeding small blobs from distance local
maxima at least `seed_min_distance` pixels apart and large blobs (area ≥
`large_blob_area_px`) from extended maxima of prominence `h_maxima_depth`.

#### Scenario: Touching buildings

- **WHEN** two buildings share a wall and form one connected region above the
  confidence threshold
- **THEN** the output contains two polygons, one per building, whose union
  covers the region

#### Scenario: Single building with a long roof crest

- **WHEN** one building's distance plane has a flat or rippled crest with
  several equal maxima
- **THEN** the output contains exactly one polygon for it

#### Scenario: Prominence controls merging

- **WHEN** a large blob has two distance maxima whose dip is shallower than
  `h_maxima_depth`
- **THEN** they form one footprint; a dip deeper than `h_maxima_depth` yields
  two

### Requirement: Model cards drive the model

Models SHALL be described by JSON cards (id, label, ONNX file, sha-256,
default flag, output class, graph contract: input/output names, static input
size, channel order, scale, per-channel mean/std, output channel roles mask /
boundary / distance with the activation to apply; recommended parameters;
source and licence). Replacing the model MUST be a card edit. Weights SHALL
NOT be committed; `tools/fetch_models.py` SHALL download and verify them, and
`tools/update_manifest.py` SHALL regenerate the manifest's `model` enum.

#### Scenario: Different export

- **WHEN** a card names another ONNX file with its own tensor names,
  normalisation and channel roles
- **THEN** the plugin runs it with no code change

#### Scenario: Weights not packaged

- **WHEN** a run selects a card whose file is absent from the package
- **THEN** the run fails naming the file and the fetch command

### Requirement: Processing grid with a hard memory budget

The plugin SHALL process the orthophoto on a grid at `resolution_m` (card
default when 0) in the orthophoto's CRS, reading from the matching overview,
and SHALL refuse a grid above its pixel budget before reading any data with
a message naming a coarser resolution that fits.

#### Scenario: Grid too large

- **WHEN** the grid at the requested resolution exceeds the budget
- **THEN** the run fails with "set resolution_m to X or more" and re-running
  at X succeeds

#### Scenario: Finer than native

- **WHEN** `resolution_m` is finer than the orthophoto's
- **THEN** the native resolution is used with a warning

### Requirement: Overlapping windows with Gaussian stitching

The model SHALL be run over windows of its input size with `overlap`, each
window's planes weighted by a 2-D Gaussian centred on the window and averaged
in the overlaps, with windows holding no valid pixel skipped.

#### Scenario: Seamless planes

- **WHEN** neighbouring windows disagree near their edges
- **THEN** the stitched probability and distance planes show no seam at the
  window boundaries

### Requirement: Vector output

Each instance SHALL become one polygon (exterior ring only), simplified by
`simplify_m`, dropped below `min_area_m2`, and replaced by its minimum
rotated rectangle when it fills at least `regularize_area_threshold` of it
and the rectangle adds at most `regularize_overlap_tol_m2` of new overlap
with neighbours. Output SHALL be a GeoJSON FeatureCollection in EPSG:4326
whose features carry `class`, `score` (mean mask probability over the
instance) and `area` (m²); run metadata SHALL record count, model, windows
and processing grid.

#### Scenario: Near-rectangular building

- **WHEN** a footprint fills ≥ the threshold of its rotated rectangle without
  adding overlap beyond the tolerance
- **THEN** the feature is that rectangle and is flagged `regularized`

#### Scenario: L-shaped building

- **WHEN** a footprint fills less than the threshold of its rectangle
- **THEN** its simplified outline is kept

### Requirement: Parameters with card fallbacks

The plugin SHALL accept model, resolution_m, tile_size, overlap, confidence,
seed_min_distance, large_blob_area_px, h_maxima_depth, min_area_m2,
simplify_m, regularize_area_threshold and regularize_overlap_tol_m2. Values
of 0, -1 or "auto" MUST resolve to the selected card's recommended values and
the effective values MUST be recorded in the run metadata.

#### Scenario: Card defaults

- **WHEN** a run leaves every parameter at its default
- **THEN** the card's recommended values are used and recorded

### Requirement: Failures are reported by the run, not pre-validated

There SHALL be no pre-run validation. A missing file, sha-256 mismatch, graph
that does not match its card, unexpected output shape, bad input or oversize
grid MUST fail the run with one actionable line and no traceback.

#### Scenario: Checksum mismatch

- **WHEN** the packaged weights differ from the card's sha-256
- **THEN** the run fails naming the file and both digests

#### Scenario: Unexpected output shape

- **WHEN** the model returns planes of a size other than the input window
- **THEN** the run fails stating the shape received and the shape expected

### Requirement: Sandbox libraries

The plugin-runner image SHALL provide scipy and scikit-image in addition to
the existing library set.

#### Scenario: Libraries missing

- **WHEN** the plugin runs on an image without them
- **THEN** the run fails with one line asking the operator to update the image
