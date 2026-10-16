# Tasks

## 1. Simulator library (no Vue / Leaflet / three.js)

- [x] 1.1 `frame.js` (port of `geo.LocalFrame`, angles, Web-Mercator), `terrain.js` (port of `TerrainModel.elevation`); tests against Python reference values
- [x] 1.2 `kinematics.js` (trapezoidal profiles both ways, speed planning, corner speed), `dubins.js`
- [x] 1.3 `trajectory.js`: timeline for multirotor, fixed-wing and VTOL, battery flights and changes, photo events, `sample(t)`, attitude, battery, height above ground
- [x] 1.4 `camera.js`, `coverage.js`, `profile.js`
- [x] 1.5 `clock.js`, `index.js` (`createSimulation`, notice, summary), `describe.js`
- [x] 1.6 Fixture from the real planner (`make_flightsim_fixture.py` → `flightsim.json`), `syntheticMission.js`

## 2. Platform

- [x] 2.1 `flightplan/simulation.py` (`terrain_payload`); `test_simulation.py`
- [x] 2.2 `api.flightplan.simulation_terrain` (read-only, POST, planner's cache / budget / permissions); `TestSimulationTerrain`

## 3. Views

- [x] 3.1 `sceneGeometry.js`, `cameraRig.js`, `imagery.js` (tested without a GPU)
- [x] 3.2 `scene.js` (three.js), `mapLayer.js` (Leaflet)
- [x] 3.3 `useMissionSim.js`; `FlightSimulator` (shell), `SimSession` (lazy), `SimControls`, `SimDashboard`, `SimProfile`, `SimView3D` (lazy)
- [x] 3.4 Planner: *Simulate flight*, wrapper around the map, `?simulate=1`; plan list action

## 4. Verification

- [x] 4.1 Unit and component tests (350 new frontend, 7 + 11 backend); 28 seeded bugs caught
- [x] 4.2 `e2e/flight-sim.e2e.mjs`: 69 checks (73 with a survey DSM), software and hardware WebGL, online and offline
- [x] 4.3 Real elevation service and imagery; a survey's DSM through the geospatial service with its orthophoto draped in 3D
- [x] 4.4 Full `webodm_core` and frontend suites; the existing planner e2e

## 5. Docs

- [x] 5.1 `docs/flight-planning/simulation.md`; README, architecture, e2e README, SPEC.md, TODO.md, AGENTS.md; `openspec/specs/flight-simulation/spec.md`
