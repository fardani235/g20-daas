// Point cloud viewer helpers: pure functions and API calls, no three.js scene.
// The WebGL side lives in composables/pointCloudLayer.js; this module is what
// the unit tests cover (attribute detection, filters, measurement math, URL
// rewriting for the Potree loader).
import { postMethod } from '@/lib/api'
import { formatArea, formatDistance, formatVolume } from '@/lib/format'

// ---------------------------------------------------------------------------
// Backend
// ---------------------------------------------------------------------------

/** Conversion state of a task's octree; `start` kicks the conversion off, `retry` restarts a failed one. */
export function getPotreeState(taskName, { start = false, retry = false } = {}) {
  return postMethod('webodm_core.api.pointcloud.potree_state', {
    task_name: taskName, start: start ? 1 : 0, retry: retry ? 1 : 0,
  })
}

/** DSM volume under a polygon given in the point cloud's CRS (never computed from the points). */
export function computeVolumeNative(taskName, ring, polygonCrs, method) {
  const coords = ring.map(p => [p[0], p[1]])
  if (coords.length && (coords[0][0] !== coords[coords.length - 1][0] || coords[0][1] !== coords[coords.length - 1][1])) {
    coords.push([coords[0][0], coords[0][1]])
  }
  return postMethod('webodm_core.api.tiles.volume', {
    task_name: taskName,
    polygon: JSON.stringify({ type: 'Polygon', coordinates: [coords] }),
    method,
    polygon_crs: polygonCrs || 'native',
  }).then(formatVolume)
}

export const CONVERTING_STATUSES = ['Queued', 'Running']

/** What the loading overlay says for a conversion/cache state. */
export function conversionLabel(state) {
  if (!state) return 'Checking point cloud…'
  if (state.status === 'Queued') return 'Preparing point cloud… (queued)'
  if (state.status === 'Running') return 'Preparing point cloud… converting to a streamable octree'
  if (state.status === 'Ready' && state.cache === 'warming') return 'Fetching point cloud from storage…'
  return 'Loading point cloud…'
}

// ---------------------------------------------------------------------------
// Loader URL rewriting. potree-core asks for `<base>/metadata.json` and derives
// hierarchy.bin / octree.bin by replacing the file name, so the loader is given
// a virtual base (`potree://<task>/metadata.json`) and this request manager
// maps each virtual file to its real private URL, same-origin with the session
// cookie and the Range header preserved. Nothing ever reaches the geospatial
// service from the browser.
// ---------------------------------------------------------------------------

export const POTREE_FILES = ['metadata.json', 'hierarchy.bin', 'octree.bin']

export function potreeBaseUrl(taskName) {
  return `potree://${encodeURIComponent(taskName)}/metadata.json`
}

export function potreeFileFor(virtualUrl) {
  const m = /^potree:\/\/[^/]+\/(metadata\.json|hierarchy\.bin|octree\.bin)$/.exec(String(virtualUrl || ''))
  return m ? m[1] : null
}

/**
 * Headers for an octree request: `Range` is kept, the multipart content-type
 * potree-core puts on range requests is dropped (meaningless on a GET).
 */
export function requestHeaders(headers) {
  const out = {}
  const add = (k, v) => { if (String(k).toLowerCase() !== 'content-type') out[k] = v }
  if (!headers) return out
  if (typeof headers.forEach === 'function' && !Array.isArray(headers)) headers.forEach((v, k) => add(k, v))
  else if (Array.isArray(headers)) headers.forEach(([k, v]) => add(k, v))
  else Object.entries(headers).forEach(([k, v]) => add(k, v))
  return out
}

/**
 * RequestManager for potree-core's `loadPointCloud(url, requestManager)`.
 * `files` maps file name -> URL (what the backend's potree_state returns).
 */
export function createPotreeRequestManager(files, fetchImpl = (...a) => fetch(...a)) {
  const resolve = url => {
    const name = potreeFileFor(url)
    if (!name) throw new Error(`Unexpected point cloud request: ${url}`)
    const real = files?.[name]
    if (!real) throw new Error(`The point cloud is missing ${name}`)
    return real
  }
  return {
    async getUrl(url) { return url },
    async fetch(input, init) {
      const url = typeof input === 'string' ? input : input?.url
      const res = await fetchImpl(resolve(url), { ...init, headers: requestHeaders(init?.headers), credentials: 'include' })
      if (!res.ok && res.status !== 206) {
        throw new Error(res.status === 401 || res.status === 403
          ? 'You do not have permission to open this point cloud'
          : `The point cloud could not be downloaded (HTTP ${res.status})`)
      }
      return res
    },
  }
}

// ---------------------------------------------------------------------------
// Attributes: which colour modes exist for a given octree.
// ---------------------------------------------------------------------------

export const COLOR_MODES = [
  { value: 'rgb', label: 'RGB' },
  { value: 'elevation', label: 'Elevation' },
  { value: 'intensity', label: 'Intensity' },
  { value: 'classification', label: 'Classification' },
]

function attribute(metadata, name) {
  return (metadata?.attributes || []).find(a => a.name === name) || null
}

function range(attr) {
  const min = Array.isArray(attr?.min) ? attr.min : [attr?.min]
  const max = Array.isArray(attr?.max) ? attr.max : [attr?.max]
  return { min, max }
}

function nonDegenerate(attr) {
  if (!attr) return false
  const { min, max } = range(attr)
  return min.some((m, i) => Number.isFinite(m) && Number.isFinite(max[i]) && max[i] > m)
}

/**
 * Colour modes available for an octree. Only modes whose attribute is present
 * *and carries information* are offered: an all-zero intensity or a single
 * classification value would just paint everything one colour.
 */
export function availableColorModes(metadata) {
  const out = []
  const rgb = attribute(metadata, 'rgb') || attribute(metadata, 'rgba')
  if (rgb && range(rgb).max.some(v => v > 0)) out.push('rgb')
  if (metadata?.boundingBox || attribute(metadata, 'position')) out.push('elevation')
  if (nonDegenerate(attribute(metadata, 'intensity'))) out.push('intensity')
  const cls = attribute(metadata, 'classification')
  if (cls && classificationsPresent(metadata).length > 1) out.push('classification')
  return out
}

export function colorModeOptions(metadata) {
  const available = availableColorModes(metadata)
  return COLOR_MODES.filter(m => available.includes(m.value))
}

/** Default colour mode: RGB when the cloud has colour, else elevation. */
export function defaultColorMode(metadata) {
  const modes = availableColorModes(metadata)
  return modes.includes('rgb') ? 'rgb' : modes[0] || 'elevation'
}

/** Elevation range [min, max] of the points (tight bounds when the converter recorded them). */
export function elevationRangeOf(metadata) {
  const pos = attribute(metadata, 'position')
  const tight = pos && Array.isArray(pos.min) && Array.isArray(pos.max) && pos.max[2] > pos.min[2]
  const min = tight ? pos.min[2] : metadata?.boundingBox?.min?.[2]
  const max = tight ? pos.max[2] : metadata?.boundingBox?.max?.[2]
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 0]
  return [min, max]
}

/** Intensity range of the cloud (what the brightness ramp is stretched over). */
export function intensityRangeOf(metadata) {
  const attr = attribute(metadata, 'intensity')
  if (!nonDegenerate(attr)) return [0, 1]
  const { min, max } = range(attr)
  return [min[0], max[0]]
}

/**
 * Classification codes present in the cloud. PotreeConverter writes a
 * histogram; without one we fall back to the min..max range (all of which
 * may not actually occur) and the layer narrows it as nodes load.
 */
export function classificationsPresent(metadata) {
  const attr = attribute(metadata, 'classification')
  if (!attr) return []
  if (Array.isArray(attr.histogram)) {
    return attr.histogram.map((n, i) => (n ? i : -1)).filter(i => i >= 0)
  }
  const { min, max } = range(attr)
  if (!Number.isFinite(min[0]) || !Number.isFinite(max[0])) return []
  const out = []
  for (let i = Math.max(0, Math.round(min[0])); i <= Math.min(255, Math.round(max[0])); i++) out.push(i)
  return out
}

// ASPRS LAS standard point classes (LAS 1.4, table 17) with Potree's default
// palette so the legend matches what is drawn.
export const CLASSIFICATION_LABELS = {
  0: 'Never classified', 1: 'Unclassified', 2: 'Ground', 3: 'Low vegetation', 4: 'Medium vegetation',
  5: 'High vegetation', 6: 'Building', 7: 'Low point (noise)', 8: 'Reserved', 9: 'Water', 10: 'Rail',
  11: 'Road surface', 12: 'Reserved', 13: 'Wire – guard', 14: 'Wire – conductor', 15: 'Transmission tower',
  16: 'Wire – connector', 17: 'Bridge deck', 18: 'High noise', 19: 'Overhead structure', 20: 'Ignored ground',
  21: 'Snow', 22: 'Temporal exclusion',
}

export const CLASSIFICATION_COLORS = {
  0: [0.5, 0.5, 0.5], 1: [0.5, 0.5, 0.5], 2: [0.63, 0.32, 0.18], 3: [0, 1, 0], 4: [0, 0.8, 0], 5: [0, 0.6, 0],
  6: [1, 0.66, 0], 7: [1, 0, 1], 8: [1, 0, 0], 9: [0, 0, 1], 10: [0.6, 0.6, 0.2], 11: [0.4, 0.4, 0.4], 12: [1, 1, 0],
  13: [0.9, 0.9, 0.5], 14: [0.9, 0.7, 0.3], 15: [0.8, 0.5, 0.2], 16: [0.7, 0.7, 0.9], 17: [0.6, 0.3, 0.6],
  18: [1, 0.3, 0.3], DEFAULT: [0.3, 0.6, 0.6],
}

export function classificationLabel(code) {
  return CLASSIFICATION_LABELS[code] || (code >= 64 ? `User defined ${code}` : `Reserved ${code}`)
}

export function classificationColor(code) {
  return CLASSIFICATION_COLORS[code] || CLASSIFICATION_COLORS.DEFAULT
}

export function cssColor(rgb) {
  return `rgb(${rgb.map(c => Math.round(c * 255)).join(',')})`
}

/** Legend entries for the classes that occur in the cloud. */
export function classificationLegend(metadata) {
  return classificationsPresent(metadata).map(code => ({
    code, label: classificationLabel(code), color: cssColor(classificationColor(code)),
  }))
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

export const POINT_BUDGETS = [
  { value: 500_000, label: '0.5 M points' },
  { value: 1_000_000, label: '1 M points' },
  { value: 2_000_000, label: '2 M points' },
  { value: 5_000_000, label: '5 M points' },
  { value: 10_000_000, label: '10 M points' },
]

export const BACKGROUNDS = [
  { value: 'dark', label: 'Dark', color: '#1c2030' },
  { value: 'black', label: 'Black', color: '#000000' },
  { value: 'gray', label: 'Gray', color: '#6b7280' },
  { value: 'white', label: 'White', color: '#ffffff' },
]

export const POINT_SIZE = { min: 1, max: 8, step: 0.5 }

export const DEFAULT_POINT_CLOUD_SETTINGS = Object.freeze({
  pointSize: 2,
  pointBudget: 1_000_000,
  background: 'dark',
})

const SETTINGS_KEY = 'pointcloud_viewer_settings'

export function loadPointCloudSettings(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(SETTINGS_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return {
      pointSize: clampSize(parsed.pointSize),
      pointBudget: POINT_BUDGETS.some(b => b.value === parsed.pointBudget) ? parsed.pointBudget : DEFAULT_POINT_CLOUD_SETTINGS.pointBudget,
      background: BACKGROUNDS.some(b => b.value === parsed.background) ? parsed.background : DEFAULT_POINT_CLOUD_SETTINGS.background,
    }
  } catch {
    return { ...DEFAULT_POINT_CLOUD_SETTINGS }
  }
}

export function savePointCloudSettings(settings, storage = globalThis.localStorage) {
  try {
    const { pointSize, pointBudget, background } = settings
    storage?.setItem(SETTINGS_KEY, JSON.stringify({ pointSize, pointBudget, background }))
  } catch {
    // Persistence is a convenience.
  }
}

export function clampSize(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return DEFAULT_POINT_CLOUD_SETTINGS.pointSize
  return Math.min(POINT_SIZE.max, Math.max(POINT_SIZE.min, n))
}

/** Device-aware budget cap: phones get fewer points than the saved preference. */
export function budgetCapFor(deviceMemory, coarsePointer = false) {
  if (coarsePointer || (deviceMemory && deviceMemory <= 2)) return 1_000_000
  if (deviceMemory && deviceMemory <= 4) return 2_000_000
  return 10_000_000
}

/** Clamp an elevation filter to the cloud's range, keeping min <= max. */
export function clampElevationFilter(filter, [lo, hi]) {
  let min = Number.isFinite(filter?.[0]) ? filter[0] : lo
  let max = Number.isFinite(filter?.[1]) ? filter[1] : hi
  min = Math.min(Math.max(min, lo), hi)
  max = Math.min(Math.max(max, lo), hi)
  if (min > max) [min, max] = [max, min]
  return [min, max]
}

export function isFullElevationRange(filter, [lo, hi]) {
  return !filter || (filter[0] <= lo && filter[1] >= hi)
}

export function formatElevation(value) {
  return `${Number(value).toFixed(2)} m`
}

// ---------------------------------------------------------------------------
// Measurement math. Points are `[x, y, z]` in the cloud's native CRS (metres
// for projected ODM outputs).
// ---------------------------------------------------------------------------

export const MEASURE_KINDS = ['distance', 'area', 'volume']
export const MEASURE_LABELS = { distance: 'Distance', area: 'Area', volume: 'Volume' }
export const MEASURE_MIN_POINTS = { distance: 2, area: 3, volume: 3 }

export function segmentLength(a, b) {
  return Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2])
}

/** 3D length of a polyline (point to point, following the terrain). */
export function polylineLength(points) {
  let total = 0
  for (let i = 1; i < points.length; i++) total += segmentLength(points[i - 1], points[i])
  return total
}

/** Horizontal (projected) area of a polygon, shoelace on X/Y. */
export function polygonArea(points) {
  if (!points || points.length < 3) return 0
  let sum = 0
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i]
    const [x2, y2] = points[(i + 1) % points.length]
    sum += x1 * y2 - x2 * y1
  }
  return Math.abs(sum) / 2
}

export function perimeter(points) {
  if (!points || points.length < 2) return 0
  return polylineLength(points) + (points.length > 2 ? segmentLength(points[points.length - 1], points[0]) : 0)
}

/** Live readout while a measurement is being drawn. */
export function measurementText(kind, points) {
  if (kind === 'distance') return formatDistance(polylineLength(points))
  if (kind === 'area') return formatArea(polygonArea(points))
  if (kind === 'volume') return points.length >= 3 ? `${formatArea(polygonArea(points))} · finish to compute` : 'Pick at least 3 points'
  return ''
}

export function canFinish(kind, points) {
  return (points?.length || 0) >= (MEASURE_MIN_POINTS[kind] || 2)
}

/** Centroid of the vertices (where the result label sits). */
export function centroid(points) {
  if (!points?.length) return [0, 0, 0]
  const s = points.reduce((acc, p) => [acc[0] + p[0], acc[1] + p[1], acc[2] + p[2]], [0, 0, 0])
  return s.map(v => v / points.length)
}

/** Native coordinates of a point from the viewer's local (centred) frame. */
export function localToNative(local, origin) {
  return [local[0] + origin[0], local[1] + origin[1], local[2] + origin[2]]
}

export function nativeToLocal(native, origin) {
  return [native[0] - origin[0], native[1] - origin[1], native[2] - origin[2]]
}

// ---------------------------------------------------------------------------
// Switcher / routing
// ---------------------------------------------------------------------------

export const POINT_CLOUD_SOURCE = 'pointcloud'

/** Route to the viewer showing a task's point cloud (kept in the URL via ?source=). */
export function pointCloudRoute(projectId, taskName) {
  return `/project/${encodeURIComponent(projectId)}/task/${encodeURIComponent(taskName)}/model?source=${POINT_CLOUD_SOURCE}`
}
