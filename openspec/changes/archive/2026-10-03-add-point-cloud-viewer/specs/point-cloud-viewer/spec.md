## Purpose

Lets a user inspect a completed task's point cloud (ODM's LAZ) directly in the
platform's 3D viewer — converting it to a streamable octree on demand and
rendering it with level-of-detail, color, filtering and measurement controls.

## ADDED Requirements

### Requirement: On-demand conversion and reuse

Opening a task's point cloud SHALL convert its stored LAZ to a Potree octree on
first use, not during ODM processing, and SHALL reuse the existing octree on
every later open. A task with no point cloud SHALL be reported as having none
rather than failing.

#### Scenario: First open converts

- **WHEN** a user opens the point cloud of a completed task that has never been converted
- **THEN** a conversion is started, its state is shown, and the cloud appears when it finishes

#### Scenario: Later opens reuse

- **WHEN** a user opens the same task's point cloud again
- **THEN** the existing octree is served without starting another conversion

#### Scenario: Task without a point cloud

- **WHEN** a task has no stored point cloud
- **THEN** the viewer explains that the task produced no point cloud and does not start a conversion

### Requirement: Conversion status and failures

A conversion SHALL progress through Queued, Running, Ready and Failed, SHALL be
idempotent for concurrent requests, and SHALL record one readable error message
when it fails. A failed conversion SHALL be retriable without re-opening the
task, and SHALL NOT affect the task's own outputs or status.

#### Scenario: Concurrent opens

- **WHEN** two users open the same unconverted point cloud at the same time
- **THEN** only one conversion runs and both eventually load the same octree

#### Scenario: Failure is reported and retriable

- **WHEN** a conversion fails (for example, an unreadable LAZ)
- **THEN** its state is Failed with a readable reason, the task keeps its point cloud and outputs, and the user can retry

#### Scenario: Restart is safe

- **WHEN** the worker restarts while a conversion is Running
- **THEN** the conversion is resumed or restarted, not left permanently Running

### Requirement: Octree content and summary

The conversion SHALL produce a Potree 2.0 octree (metadata, hierarchy and octree
buffers) that preserves the point cloud's coordinates, elevation, RGB, intensity
and classification, and SHALL record a summary of the result.

#### Scenario: Attributes preserved

- **WHEN** a LAZ carries RGB, intensity and classification
- **THEN** the octree retains them and the viewer can color and filter by each

#### Scenario: Summary recorded

- **WHEN** a conversion completes
- **THEN** the point count, octree byte size, native and EPSG:4326 bounds, CRS and available attributes are recorded for display and returned with the task's state

#### Scenario: Empty or unreadable input

- **WHEN** the LAZ cannot be read or contains no points
- **THEN** the conversion fails with a readable reason and records no summary

### Requirement: Authenticated same-origin streaming

The octree's files SHALL be served to the browser by the application over a
same-origin, session-authenticated endpoint that supports HTTP range requests,
serves only the octree's known member names, and refuses a user who cannot read
the task. The geospatial service SHALL NOT be reachable directly by the browser.

#### Scenario: Range request

- **WHEN** the loader requests a byte range of the octree buffer
- **THEN** a partial response with the correct range headers is returned and the loader can stream the cloud progressively

#### Scenario: Read authorization

- **WHEN** a user without read access to the task requests its octree
- **THEN** the request is refused and no octree bytes are returned

#### Scenario: Path traversal

- **WHEN** a request names a file outside the task's octree directory or an unknown member
- **THEN** the request is refused

### Requirement: Viewer integration

The existing 3D viewer SHALL offer the task's point cloud as a source alongside
the task's model and its reconstruction runs, SHALL indicate when a cloud is
still converting, and SHALL keep the selected source in the page URL so it can be
linked and shared.

#### Scenario: Source switch

- **WHEN** a task has both a model and a point cloud
- **THEN** the viewer can switch between them without leaving the page

#### Scenario: Converting state

- **WHEN** the point cloud is selected while its conversion is queued or running
- **THEN** the viewer shows that state and, when the conversion reports it, its progress, and loads the cloud automatically when it becomes ready

### Requirement: Level-of-detail rendering

The viewer SHALL render the octree with level-of-detail so that a cloud of many
millions of points stays interactive, SHALL respect a point budget, and SHALL
show load progress and decode/GPU failures distinctly from conversion failures.

#### Scenario: Large cloud

- **WHEN** a multi-million-point octree is opened
- **THEN** the visible point count stays within the budget while orbiting and the cloud becomes progressively sharper as the camera rests

#### Scenario: Budget control

- **WHEN** the user changes the point budget
- **THEN** the rendered density changes accordingly without reloading the cloud

#### Scenario: Render failure

- **WHEN** the octree cannot be decoded or WebGL is unavailable
- **THEN** a distinct error or unsupported state is shown with a retry or download option

### Requirement: Color modes

The viewer SHALL color the cloud by RGB, elevation, intensity or classification,
SHALL offer only modes whose attribute is present in the cloud, and SHALL show a
legend for elevation and classification.

#### Scenario: Attribute missing

- **WHEN** a cloud has no intensity
- **THEN** the intensity mode is unavailable rather than rendering an empty or wrong color

#### Scenario: Classification legend

- **WHEN** the cloud is colored by classification
- **THEN** a legend names the classes present and their colors

### Requirement: Appearance and filtering

The viewer SHALL let the user adjust point size and background, and SHALL filter
the rendered cloud by an elevation range and by classification visibility, with
feedback on how many points the filter hides.

#### Scenario: Elevation window

- **WHEN** the user narrows the elevation range
- **THEN** only points inside the window are drawn and the hidden points are excluded from measurements

#### Scenario: Classification filter

- **WHEN** the user hides one or more classes
- **THEN** points of those classes are not drawn and the remaining view updates immediately

### Requirement: Measurement

The viewer SHALL measure distance between picked points and area of a picked
polygon on the cloud, and SHALL measure volume when a surface model (the task's
DSM or a reconstruction mesh) is available for the measured area. Measurements
SHALL be clearable and SHALL NOT change the cloud.

#### Scenario: Distance

- **WHEN** the user picks two points
- **THEN** the horizontal and 3D distances are shown in the task's units

#### Scenario: Area

- **WHEN** the user picks three or more points forming a polygon
- **THEN** the polygon's area is shown

#### Scenario: Volume without a surface

- **WHEN** the user requests volume and no surface model covers the polygon
- **THEN** the viewer explains that a surface (DSM or reconstruction) is required and offers none instead of a wrong value
