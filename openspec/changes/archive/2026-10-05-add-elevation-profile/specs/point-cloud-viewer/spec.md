## ADDED Requirements

### Requirement: Elevation profile along a transect

The viewer SHALL let a user draw a transect (a polyline of 2–50 vertices
picked on the point cloud) and SHALL show an elevation profile along it
computed on the server from the **whole** point cloud — never from the
points currently loaded in the browser — reading only the octree nodes
under the corridor, from the host cache or object storage. The profile
SHALL show the ground, the top surface and the height between them; where
the cloud is classified it SHALL show one layer per classification code
(excluding unclassified and noise codes), and where it is not it SHALL
still show ground and top. Bins with no points SHALL be shown as gaps,
never as values. When the points under the corridor exceed the configured
budget the service SHALL drop the deepest octree levels uniformly and
SHALL report the sampling actually used. The chart SHALL present length,
elevation range, ascent and descent, steepest grade, heights and coverage,
and pointing at the chart SHALL mark the corresponding spot in the 3D
scene. The request SHALL require read permission on the task and SHALL be
refused for a task whose octree is not Ready.

#### Scenario: Classified cloud

- **WHEN** a transect crosses a building and a tree row in a cloud with ASPRS classes
- **THEN** the ground is the median of class-2 points per bin, the building and the vegetation are separate layers with their heights above ground, ground under the building is interpolated and marked as such, and noise classes are excluded

#### Scenario: Unclassified cloud

- **WHEN** the cloud carries no classification (or only codes 0/1)
- **THEN** the profile shows ground and top only, the ground is a filtered low envelope labelled as such, and the UI points at the `pc-classify` option

#### Scenario: Gap

- **WHEN** a run of bins along the line contains no points
- **THEN** every series is `null` there, the run is listed in `gaps` and drawn as a hatched column, and summary numbers never bridge it

#### Scenario: Very large cloud

- **WHEN** the nodes under the corridor hold more points than `PROFILE_MAX_POINTS`
- **THEN** whole octree levels are dropped deepest-first until the budget fits, the step is widened to the sampled spacing, and `sampling.full_resolution` is false with the spacing reported

#### Scenario: Octree in object storage

- **WHEN** the host copy of `octree.bin` has been evicted
- **THEN** the profile is read from the `s3://` object by byte range (mixed with cached local files) and a background refill is queued

#### Scenario: Outsider or unprepared cloud

- **WHEN** a user from another organization calls the profile API, or the task has no Ready octree
- **THEN** the call is refused with a permission error, or with a message saying the point cloud must be opened in the viewer first
