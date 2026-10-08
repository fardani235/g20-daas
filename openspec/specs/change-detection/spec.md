# Change Detection Plugin Specification

## Purpose

A user plugin (`plugins/change-detection`) that compares two surveys of the
same project — a *before* and an *after* — and reports what changed and how
much: cut and fill regions with area, volume and uncertainty, and the share
of the commonly covered area that changed. It uses classical raster analysis
only (no machine learning, no model files). It is the first *comparison*
plugin; the platform mechanics it relies on are specified in
`plugin-execution` (Comparison runs), `user-plugins` (Reference-survey
inputs) and `plugin-outputs` (Result summary on the run row).

## Requirements

### Requirement: Packaged as a comparison plugin

Change detection SHALL be delivered as user plugin packages built from one
code base: `change-detection` (`output_kind` `vector`, `render_kind`
`change`) and `change-detection-raster` (`output_kind` `raster`). Each SHALL
declare an elevation surface (`dsm` or `dtm`) and an optional orthophoto for
the run's task and the same pair with `survey: "reference"`. The packages
SHALL contain no model files and require no network access. The parameter
`reference_role` (`before` | `after`) SHALL decide which survey is the
earlier one; fill SHALL mean the surface is higher in the later survey.

#### Scenario: Installed and run on two surveys

- **WHEN** an organization owner uploads the package and a member runs it on
  a task, choosing another task of the project to compare with
- **THEN** the run produces change regions and a summary on the task it was
  started on

#### Scenario: Roles swapped

- **WHEN** the same pair is run with `reference_role` reversed
- **THEN** cut and fill swap and the removed offset changes sign

#### Scenario: Same survey twice

- **WHEN** both surfaces are the same raster
- **THEN** the run fails saying so instead of reporting no change

### Requirement: Common grid

The two surveys SHALL be compared on one grid: the CRS of the run's task's
input when projected (else the reference's, else a local UTM zone), the
intersection of the two footprints less `edge_buffer_m`, and the coarser of
the two native resolutions unless `resolution_m` asks for coarser. A grid
above the pixel budget SHALL be coarsened with a warning, not refused.
Downsampling SHALL average. Surveys that do not overlap SHALL fail the run
with one line.

#### Scenario: Different resolution, footprint and CRS

- **WHEN** the surveys differ in resolution, extent and coordinate system
- **THEN** the comparison covers their overlap at the coarser resolution in
  the run's task's CRS and volumes match a same-grid comparison

#### Scenario: No overlap

- **WHEN** the footprints do not intersect
- **THEN** the run fails with a message saying the surveys do not overlap

### Requirement: Vertical co-registration measured on unchanged ground

With `vertical_alignment` `auto` the plugin SHALL estimate the height offset
between the surveys as the mode of the height-difference distribution over
the overlap and subtract it, SHALL take the width of that peak as the noise,
and SHALL report the share of the overlap in the peak and any second peak.
It SHALL measure how constant the offset is on a tiling of the site, taking
the largest group of tiles that agree with one flat or tilted surface as
unchanged ground and their spread as the systematic uncertainty. A tilt
SHALL be reported and removed only with `vertical_alignment` `tilt`. With
`none` nothing SHALL be removed and the offset of the overlap SHALL still be
reported.

#### Scenario: Constant offset with a large excavation

- **WHEN** the later survey sits a constant amount above the earlier one and
  a large part of the site was excavated
- **THEN** the removed offset equals the constant within the noise and the
  unchanged ground reports no change

#### Scenario: Tilt

- **WHEN** the offset drifts across the site
- **THEN** `auto` raises the level of detection, warns and suggests `tilt`;
  `tilt` removes the drift and restores the level of detection

#### Scenario: Heights trusted

- **WHEN** `vertical_alignment` is `none` and most of the overlap differs by
  more than the noise
- **THEN** that difference is reported as change and the run is flagged

### Requirement: Horizontal co-registration applied only when supported

The plugin SHALL estimate a horizontal translation of the reference survey
within `max_shift_m` and SHALL apply it only when it improves the agreement
of the two surfaces and two independent halves of the samples find the same
position; otherwise, and with `horizontal_alignment` `off`, the surveys are
compared in place. A significant shift that is not applied SHALL widen the
registration tolerance and be reported.

#### Scenario: Shifted survey

- **WHEN** one survey is displaced by more than a cell on a site with relief
- **THEN** the shift is corrected and no regions appear at building edges

#### Scenario: Featureless ground

- **WHEN** the surfaces offer nothing to align on
- **THEN** no shift is applied

### Requirement: Change separated from noise

A cell SHALL count as changed only where its difference from every cell
within the registration tolerance in the other survey reaches the level of
detection — the confidence multiple of the combined noise and systematic
uncertainty, never below the automatic floor or `min_height_change_m` — over
a connected region of at least `min_area_m2`. Height change on cells an
orthophoto shows as green vegetation SHALL form its own class and be
excluded from cut and fill when `vegetation_filter` is on.

#### Scenario: Unchanged site

- **WHEN** two surveys of an unchanged site differ only by noise and offset
- **THEN** no regions are reported and the changed share is zero

#### Scenario: Thin and tiny features

- **WHEN** the site has a trench one metre wide and a speck of one square
  metre
- **THEN** the trench is a region and the speck is not

#### Scenario: Vegetation

- **WHEN** trees grew between the surveys and an orthophoto is supplied
- **THEN** their height change is reported as `vegetation` and not as fill

### Requirement: Numbers with their uncertainty

The result SHALL report the area both surveys cover, the changed area and
its share of that area, cut, fill and net volume each with a one-sigma
uncertainty combining random, systematic and below-detection edge terms, and
per region its area, volume, uncertainty, mean and extreme height change.
It SHALL report a reliability grade (`high`, `medium`, `low`) with reasons,
grading `low` when little of the overlap is unchanged, two candidate offsets
compete, or a DSM is compared with a DTM. A human-readable `summary` and
`warnings` SHALL be included in the metadata.

#### Scenario: Known earthworks

- **WHEN** the later survey contains a pit and a stockpile of known volume
- **THEN** cut and fill match them within the tolerance of the test suite and
  the changed share equals changed area over commonly covered area

#### Scenario: Competing offsets

- **WHEN** two parts of the site of similar size sit at different levels
- **THEN** the larger is taken as unchanged ground and the run is graded
  `low`, naming both offsets

### Requirement: Orthophotos are secondary

Orthophotos SHALL never affect a volume and SHALL never fail a run that has
an elevation pair: an unreadable orthophoto or a failure of the photo stage
SHALL become a warning. With both orthophotos and `appearance_change` on,
cells that look different while their height did not change SHALL be
reported as `appearance` regions without volume, and SHALL be dropped with a
warning when they cover more than the comparability limit. Without an
elevation pair the regions package SHALL compare the orthophotos alone,
without volumes, graded `low`; the raster package SHALL fail.

#### Scenario: Broken orthophoto

- **WHEN** one orthophoto cannot be read
- **THEN** the elevation result is unchanged and a warning names the file

#### Scenario: Photos only

- **WHEN** only orthophotos are selected
- **THEN** appearance regions are returned with no volumes and low reliability
