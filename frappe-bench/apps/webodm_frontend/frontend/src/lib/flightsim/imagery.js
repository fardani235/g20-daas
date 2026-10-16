// Map imagery for the 3D ground.
//
// The 3D view drapes the same basemap the 2D map shows over the terrain. It
// does so with what the platform already allows: tiles are requested as
// images (`<img crossorigin>`), which the deployment's content security
// policy permits for any https origin, and composed on a canvas that
// becomes the terrain's texture. No tile is fetched by script, no extra
// service, key or policy change is needed, and a tile that fails to load
// simply leaves the ground its plain colour.

import { latOfMercatorY, mercatorX, mercatorY } from './frame'

export const TILE_SIZE = 256

/**
 * Which tiles cover `bounds` (`{ west, south, east, north }`), at the
 * deepest zoom that needs at most `maxTiles` of them.
 *
 * Returns `{ z, x0, y0, nx, ny, width, height, box }`: the tile range, the
 * mosaic's pixel size and the geographic box the mosaic covers (whole
 * tiles, so a little more than `bounds`).
 */
export function tilePlan(bounds, { maxTiles = 36, maxZoom = 19, minZoom = 1 } = {}) {
  const mx0 = mercatorX(bounds.west)
  const mx1 = mercatorX(bounds.east)
  const my0 = mercatorY(bounds.north)
  const my1 = mercatorY(bounds.south)
  const eps = 1e-12
  let best = null
  for (let z = minZoom; z <= maxZoom; z++) {
    const n = 2 ** z
    const x0 = Math.floor(mx0 * n)
    const x1 = Math.floor(mx1 * n - eps)
    const y0 = Math.floor(my0 * n)
    const y1 = Math.floor(my1 * n - eps)
    const nx = Math.max(1, x1 - x0 + 1)
    const ny = Math.max(1, y1 - y0 + 1)
    if (best && nx * ny > maxTiles) break
    best = { z, x0, y0, nx, ny }
  }
  const n = 2 ** best.z
  return {
    ...best,
    width: best.nx * TILE_SIZE,
    height: best.ny * TILE_SIZE,
    box: {
      west: (best.x0 / n) * 360 - 180,
      east: ((best.x0 + best.nx) / n) * 360 - 180,
      north: latOfMercatorY(best.y0 / n),
      south: latOfMercatorY((best.y0 + best.ny) / n),
    },
  }
}

/** URL of one tile from a Leaflet-style template (`{z}`, `{x}`, `{y}`, optional `{s}`). */
export function tileUrl(template, z, x, y) {
  const n = 2 ** z
  const wrapped = ((x % n) + n) % n
  return template
    .replace('{s}', 'abc'[(wrapped + y) % 3])
    .replace('{z}', String(z))
    .replace('{x}', String(wrapped))
    .replace('{y}', String(y))
}

/** The tiles of a plan, row by row: `[{ x, y, px, py, url }]`. */
export function planTiles(plan, template) {
  const out = []
  for (let j = 0; j < plan.ny; j++) {
    for (let i = 0; i < plan.nx; i++) {
      const x = plan.x0 + i
      const y = plan.y0 + j
      if (y < 0 || y >= 2 ** plan.z) continue
      out.push({ x, y, px: i * TILE_SIZE, py: j * TILE_SIZE, url: tileUrl(template, plan.z, x, y) })
    }
  }
  return out
}

/**
 * Draw the tiles of `plan` onto `canvas` as they arrive. `onTile` is called
 * after each one is drawn (so a texture can be refreshed); `isCancelled`
 * lets a disposed view stop the drawing. Resolves `{ loaded, failed }` when
 * every tile has either arrived or failed. `createImage` is injectable for
 * tests.
 */
export function drawTiles(canvas, plan, template, {
  onTile = null, isCancelled = () => false, createImage = () => new Image(), crossOrigin = 'anonymous',
} = {}) {
  const ctx = canvas.getContext && canvas.getContext('2d')
  const tiles = planTiles(plan, template)
  if (!ctx || !tiles.length) return Promise.resolve({ loaded: 0, failed: tiles.length })
  let loaded = 0
  let failed = 0
  return new Promise(resolve => {
    const done = () => { if (loaded + failed === tiles.length) resolve({ loaded, failed }) }
    for (const tile of tiles) {
      const img = createImage()
      // Without CORS approval a tile would taint the canvas and the GPU would refuse it.
      if (crossOrigin) img.crossOrigin = crossOrigin
      img.decoding = 'async'
      img.onload = () => {
        if (!isCancelled()) {
          try {
            ctx.drawImage(img, tile.px, tile.py, TILE_SIZE, TILE_SIZE)
            loaded++
            if (onTile) onTile(tile)
          } catch {
            failed++
          }
        } else {
          failed++
        }
        done()
      }
      img.onerror = () => { failed++; done() }
      img.src = tile.url
    }
  })
}

/** Template of the platform's own tile endpoint for a task's orthophoto (same origin, session cookie). */
export const orthoTemplate = task =>
  `/api/method/webodm_core.api.tiles.serve?task_name=${encodeURIComponent(task)}&dataset=orthophoto&z={z}&x={x}&y={y}`
