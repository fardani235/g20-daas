# Design

## Context

See `proposal.md`. The full record - the flight model, the camera and
coverage model, altitude references, evidence and limits - is
`docs/flight-planning/simulation.md`; this is the short form of the
decisions.

Shaping facts: the planner is a pure server-side function whose mission
dict is what the map draws, the plan stores and the exporters read; the
planner interpolates terrain from a small cached grid that the browser
never sees; the SPA already ships Leaflet and three.js; the deployment's
content security policy allows images from any https origin and script
connections only to the platform itself.

## Decisions

### D1. Simulate in the browser, from the mission as returned

Playback needs a state per animation frame, so sampling has to be local
whatever else is decided. Building the timeline locally as well keeps the
simulator to one implementation, lets it play an unsaved preview, and adds
nothing to the server but the terrain grid. Rejected: a server-built
trajectory sampled by the client (two halves to keep consistent, a payload
that grows with the mission, and a round trip on every edit). The price - a
JavaScript port of the frame and the terrain interpolation - is paid for by
tests against reference values from the Python originals.

### D2. Independent of the planning engine

`lib/flightsim/` knows the shape of the mission dict and nothing else. It
imports neither the planning client nor Vue, never writes to the mission
(tests freeze it), and derives nothing the planner already decided: route,
heights, flights and photo counts are read, not recomputed.

### D3. The planner's own terrain grid

Height above ground must agree with the plan's. A different or resampled
terrain would contradict the mission's `agl` and its clearance findings on
the same screen. `simulation_terrain` returns the grid `preview` uses -
same source, extent, cache key, budget and permissions - with nodes
unchanged and missing nodes kept missing.

### D4. Closed-form motion, sampled

Each moving piece has a trapezoidal speed profile with closed forms in both
directions. A sample is a binary search plus arithmetic: deterministic,
independent of frame rate and history, and as cheap at 20 000 waypoints as
at 20. Rejected: stepwise integration (state depends on the path taken to a
time; scrubbing would need replay).

### D5. The planner's motion model, not a physics model

Constants are the planner's (2.5 m/s², 2 s turns, 20 s on the ground,
published climb rates), so the simulation and the estimate describe the
same flight and their differences have stated causes. A richer aircraft
model would look more authoritative and be less defensible: nothing in the
catalog supports it.

### D6. One clock, one state object

The controller samples once per frame and hands the same object to the map
layer, the scene and the panels. Views hold no time. Synchronization is
then structural rather than something to keep in tune.

### D7. three.js, not CesiumJS

A mission is at most 60 km across: a local metric scene in the planner's
frame is exact. The terrain must be the planner's grid, not a tileset.
three.js is already in the bundle and loads on demand; imagery arrives as
`<img crossorigin>` tiles composed on a canvas, which the existing CSP
allows. Cesium would add megabytes of workers and assets, need `connect-src`
to tile hosts and usually a token - deployment and policy changes for no
gain in accuracy at this scale.

### D8. Geometry outside the renderer

Terrain mesh, texture coordinates, path, camera poses and the tile plan are
plain typed arrays and numbers from modules without three.js, so
georeferencing and synchronization are tested without a GPU; the scene and
the map layer are thin.

### D9. Coverage as one raster in the map's projection

Footprints are burned once into a raster that is linear in Web-Mercator.
The same canvas is the Leaflet overlay and the terrain's texture; progress
during playback is a comparison per cell, not a re-rasterization.

### D10. Estimates say so; the plan stays the authority

The notice is on the dock; elevation- and battery-derived readouts are
labelled; unknown ground is shown as unknown; simulated totals are shown
beside the plan's. Findings and export remain the planner's.

## Risks

- **Mistaken for telemetry or a safety check.** Mitigated by wording and by
  tests that assert the wording; not eliminated.
- **Not validated against flown missions.** Stated in the limits.
- **Software WebGL** composites slowly; the checks pass there, smoothness
  needs a GPU.
