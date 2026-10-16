# Flight Simulation Specification

## Purpose

Lets a member replay a flight plan before flying it: the aircraft follows
the planned mission on the map and over 3D terrain, with playback controls
and a dashboard, so the mission and its estimates can be seen in time and
space. A simulation is an estimate derived from the plan; the platform
never presents it as flight telemetry or as verified flight-safety
information, and simulating never changes the plan.

## Requirements

### Requirement: Simulating a plan

Any plan with a computed mission SHALL be simulable from the planner and
from the plan list, saved or not. The simulation SHALL be built from the
mission the planner computed: its waypoints, legs, battery flights, speeds,
heights and photo counts. It MUST NOT lay out flight lines or decide
altitudes of its own.

#### Scenario: From the planner

- **WHEN** a member presses *Simulate flight* on a plan that has a mission, saved or unsaved
- **THEN** the simulation of the mission on screen opens around the planner's map, at time zero, paused

#### Scenario: From the plan list

- **WHEN** a member presses *Simulate* on a plan in the list
- **THEN** the plan opens with its simulation

#### Scenario: Nothing to simulate

- **WHEN** a plan has no computed route
- **THEN** simulating is not offered, and a mission that cannot be simulated is reported as a message, not as a failure of the page

#### Scenario: The plan is edited

- **WHEN** the plan changes while its simulation is open
- **THEN** the simulation is rebuilt from the new mission and returns to its start; when the plan stops computing, the simulation closes

### Requirement: The route flown is the route planned

The simulated aircraft SHALL pass through every waypoint of the mission, in
order, at the waypoint's height, starting and ending on the ground at the
takeoff point (or, where the plan lands in place, at the last waypoint).
Photo events SHALL be one per planned photo, at the positions the plan's
photo spacing gives along each leg.

#### Scenario: Waypoints

- **WHEN** a mission is simulated
- **THEN** each leg is flown through its waypoints in order, at their planned heights, and legs are flown once each in the planned order

#### Scenario: Photos

- **WHEN** a mission with *n* planned photos is simulated
- **THEN** exactly *n* photo events occur, leg by leg as planned, at the positions the per-photo mission export uses

### Requirement: Playback

The simulation SHALL offer play, pause, stop (return to the start),
restart, seeking to any moment, and playback speeds from 0.5× to 64×.
Playback SHALL be deterministic: the state shown for a time depends only on
the mission and that time, however the time was reached.

#### Scenario: Controls

- **WHEN** play, pause, stop and restart are used
- **THEN** time advances only while playing, stop returns to time zero paused, and restart plays again from time zero

#### Scenario: Speed

- **WHEN** the speed is changed
- **THEN** simulated time passes that many times faster, and the state at any given time is unchanged

#### Scenario: Seeking

- **WHEN** a member drags the timeline or clicks the elevation profile
- **THEN** every view shows that moment; playing to a time and seeking to it show the same state

### Requirement: Flight dynamics from the aircraft's profile

Motion between waypoints SHALL be smooth and bounded by the aircraft's
known capabilities: the planned speeds capped at the aircraft's mission
speed limit, its published climb and descent rates, and a bounded
acceleration. An aircraft that hovers SHALL stop at flight-line ends and
turn on the spot; an aircraft that cannot hover SHALL join lines with
curves no tighter than its turn radius. Where a figure is not published, an
assumed value SHALL be used and stated.

#### Scenario: Limits

- **WHEN** any mission is simulated
- **THEN** position is continuous in flight, ground speed never exceeds the planned speed, vertical speed never exceeds the published rates, and speed changes no faster than the acceleration allows

#### Scenario: A climb the aircraft cannot do at the planned speed

- **WHEN** a stretch of the route would need a faster climb or descent than the aircraft's rate at the planned speed
- **THEN** the simulation flies that stretch slower and says how many stretches were affected

#### Scenario: Multirotor

- **WHEN** a multirotor mission is simulated
- **THEN** the aircraft is at rest at the start and end of every flight line and yaws between them

#### Scenario: Fixed-wing

- **WHEN** a fixed-wing mission is simulated
- **THEN** the aircraft never stops between takeoff and landing, its turns are no tighter than its turn radius, and the simulation states that the plan's time estimate approximates these turns

### Requirement: Several batteries

A mission split into several battery flights SHALL be simulated flight by
flight: each with its own takeoff, transit, survey, return and landing,
separated by a battery change on the ground. Battery use SHALL be estimated
per flight with the plan's model and labelled an estimate.

#### Scenario: Flights

- **WHEN** a mission of three batteries is simulated
- **THEN** three flights are flown over the legs the plan assigned to each, each starting with a full battery at the takeoff point, with a battery change between them that is not counted as flight time

#### Scenario: Reserve

- **WHEN** a flight would last longer than the usable battery time
- **THEN** the dashboard flags the battery as into its reserve

### Requirement: Terrain and altitude references

Heights SHALL keep the mission's reference: above the takeoff point. Height
above ground SHALL be computed from the same elevation grid the planner
used for the plan, with the same interpolation, so that at every waypoint
it equals the mission's own figure. Where the ground is not known the
simulation MUST say so and MUST NOT assume a value. The 3D terrain SHALL be
drawn from that grid to scale.

#### Scenario: Agreement with the plan

- **WHEN** a plan computed over an elevation model is simulated
- **THEN** the simulated height above ground at each waypoint equals the mission's, to the rounding

#### Scenario: Terrain following

- **WHEN** a terrain-following plan is simulated
- **THEN** along the flight lines the height above ground stays within the plan's following tolerance of the planned height

#### Scenario: No elevation data

- **WHEN** the plan has no elevation data, or the grid cannot be loaded
- **THEN** the simulation still runs, height above ground is shown as not known, and the 3D ground is flat at the takeoff height and labelled so

#### Scenario: A survey's own model

- **WHEN** the plan's elevation source is a survey of the project
- **THEN** the simulation uses that survey's grid, subject to the same access rules as planning with it

### Requirement: Camera, footprint and coverage

The simulation SHALL show the camera's field of view and ground footprint
from the plan's camera profile, orientation and gimbal pitch at the
simulated height above ground, the photos taken so far, and an estimate of
coverage and overlap over the survey area.

#### Scenario: Footprint

- **WHEN** the aircraft is at the planned height over level ground
- **THEN** the footprint has the size the plan states, and the plan's spacings give the planned front and side overlap

#### Scenario: Rising ground

- **WHEN** the aircraft flies at a constant altitude over rising ground
- **THEN** the footprint and the overlap shrink accordingly, and the lowest overlap matches the plan's own

#### Scenario: Coverage

- **WHEN** the flight is played
- **THEN** the share of the survey area covered grows from zero as photos are taken, and the same coverage is shown on the map and in 3D

### Requirement: Synchronized 2D and 3D

The 2D map and the 3D view SHALL show the same mission at the same
simulated time, from one clock and one sampled state, in every layout and
while playing as well as when paused. The 3D view SHALL offer free,
follow, top-down, first-person and survey-camera views.

#### Scenario: Same moment

- **WHEN** the simulation is playing or has been seeked
- **THEN** the aircraft's position on the map and in the 3D scene are those of the same state

#### Scenario: Cameras

- **WHEN** a camera view is chosen
- **THEN** follow tracks the aircraft, free stays where the member put it, top-down is above the aircraft north up, first-person is at the aircraft looking ahead, and the survey-camera view uses the plan's gimbal pitch and the camera's field of view

#### Scenario: No WebGL

- **WHEN** the browser cannot provide a 3D context
- **THEN** the 3D view says so and the simulation continues on the map

### Requirement: Dashboard

A dashboard SHALL show, for the current moment: what the aircraft is doing,
progress, distance flown, speed, height above takeoff and above ground,
battery estimate, photos taken, area covered, and an elevation profile of
the whole flight with a cursor at the current position.

#### Scenario: Readouts

- **WHEN** the simulation is at a moment on a flight line
- **THEN** the dashboard shows the line, the planned speed, the heights, the battery estimate and the photos and coverage so far, and the profile's cursor is at the distance flown

### Requirement: Estimates are labelled as estimates

Every simulation screen SHALL state that it is an estimate replayed from
the plan and not flight telemetry or a verification of flight safety.
Figures derived from the elevation model or the battery model SHALL be
labelled estimates. The simulated totals SHALL be shown next to the plan's
own, not instead of them. Nothing SHALL be worded as verified, safe or
cleared.

#### Scenario: Notice

- **WHEN** a simulation is open
- **THEN** the notice is visible, and the terrain clearance and battery readouts say they are estimates

#### Scenario: Differences

- **WHEN** the simulated time or clearance differs from the plan's estimate
- **THEN** both are shown side by side

### Requirement: Simulating changes nothing

Simulating MUST NOT modify the mission, the stored plan, its findings or
its export. The only request it makes is a read-only one for the plan's
elevation grid, with the access rules of planning: a member of an
organization, a project of that organization, and for a survey's model a
task the caller may read in that project.

#### Scenario: Plan untouched

- **WHEN** a saved plan is simulated, played and closed
- **THEN** its stored mission, definition and modification time are unchanged, it is not marked as edited, and its export options are the same

#### Scenario: Access

- **WHEN** the elevation grid is requested for a project of another organization, or names a survey of another project or organization
- **THEN** the request is refused, or the survey reads as missing, exactly as when planning

### Requirement: Performance

Building a simulation and sampling it SHALL remain fast for missions up to
the planner's limits (20 000 waypoints, 100 000 photos). The cost of
showing a moment SHALL NOT grow with the size of the mission.

#### Scenario: A large mission

- **WHEN** a mission at the planner's limits is simulated
- **THEN** it is built in about a second, a state is sampled in microseconds, and seeking anywhere is immediate
