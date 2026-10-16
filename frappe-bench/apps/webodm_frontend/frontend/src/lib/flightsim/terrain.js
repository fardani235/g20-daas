// The ground under a simulated flight.
//
// The planner interpolates elevations from a small grid over the site
// (`webodm_core/flightplan/terrain.py`); `simulation_terrain` hands the
// simulator that same grid. `elevation` here is `TerrainModel.elevation`
// rule for rule - bilinear, missing nodes skipped, nothing outside the grid
// - so a height above ground computed in the browser equals the one in the
// mission. Unknown ground stays unknown (`null`): the simulator never
// invents terrain to make a readout look complete.

/**
 * A terrain sampler over the grid payload of `simulation_terrain`
 * (`{ south, west, north, east, rows, cols, values, source, spacing_m }`,
 * rows south to north). Returns null for a missing or malformed grid.
 */
export function createTerrain(grid) {
  if (!grid || !(grid.rows >= 2) || !(grid.cols >= 2) || !Array.isArray(grid.values)) return null
  const { south, west, north, east, rows, cols } = grid
  if (!(north > south) || !(east > west) || grid.values.length !== rows) return null

  // One flat array; NaN marks a node without data.
  const z = new Float64Array(rows * cols)
  let min = Infinity
  let max = -Infinity
  let known = 0
  for (let r = 0; r < rows; r++) {
    const row = grid.values[r]
    if (!Array.isArray(row) || row.length !== cols) return null
    for (let c = 0; c < cols; c++) {
      const v = row[c]
      if (v === null || v === undefined || !Number.isFinite(v)) {
        z[r * cols + c] = NaN
      } else {
        z[r * cols + c] = v
        known++
        if (v < min) min = v
        if (v > max) max = v
      }
    }
  }

  function elevation(lng, lat) {
    if (!(lat >= south && lat <= north && lng >= west && lng <= east)) return null
    const fy = (lat - south) / (north - south) * (rows - 1)
    const fx = (lng - west) / (east - west) * (cols - 1)
    const r0 = Math.min(Math.floor(fy), rows - 2)
    const c0 = Math.min(Math.floor(fx), cols - 2)
    const ty = fy - r0
    const tx = fx - c0
    const i = r0 * cols + c0
    const v00 = z[i]
    const v01 = z[i + 1]
    const v10 = z[i + cols]
    const v11 = z[i + cols + 1]
    const w00 = (1 - tx) * (1 - ty)
    const w01 = tx * (1 - ty)
    const w10 = (1 - tx) * ty
    const w11 = tx * ty
    let sum = 0
    let weight = 0
    let plain = 0
    let count = 0
    if (v00 === v00) { sum += v00 * w00; weight += w00; plain += v00; count++ }
    if (v01 === v01) { sum += v01 * w01; weight += w01; plain += v01; count++ }
    if (v10 === v10) { sum += v10 * w10; weight += w10; plain += v10; count++ }
    if (v11 === v11) { sum += v11 * w11; weight += w11; plain += v11; count++ }
    if (!count) return null
    // Exactly on a corner whose own value is missing.
    if (weight <= 1e-9) return plain / count
    return sum / weight
  }

  return {
    south, west, north, east, rows, cols,
    values: z,
    spacing: Number(grid.spacing_m) || 0,
    source: grid.source || null,
    min: known ? min : null,
    max: known ? max : null,
    coverage: known / (rows * cols),
    elevation,
    /** Elevation of node `(row, col)`, NaN where there is no data. */
    node: (r, c) => z[r * cols + c],
  }
}

/** Whether the grid's source is a surface model (tree tops, roofs) rather than bare ground. */
export const isSurfaceModel = terrain => terrain?.source?.surface === 'surface'

/** One line saying what the simulated ground is, for the dashboard. */
export function terrainLabel(terrain) {
  if (!terrain) return 'No elevation data: flat ground at the takeoff height is assumed'
  const src = terrain.source || {}
  const res = src.resolution_m ? `, about ${formatMetres(src.resolution_m)} resolution` : ''
  const acc = src.vertical_accuracy_m ? `, ±${formatMetres(src.vertical_accuracy_m)} nominal` : ''
  return `${src.label || 'Elevation model'}${res}${acc}`
}

function formatMetres(v) {
  const n = Number(v)
  return n < 1 ? `${Math.round(n * 100)} cm` : `${Math.round(n * 10) / 10} m`
}
