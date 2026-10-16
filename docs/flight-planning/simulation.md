# Flight simulation

Replay a flight plan before flying it: the aircraft follows the planned
mission on the map and over 3D terrain, from takeoff through every flight
line, turn and battery change to the landing, with a dashboard of what it
is doing at each moment.

- [Using it](#using-it)
- [What the simulation is, and is not](#what-the-simulation-is-and-is-not)
- [How it works](#how-it-works)
- [The flight model](#the-flight-model)
- [Camera, footprint and coverage](#camera-footprint-and-coverage)
- [Terrain and heights](#terrain-and-heights)
- [The views](#the-views)
- [Simulation and plan compared](#simulation-and-plan-compared)
- [Architecture](#architecture)
- [Testing](#testing)
- [Limits and what is not done](#limits-and-what-is-not-done)

> **Simulation only.** It is an estimate replayed from the flight plan,
> nominal equipment figures and an elevation model, in still air. It is not
> flight telemetry and it does not verify that the flight is safe:
> obstacles, airspace, wind, weather and the actual aircraft are not
> modelled. The same notice is on the simulator's dock, and every figure
> that comes from the elevation model or the battery model is labelled an
> estimate.

## Using it

Open any plan in the planner and press **Simulate flight** (next to
*Export…*), or press the play button of a plan in the **Flight plans** list.
A plan does not have to be saved: the simulation plays the mission that is
on screen. Nothing is stored and the plan is not changed; **Exit** returns
to planning exactly as it was. Editing the plan while the simulation is
open rebuilds it from the start.

The planner's map stays where it is. The simulator adds a 3D view beside it
and a dock below:

| Part | What it does |
|---|---|
| **Play / pause, stop, restart** | Stop returns to the start; restart plays again from it. Space toggles play while the controls have focus; Home / End jump to the ends. |
| **Timeline** | Drag to any moment. Colours are the phases of the flight (survey lines, turns, transit, takeoff / landing, battery change). On a multi-battery mission the numbers above it jump to the start of each flight. |
| **Speed** | 0.5× to 64×. Speed changes how fast time passes, never what happens at a given time. |
| **2D / Split / 3D** | The map, both, or the 3D view. On a phone the 3D view opens alone. |
| **Coverage** | *Covered so far* tints the ground the photos taken up to now have seen; *Photos per point* colours it by how many photos will see it (1, 2, 3–4, 5–8, 9 or more); *off* hides it. One setting for both views. |
| **Follow on map** | Keeps the 2D map centred on the aircraft until you drag the map. |
| **Dashboard** | What the aircraft is doing (phase, flight line, flight number), progress, distance flown, ground and vertical speed, height above the takeoff point and elevation, height above ground, battery, photos taken, area covered, heading / pitch / roll. |
| **Elevation profile** | Ground and aircraft against the ground distance flown, for the whole flight including transit, turns and landing, with the lowest clearance marked. The cursor follows the playback; click or drag on the chart to go there. |
| **Details** | The simulated totals next to the plan's estimates, the coverage figures, and what the simulation assumed. |

In the 3D view:

| Camera | View |
|---|---|
| **Follow** | Orbits the aircraft as it flies; drag to look around it, scroll to zoom. |
| **Free** | The whole site; the camera stays where you put it. |
| **Top-down** | Straight down on the aircraft, north up; scroll to change the height. |
| **FPV** | From the nose, the horizon banking with the aircraft. |
| **Camera** | Through the survey camera: the plan's gimbal pitch and the camera's real field of view, with the frame of one photo drawn. |

The layers button switches the flight path, line ends, photo positions and
the camera's view cone on and off. The ground is the plan's elevation model
draped with the same basemap as the map (and the project's orthophoto when
it is switched on in the map's options); heights are drawn to scale, with
no vertical exaggeration.

## What the simulation is, and is not

| It is | It is not |
|---|---|
| The planner's mission, flown: its waypoints, legs, battery flights, speeds, heights and photo counts | A second planner. It lays out no line and decides no altitude. |
| Deterministic: the same plan gives the same flight, frame for frame, however you get to a moment | A physics simulation. There is no wind, no air density, no mass, no controller. |
| An estimate from published nominal figures and an elevation model | Telemetry, a log, or a prediction of what a particular aircraft will do |
| A way to see the mission and its numbers in time and space | A check that the flight is safe, legal or clear of obstacles |

The plan's own findings and its "what was and was not checked" table are
unchanged by it and remain the place where limits are reported.

## How it works

```
plan (saved or on screen)                         server
  mission  ───────────────┐                       simulation_terrain(definition)
  definition ─────────────┼─────────────────────►   the planner's elevation grid,
                          │   one read-only call     same source, extent and cache
                          ▼
            createSimulation(mission, grid)        lib/flightsim (no Vue / Leaflet / three.js)
              trajectory   timeline of motion, photo events
              camera       field of view, footprint
              coverage     raster of what the photos see
              profile      ground and aircraft vs distance
                          │
            useMissionSim  one clock ── sample(t) ── one state object per frame
                          │
          ┌───────────────┼────────────────┬─────────────────┐
          ▼               ▼                ▼                 ▼
     2D map layer     3D scene         dashboard          profile
     (Leaflet)        (three.js)
```

1. **Built once.** When the simulation opens, the mission is turned into a
   timeline of motion pieces with closed-form speed profiles
   (`trajectory.js`). Building a mission at the planner's limits (20 000
   waypoints, 100 000 photos) takes about half a second.
2. **Sampled per frame.** "Where is the aircraft at time *t*" is a binary
   search and a few arithmetic operations - about 7 µs, independent of the
   size of the mission. Nothing is integrated step by step, so scrubbing,
   jumping and playing at any speed all land on the same state.
3. **One clock, one state.** `useMissionSim` owns the clock. Each frame it
   samples the state once and hands that same object to every view. The 2D
   map and the 3D scene have no time of their own and cannot drift apart.

The mission is never written to (the tests freeze it and compare it before
and after), nothing is sent to the server except the request for the
elevation grid, and export is untouched.

## The flight model

The model is the one behind the planner's own time estimate
(`mission.py`), so the two agree where they describe the same thing.

| | Value | Source |
|---|---|---|
| Acceleration and braking along track | 2.5 m/s² | planner (`ACCEL_MS2`) |
| Yaw at a line end | at most 2 s (45 °/s) | planner (`TURN_SECONDS`) |
| Ground time before lift-off and after touchdown | 10 s + 10 s | planner (`GROUND_OVERHEAD_S`, 20 s) |
| Climb / descent rate | the aircraft's published rates; 3 / 2 m/s when not published (and the simulation says so) | drone profile / planner defaults |
| Speed on lines and turns, on transit | the plan's speed and transit speed, capped at the aircraft's mission speed limit (and the simulation says so) | plan / drone profile |
| Vertical acceleration | 2 m/s² | simulator |
| Battery change between flights | 60 s on the ground, not counted as flight time | simulator |

**Multirotor.** Pre-flight, a vertical climb to the first waypoint's height,
level transit, then for each flight line: yaw onto the line, accelerate,
fly, brake to a stop at its end, yaw, hop to the next line. Along a line
the aircraft flies straight between the plan's waypoints, climbing and
descending as they say (terrain following). At a bend inside a line (a
corridor) it slows instead of stopping: the speed through a bend of angle θ
is limited to `a·τ / (2·sin(θ/2))` with τ = 0.8 s, and the heading swings
through the bend. Where the planned speed would need a climb or descent
faster than the aircraft's rate, the simulation slows down on that stretch
and notes how often it had to. Return: climb to the return height if it is
higher, level transit home, vertical descent, touchdown. *Land in place*
descends where the survey ends; *hover* is shown for five seconds before
the return the plan budgets.

**Fixed-wing.** No stops. Lines are joined by Dubins paths - the shortest
curves of the aircraft's turn radius between two positions and headings -
flown at the planned speed and banked accordingly. The aircraft climbs
along its way out and descends along its way home, circling over the
takeoff point when that way is too short to gain or lose the height. A
profile without a turn radius gets one from a 30° bank at the planned speed
(and the simulation says so).

**VTOL.** Vertical takeoff and landing as a multirotor, forward flight and
turns as a fixed-wing.

**Attitude** (pitch and roll) is derived from how the velocity changes
around the sampled instant: a multirotor leans into its acceleration and
into the airflow, an aircraft in a turn banks by `atan(v·ω / g)`. It is for
the picture; nothing is computed from it.

**Battery** is the planner's model: time in the air over the rated
endurance (`max_flight_time_min × endurance_factor`), per flight. The
reserve is marked on the gauge, and a flight that would run into it is
flagged.

## Camera, footprint and coverage

- **Photo events** are placed where the planner counts them and where the
  *photo at every waypoint* export puts its waypoints: every photo spacing
  from the start of each leg, `legs[i].photos` of them. The tests compare
  the positions with the exporter's.
- **Field of view** comes from the camera profile (sensor size and focal
  length) and the plan's orientation; the *Camera* view uses it as is.
- **Footprint** is the planner's similar-triangles geometry at the height
  above ground the aircraft is simulated at, turned to the heading and
  tilted by the plan's gimbal pitch. The ground under a photo is taken as
  level at the elevation below the aircraft.
- **Coverage** burns every footprint into a raster over the site (at most
  512 cells on its longer side, regular in the map's projection). Each
  cell records the first photo that saw it and how many see it. From that:
  the share of the survey area covered, in at least 3 and 5 photos, photos
  per point, and the overlap the plan's spacings give over the highest
  ground (which matches the planner's own `side_overlap_min` /
  `front_overlap_min`). The same raster is the overlay in both views.

## Terrain and heights

Every height in a mission is **relative to the takeoff point**, and the
simulation keeps that reference: the aircraft's `alt` is the mission's.
Height above ground is

```
agl = ground(takeoff) + alt − ground(under the aircraft)
```

with both ground values from the **same elevation grid the planner used**
(`simulation_terrain` returns it; same source, extent and cache entry) and
the same bilinear interpolation. The datum of the source cancels, as in the
planner. At every waypoint the simulated height above ground equals the
mission's `agl` to the rounding (tested); between waypoints the simulation
samples the grid more finely than the planner's analysis, so its lowest
clearance can be slightly lower than the plan's figure - both are shown.

Unknown ground stays unknown. Outside the grid, in a hole of it, or
without elevation data at all, the dashboard says *not known* rather than
assuming a height; on the lines themselves the ground the mission recorded
at its waypoints is used when the grid is not available. In 3D the terrain
is drawn from the grid's nodes without smoothing or exaggeration; holes are
filled in the drawing only, and without elevation data the ground is a flat
sheet at the takeoff height, labelled as such.

## The views

- **2D** (`mapLayer.js`): on the planner's own Leaflet map, above its
  route: the aircraft turned to its heading, the track flown, the camera's
  footprint (it lights up for a moment at each exposure), the coverage
  raster as an image overlay. The flown track is kept in chunks so that a
  long mission costs no more per frame than a short one. While playing,
  the coverage overlay is repainted at most 20 times a second; a pause, a
  jump or the end of the flight always shows the exact moment.
- **3D** (`scene.js`, three.js): terrain mesh from the elevation grid with
  the basemap draped on it, the path coloured by phase with the flown part
  highlighted, photo positions, the survey boundary laid on the ground, the
  aircraft with its view cone and footprint, a line down to the ground. The
  coverage raster is blended into the terrain's own material. A mission of
  48 000 photos and 35 batteries is drawn in 26 draw calls.

Why three.js and not a globe engine such as CesiumJS: three.js is already
shipped (model and point cloud viewers) and loads on demand; a mission is
at most 60 km across, so a local metric scene in the planner's own frame is
exact where a globe adds nothing; the terrain has to be the planner's grid,
not a third-party tileset with its own datum; and imagery loads as plain
images, which the deployment's content security policy already allows for
any https origin. Cesium would need its workers and assets served, script
access to tile hosts (`connect-src`) and normally an access token - changes
to deployment and policy for no gain in accuracy at this scale. **No
deployment, Compose, Helm or CSP change is needed for the simulator.**

## Simulation and plan compared

The dock's *Details* show both. Where they differ, and why:

| | Typical difference | Reason |
|---|---|---|
| Distance (multirotor) | under a metre per flight | same route |
| Flight time (multirotor) | 1–4 % longer | the simulation accelerates and brakes on the transit and on short hops between lines, and climbs to the return height; the plan's estimate does not count those |
| Photos, batteries | none | taken from the plan |
| Lowest clearance | equal or slightly lower | finer sampling of the same grid, and the transit and turns as flown |
| Distance and time (fixed-wing, VTOL) | several % either way | the plan approximates a turn as a straight hop plus half a circle; the simulation flies the curve |

A difference is information, not an error in either; the plan's figures
remain the ones the findings and the export are based on.

## Architecture

| Path | Role |
|---|---|
| `webodm_core/flightplan/simulation.py` | `terrain_payload`: the planner's `TerrainModel` as JSON, nodes unchanged, missing nodes kept missing. No Frappe. |
| `webodm_core/api/flightplan.py` → `simulation_terrain` | The one endpoint: organization member, project of that organization, a task the caller may read for a survey's DSM / DTM; POST only; uses the planner's cache and lookup budget; writes nothing. |
| `frontend/src/lib/flightsim/frame.js`, `terrain.js` | Ports of `geo.LocalFrame` and `TerrainModel.elevation`, tested against values from the Python originals. |
| `…/kinematics.js`, `dubins.js` | Trapezoidal speed profiles (closed form both ways), speed planning over a polyline, Dubins paths. |
| `…/trajectory.js` | `buildTrajectory(mission, { terrain })` → timeline, photo events, `sample(t)`. |
| `…/camera.js`, `coverage.js`, `profile.js` | Footprint and overlap; coverage raster; elevation profile. |
| `…/clock.js`, `index.js`, `describe.js`, `api.js` | The clock; `createSimulation`; dashboard wording; the terrain request. |
| `…/sceneGeometry.js`, `cameraRig.js`, `imagery.js` | Everything the 3D view draws, as typed arrays and poses - tested without a GPU. |
| `…/scene.js`, `mapLayer.js` | The three.js scene and the Leaflet layer: thin shells over the above. |
| `frontend/src/composables/useMissionSim.js` | Controller: simulation, clock, shared state, coverage canvas. |
| `frontend/src/components/flightsim/` | `FlightSimulator` (a light shell around the planner's map: all the planner page loads up front), `SimSession` (an open simulation: controller, map layer, dock; loaded when a simulation is first opened), `SimControls`, `SimDashboard`, `SimProfile`, `SimView3D` (three.js; a further on-demand chunk). |

Nothing in `lib/flightsim/` imports Vue, the planning client or the
planning engine; the only thing it knows about a mission is the shape of
the dict `plan_mission` returns. The planner page changed by one wrapper
around its map, one button and three small hooks, and its script grew by
about 3 kB: the simulator (71 kB) and the 3D view (44 kB, plus three.js,
shared with the model viewer) are separate chunks fetched on demand.

`simulation_terrain(definition, project, terrain="fetch")` returns
`{terrain, pending, message, elevation_source}`; `terrain` is `{south,
west, north, east, rows, cols, values (rows south to north, null where
unknown), spacing_m, min_m, max_m, coverage, source, datum_note}` or null.

## Testing

| Suite | What it covers | Run |
|---|---|---|
| `lib/flightsim/*.test.js` (288) | Projection and terrain sampling against the planner's values; speed profiles; Dubins paths; the route flown is the route planned (every waypoint, order, heights); photo events against the exporter's exposure positions; speed, climb and acceleration limits; multirotor, fixed-wing and VTOL behaviour; agreement with the plan's estimates; multi-battery flights and battery model; height above ground against the mission's own; determinism and that the mission is never modified; footprint, overlap and coverage; the clock; profile; dashboard wording; 3D geometry, georeferencing of imagery and coverage, camera poses; the map layer; performance at the planner's limits | `npm test` |
| `composables/useMissionSim.test.js` (18), `components/flightsim/FlightSimulator.test.js` (23), `SimView3D.test.js` (11) | Opening, terrain request and its failure modes, playback controls, that map and 3D are handed the same state object each frame, layouts, coverage, closing, rebuilding on a plan change; the 3D pane's cameras, layers, imagery status and its message when WebGL is missing | `npm test` |
| `pages/FlightPlanner.simulation.test.js` (9), `FlightPlans.test.js` (+1) | The button, simulating an unsaved and a saved plan, `?simulate=1`, that the planner is unchanged afterwards and nothing is saved or recomputed | `npm test` |
| `webodm_core/flightplan/test_simulation.py` (7, no Frappe) | The grid payload: nodes unchanged, holes kept, interpolation from it equals the planner's, mission `agl` reproduced | `python -m pytest webodm_core/flightplan/test_simulation.py` |
| `webodm_core/api/test_flightplan.py` → `TestSimulationTerrain` (11, Frappe) | Same grid and cache as the planner, cached / pending, no source, failed lookup, read-only, organization and project scoping, a survey of another project reads as missing, lookup budget, POST only | `frappe --site <site> run-tests --app webodm_core --module webodm_core.api.test_flightplan` |
| `e2e/flight-sim.e2e.mjs` (69 checks, 73 with a survey's DSM) | The whole feature in a real browser with WebGL against a running stack | see `frontend/e2e/README.md` |

The fixture `src/lib/__fixtures__/flightsim.json` is produced by the real
planner (`make_flightsim_fixture.py`): eight missions (flat, terrain
following over a ridge, constant altitude over a slope with a hole in the
grid, three batteries, a corridor, land in place, fixed-wing, VTOL), their
elevation grids, and reference values from the Python code for the frame,
the terrain interpolation and the exporters' exposure positions.

Twenty-eight deliberate bugs (swapped axes, a shifted photo, a battery
computed from the wrong time, a dropped climb limit, a coverage raster
upside down, a clock that ignores its speed, views given separate copies of
the state, a simulation that writes to the mission, …) were each
introduced once; each failed the suite.

### What was exercised during development

- **Real services**: a 700 × 480 m block in the hills north of Bandung
  planned with terrain following against the public Open Topo Data (an
  18 × 22 node grid with 241 m of relief), simulated over that grid with
  Esri imagery draped on it:
  1202 s simulated against 1172 s planned, 9502 m against 9501.6 m, 99.5 m
  above ground mid-line against 100 m planned.
- **A survey's DSM end to end**: a plan whose elevation source is a task's
  DSM (Frappe → geospatial `/raster/sample` → a 0.25 m GeoTIFF with a 35 m
  structure in it): an 89 × 110 node grid drawn node for node (20 806
  triangles), the structure visible in the profile as the lowest clearance
  (77 m simulated, 80 m planned), 80.0 m above ground mid-line against 80 m
  planned, one stretch slowed to keep the climb within the aircraft's
  rate, and the project's orthophoto draped on the 3D terrain in register
  with the 2D map.
- **Frame rate** on a 2015 integrated GPU (Intel HD 520, headless Chrome
  with hardware WebGL): 57–60 frames per second in the split, 2D and 3D
  layouts on an ordinary mission, and at 1× and 8× on a mission of 48 233
  photos and 35 batteries. That mission opened in 1.6–2.9 s; a jump
  anywhere in its timeline, both views updated, took 1.9 ms. At 64× it
  played at 28–50 frames per second (once 16) across runs on a machine
  that was busy with other work: at that speed the cost is the browser
  compositing the coverage overlay and the growing track, not the
  simulation - the main thread was idle more than half the time.
- **Without a GPU** (software WebGL) the browser composites a page of this
  size at one or two frames per second whatever is drawn; the checks pass
  there too, but it is not a way to judge smoothness.
- **The browser run** (`e2e/flight-sim.e2e.mjs`, 69 checks, 73 with
  `E2E_DSM_TASK`; software and hardware WebGL; online and with
  `E2E_OFFLINE=1`): from the plan list into the simulation, wording,
  playback, scrubbing, the 2D map and the 3D scene on the same state while
  paused and while playing, five cameras, layouts, coverage, the profile,
  editing while simulating, three batteries, a fixed-wing, landing in
  place without elevation data, a large mission, a phone viewport, and
  that the stored plan and its export options are identical afterwards.

## Limits and what is not done

- **Not validated against a flown mission.** No simulated flight was
  compared with a real aircraft's log. Times and battery figures are the
  planner's nominal model replayed, not measurements.
- **Still air, nominal aircraft.** No wind, temperature, payload, battery
  age or controller behaviour; a real aircraft's cornering, braking and
  climb differ by model and firmware.
- **Turn models are generic.** A multirotor stops at line ends (the
  planner's assumption); flight apps that fly coordinated turns will be
  faster. Fixed-wing turns are Dubins curves at the published radius, not
  the vendor app's own turn pattern.
- **Footprints assume level ground under each photo.** Exact for a nadir
  photo over gentle terrain; a simplification for an oblique camera or
  steep ground. Coverage is where the camera points, not image quality.
- **The terrain is the planner's grid**: at most 400 nodes from a global
  model, 10 000 from a survey's DSM / DTM. It is as coarse as the data
  the plan was made with, and knows nothing of trees, buildings or wires
  unless the source is a surface model.
- **Exports are not simulated per file.** Routes split by a format's
  waypoint cap, and what a particular flight app does at a waypoint, are
  not shown; the simulation follows the mission, one flight per battery.
- **The battery change** is a fixed minute for the picture.
- **No recording or sharing** of a simulation, and no comparison of two
  plans side by side.
