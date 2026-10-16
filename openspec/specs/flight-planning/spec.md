# Flight Planning Specification

## Purpose

Lets a member plan a drone mapping mission inside a project before flying
it: locate the site, draw an area or a corridor, choose the aircraft and
camera, and obtain flight lines with estimates of resolution, photos, flight
time and batteries, checked against the selected aircraft's limits, then
export the plan in a format that aircraft's flight app can use. A plan is a
planning estimate; the platform never presents it as verified flight-safety
information.

## Requirements

### Requirement: Locating the site

The planner SHALL locate a site from typed coordinates or from an address.
Coordinates SHALL be parsed without any external lookup; an address SHALL be
resolved through the configured geocoder by the server, never by the
browser. When the geocoder is disabled or fails, coordinate entry MUST keep
working.

#### Scenario: Coordinates

- **WHEN** a member searches for `-6.7900, 107.6180`, `6.79 S 107.618 E` or `6°47'24"S 107°37'05"E`
- **THEN** the map moves there and no request leaves the platform

#### Scenario: Longitude first

- **WHEN** the first number cannot be a latitude, or hemisphere letters say the order is longitude, latitude
- **THEN** the pair is swapped and the result says so; an ambiguous pair is read latitude first

#### Scenario: Geocoder unavailable

- **WHEN** the address search is switched off or the service fails
- **THEN** the search returns no results with a message that coordinates can still be entered

### Requirement: Area and corridor missions

A plan SHALL be either an **area** (a polygon) or a **corridor** (a line with
a width). For an area the planner SHALL lay parallel flight lines across the
polygon; for a corridor it SHALL lay passes parallel to the centre line that
together cover the width. Flight lines SHALL be generated on the server from
the definition.

#### Scenario: Area

- **WHEN** a 400 m × 300 m rectangle is planned with a line spacing of 43 m
- **THEN** seven lines are laid along the long side, evenly spaced and centred, flown alternately in opposite directions

#### Scenario: Automatic direction

- **WHEN** the flight direction is automatic
- **THEN** the lines run in the direction that needs no more lines than any other direction

#### Scenario: Concave area and holes

- **WHEN** a flight line leaves and re-enters the area, or crosses a hole
- **THEN** it is flown as separate photographed legs and the gap is not photographed, except gaps of a couple of photo spacings, which are flown through

#### Scenario: Corridor

- **WHEN** a corridor's width exceeds what one swath covers
- **THEN** enough parallel passes are planned, centred on the line and following its bends, that the swaths cover the width

#### Scenario: Unusable shape

- **WHEN** the outline crosses itself, has too few points, or is larger than the supported extent
- **THEN** no mission is produced and the reason is returned with the field it concerns

### Requirement: Equipment profiles

Aircraft and cameras SHALL be data, separate from the planning logic: a
built-in catalog and an organization's own profiles, read through the same
validation and reaching the planner in the same form. Adding a model to the
catalog MUST NOT require a change to planner, check or exporter code. A
figure that is not known SHALL be representable as unknown, and the check
that needs it SHALL be skipped visibly rather than assumed to pass.

#### Scenario: Built-in catalog

- **WHEN** the catalog is loaded
- **THEN** every aircraft's cameras and mission formats resolve, and each aircraft states whether it supports autonomous waypoint flight and which mission formats its flight app loads, with a support level of `official`, `community` or `boundary`

#### Scenario: Custom profile

- **WHEN** a member saves a camera or drone profile
- **THEN** it is validated like a catalog entry, belongs to their organization, and is offered in the planner; an invalid profile is refused through the API, Desk and the REST resource alike

#### Scenario: Other organizations

- **WHEN** a member of another organization lists equipment, or plans with the id of a profile that is not theirs
- **THEN** the profile is absent, and the id behaves exactly like one that does not exist

#### Scenario: Unknown figure

- **WHEN** a camera's shortest photo interval is not published
- **THEN** a cautious value is assumed, the plan says it is assumed, and a too-short interval is a warning rather than a blocking error

### Requirement: Flight parameters and estimates

The planner SHALL accept flight height or ground resolution (each derived
from the other through the selected camera), speed, front and side overlap,
flight direction, camera orientation and gimbal pitch, and SHALL return
ground resolution, photo footprint, line and photo spacing, flight distance,
flight duration, photo count, coverage area and the number of batteries,
computed from the selected drone, camera and parameters.

#### Scenario: Height and resolution

- **WHEN** a ground resolution is given instead of a height
- **THEN** the height is derived from the camera, and the mission is planned at that height

#### Scenario: Equipment changes the plan

- **WHEN** the same area is planned with a different camera or aircraft
- **THEN** the resolution, line count, photo count, flight time and battery count change accordingly

#### Scenario: Batteries

- **WHEN** the mission does not fit the usable time of one battery (rated time × endurance factor × (1 − reserve))
- **THEN** it is split into flights at flight-line ends, each including its own transit from and to the takeoff point, and the battery count is the number of flights

### Requirement: Terrain

When elevation data is available the planner SHALL report the height above
ground along the route and its effect on resolution and overlap, and SHALL
support terrain following, in which each waypoint's height is derived from
the elevation data so that the height above ground stays within a tolerance
of the target. Elevation data SHALL come from a configurable global service
or from the DSM / DTM of a completed task of the same project. Its absence
or failure MUST NOT prevent planning and SHALL be reported.

#### Scenario: Constant altitude over rising ground

- **WHEN** the ground rises under a constant-altitude route
- **THEN** the plan reports the range of height above ground and of resolution, and flags an overlap that drops too low or a clearance below the minimum

#### Scenario: Terrain following

- **WHEN** terrain following is selected and elevation data covers the site and the takeoff point
- **THEN** waypoint heights vary with the ground, waypoints are added only where the ground bends, and the height above ground stays within the tolerance

#### Scenario: No data

- **WHEN** terrain following is selected and no elevation data is available
- **THEN** the route is still returned, with a blocking finding

#### Scenario: Service failure

- **WHEN** the elevation service fails
- **THEN** the plan is computed on flat ground with a warning, and the failure is not cached

#### Scenario: Project survey

- **WHEN** the DSM of a completed task of the same project is chosen
- **THEN** it is sampled through the geospatial service with the highest surface in each cell, and a task of another project or organization reads as missing

### Requirement: Validation against the aircraft

Every plan SHALL be checked against the selected aircraft's and camera's
known limits: mission speed, minimum airspeed, the camera's photo interval at
the planned speed, height limits, service ceiling where the terrain elevation
is known, climb and descent rate under terrain following, and battery
endurance. A plan the aircraft cannot fly as specified SHALL carry an
`error` finding stating the limit and what to change.

#### Scenario: Too fast for the airframe

- **WHEN** the planned speed exceeds the aircraft's limit for automated flight
- **THEN** the plan carries a blocking finding naming the limit

#### Scenario: Too fast for the camera

- **WHEN** the photo interval at the planned speed is shorter than the camera's minimum
- **THEN** the finding states the highest speed that works

#### Scenario: No autonomous flight

- **WHEN** the selected aircraft has no waypoint flight
- **THEN** the plan says it can only serve as a reference and no mission file is offered

### Requirement: Estimates are distinguished from verified information

Every finding SHALL state its severity (`error`, `warning`, `info`) and how
it was arrived at (`checked`, `estimated`, `advisory`, `not_checked`, or
`note`). Every mission SHALL carry, per topic — aircraft limits, terrain,
obstacles, batteries, aviation rules, airspace, weather — whether it was
checked, estimated, advisory or not checked. Nothing SHALL be reported as
verified, safe or cleared, and a fixed notice that the plan is a planning
estimate SHALL accompany every mission and every exported overview.

#### Scenario: What was not checked

- **WHEN** any plan is computed
- **THEN** obstacles and weather are reported as not checked; airspace is not checked unless a zone file is installed

#### Scenario: Terrain

- **WHEN** elevation data was used
- **THEN** terrain is reported as estimated, naming the source, its resolution and nominal accuracy; without data it is reported as not checked and the ground is stated to be assumed flat

#### Scenario: Airspace zone file

- **WHEN** an operator-supplied zone file is installed and the plan touches a zone
- **THEN** the zone is named in an advisory warning; a plan touching no zone is described as not touching any zone in that file, not as cleared

#### Scenario: Rule preset

- **WHEN** the route exceeds the height limit or the line-of-sight distance of the selected rule preset
- **THEN** an advisory warning is raised and the plan remains exportable after acknowledgement

### Requirement: Plans in projects

A flight plan SHALL belong to a project and to that project's organization,
be visible and editable by the organization's members only, and be deleted
with its project. Its computed mission SHALL always be produced by the
server from the stored definition; a mission supplied by a client MUST be
ignored. A plan SHALL be duplicable within a project or into another project
of the organization.

#### Scenario: Save and reopen

- **WHEN** a plan is saved and opened again
- **THEN** its shape, takeoff point, equipment, the parameters the user set, and its estimates are restored

#### Scenario: Forged mission

- **WHEN** a plan document is written with a mission whose findings were removed
- **THEN** the stored mission is recomputed from the definition

#### Scenario: Other organizations

- **WHEN** a member of another organization lists, opens, edits, exports or deletes the plan
- **THEN** the list omits it and each direct request is denied

#### Scenario: Profile deleted later

- **WHEN** the custom profile a saved plan used is deleted
- **THEN** the plan still opens, recomputes and exports with the equipment snapshot it was saved with

### Requirement: Mission export

A saved plan SHALL be exportable as a mission file in the formats the
selected aircraft's profile lists (DJI WPML, QGroundControl plan, MAVLink
waypoint list, Litchi CSV), as a boundary file for flight apps that plan
their own lines, and as reference files. Export formats SHALL be pluggable.
The export SHALL be refused when the format does not apply to the aircraft,
when the plan or the format has an `error` finding, and, for mission files,
when `warning` findings have not been acknowledged.

#### Scenario: Per-aircraft formats

- **WHEN** export options are requested for a plan
- **THEN** each format states whether it is available for the plan's aircraft, with the support level, or why it is not

#### Scenario: Blocked

- **WHEN** the plan has a blocking finding
- **THEN** no mission file is produced, with or without acknowledgement, while boundary and reference files remain available

#### Scenario: Acknowledgement

- **WHEN** the plan has warnings and a mission file is requested without acknowledgement
- **THEN** it is refused; with acknowledgement it is produced and the export, the user and the acknowledged warnings are recorded on the plan

#### Scenario: Waypoint and battery limits

- **WHEN** the mission needs several batteries, or more waypoints than the format or the aircraft's flight app accepts
- **THEN** it is exported as several files in flight order, split at flight-line ends, each starting where the previous one ended

#### Scenario: Heights that cannot be loaded

- **WHEN** a terrain-following plan is exported for an aircraft whose missions cannot carry a height per waypoint
- **THEN** the mission export is refused

#### Scenario: File contents

- **WHEN** a mission file is produced
- **THEN** its waypoints, order and heights are those of the stored mission, and its photo trigger is the one requested among those the format supports
