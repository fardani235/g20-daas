// Flight simulator: a computed mission, replayed.
//
// `createSimulation(mission, terrainGrid)` is the one object the views
// share: the timeline of the flight (`trajectory`), the camera geometry,
// the coverage raster and the elevation profile, all built once from the
// mission the planner returned and the elevation grid it was planned over.
// `simulation.sample(t)` is the state every view draws.
//
// The package has no Vue, no Leaflet and no three.js in it, and it does not
// import the planning client: it can be tested, and reasoned about, on its
// own. It never writes to the mission (the tests freeze it) and it sends
// nothing to the server. The views live in `components/flightsim/`,
// `mapLayer.js` (Leaflet) and `scene.js` (three.js).

import { cameraModel, footprintCorners, footprintSize, overlapAt } from './camera'
import { buildCoverage } from './coverage'
import { buildProfile } from './profile'
import { createTerrain } from './terrain'
import { PHASE_INFO, buildTrajectory } from './trajectory'

export { SimulationError, PHASE, PHASE_INFO, DYNAMICS } from './trajectory'
export { SPEEDS, createClock } from './clock'

/**
 * What every simulation screen says about itself. The wording matters: this
 * is a model of the plan, not a record of a flight and not a safety check.
 */
export const SIMULATION_NOTICE = (
  'Simulation only - an estimate replayed from the flight plan, nominal equipment figures and an '
  + 'elevation model, in still air. It is not flight telemetry and it does not verify that the flight '
  + 'is safe: obstacles, airspace, wind, weather and the actual aircraft are not modelled.'
)

/**
 * Build the simulation of `mission`. `terrainGrid` is the payload of
 * `simulation_terrain` (or null: no elevation data). Throws
 * `SimulationError` when the mission has no route.
 */
export function createSimulation(mission, terrainGrid = null, options = {}) {
  const terrain = createTerrain(terrainGrid)
  const trajectory = buildTrajectory(mission, { terrain, options })
  const camera = cameraModel(mission)
  const coverage = buildCoverage(trajectory, camera, { maxSide: options.coverageSide || 512 })
  const profile = buildProfile(trajectory)
  const footprint = new Float64Array(8)

  return {
    mission,
    terrain,
    trajectory,
    camera,
    coverage,
    profile,
    duration: trajectory.duration,
    notes: trajectory.notes,
    /** The state of the flight at `t` seconds; see `trajectory.sample`. Adds coverage and camera figures. */
    sample(t, out = {}) {
      trajectory.sample(t, out)
      out.coverage = coverage ? coverage.fractionAt(out.photos) : null
      if (camera) {
        const size = footprintSize(camera, out.aglAssumed)
        out.gsdCm = size.gsdCm
        out.footprintAcross = size.across
        out.footprintAlong = size.along
      } else {
        out.gsdCm = out.footprintAcross = out.footprintAlong = null
      }
      return out
    },
    /** Ground footprint of the camera for a sampled state: 4 corners `[x0, y0, ...]` in the local frame, or null. */
    footprintAt(state, out = footprint) {
      if (!camera || !state.airborne || !(state.aglAssumed > 0.5)) return null
      return footprintCorners(camera, state.x, state.y, state.aglAssumed, state.heading, out)
    },
    /** Front and side overlap the plan's spacings give at the state's height above ground. */
    overlapAt: state => (camera ? overlapAt(camera, state.aglAssumed) : null),
    summary: summarize(trajectory, coverage, profile, mission),
  }
}

/**
 * The simulation's totals next to the planner's own estimates, so a
 * difference between the two is visible instead of silently replacing one
 * figure with another.
 */
export function summarize(trajectory, coverage, profile, mission) {
  const stats = mission.stats || {}
  return {
    flightTime: trajectory.flightTime,
    duration: trajectory.duration,
    distance: trajectory.distance,
    photos: trajectory.photos.count,
    flights: trajectory.flights.length,
    planned: {
      flightTime: stats.total_duration_s ?? null,
      distance: stats.total_distance_m ?? null,
      photos: stats.photo_count ?? null,
      flights: stats.flights ?? null,
    },
    lowestClearance: profile.lowest ? profile.lowest.clearance : null,
    plannedLowestClearance: mission.terrain?.clearance_min_m ?? null,
    coveredPercent: coverage ? coverage.stats.coveredPercent : null,
    exceedsUsable: trajectory.flights.some(f => f.exceedsUsable),
  }
}

/** Label of a phase id. */
export const phaseLabel = phase => PHASE_INFO[phase]?.label || ''
