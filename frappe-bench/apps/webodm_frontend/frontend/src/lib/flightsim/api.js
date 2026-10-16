// The simulator's one request: the elevation grid the plan was computed over.
//
// Everything else the simulator needs is already in the mission the planner
// returned. The call is read-only on the server (`simulation_terrain`); it is
// a POST because fetching an uncached grid makes an outbound lookup, which
// the platform only allows behind the session's CSRF token.

import { postMethod } from '../api'
import { ensureCsrfToken } from '../session'

/**
 * Resolves `{ terrain, pending, message }`: `terrain` is the grid for
 * `createSimulation` or null (no elevation source, or it could not be
 * loaded - `message` says why). `mode: 'cached'` answers at once and sets
 * `pending` when the grid would have to be fetched.
 */
export async function getSimulationTerrain({ definition, project, mode = 'fetch' }) {
  if (!window.csrf_token) await ensureCsrfToken()
  return postMethod('webodm_core.api.flightplan.simulation_terrain', { definition, project, terrain: mode })
}
