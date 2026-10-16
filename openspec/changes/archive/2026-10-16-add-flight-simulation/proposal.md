# Proposal: Flight simulation

## Why

A flight plan is a list of lines and a table of numbers. Whether the
mission makes sense in space and time - where the aircraft is when the
battery is half used, how close the third line comes to the ridge, what the
camera actually sees over the high ground, how a fixed-wing gets from one
line to the next - is left to the pilot's imagination until the day of the
flight. Replaying the plan answers those questions at the desk.

The risk is the same as with the plan itself, sharper: an animated aircraft
looks like a recording. The simulation must be unmistakably an estimate
replayed from the plan, must not become a second source of truth beside the
planner, and must not touch the plan.

## What Changes

- **Simulator library** `webodm_frontend/frontend/src/lib/flightsim/`,
  with no Vue, Leaflet, three.js or planner dependency: the planner's local
  frame and terrain interpolation ported and tested against the Python
  originals; closed-form speed profiles; Dubins turns; a timeline built
  from the mission (`buildTrajectory`) with `sample(t)`; photo events;
  camera footprint; coverage raster; elevation profile; a clock.
- **One read-only endpoint** `webodm_core.api.flightplan.simulation_terrain`
  and a pure helper `flightplan/simulation.py`: the elevation grid the
  planner used for a definition, with the planner's cache, budget and
  access rules.
- **Views**: a Leaflet layer on the planner's own map; a three.js scene
  (terrain from the grid, basemap imagery, path, aircraft, footprint,
  coverage; free / follow / top-down / FPV / camera views), loaded on
  demand; transport controls and timeline; dashboard; elevation profile.
  One controller (`useMissionSim`) feeds them the same state each frame.
- **Planner integration**: a *Simulate flight* button, the simulator
  wrapped around the planner's map, a *Simulate* action in the plan list.

## Impact

- New code only, apart from: one endpoint and one import in
  `api/flightplan.py`; a wrapper, a button and three small hooks in
  `FlightPlanner.vue`; one action in `FlightPlans.vue`.
- No new dependency (three.js and Leaflet are already shipped), no new
  service, no configuration, no change to Docker Compose, Helm, the
  content security policy or the Frappe image.
- No change to `plan_mission`, the mission's shape, the findings or the
  exporters.
