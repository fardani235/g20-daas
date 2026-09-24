// Raster metadata: fetch wrapper for `api.task.get_raster_metadata` plus the
// pure formatting / derivation helpers the map, console and layer panel share.
// Mirrors lib/plugins.js for the transport bits. Everything below the fetch
// section is side-effect free and unit-tested.

import { frappeErrorMessage } from './utils'

function headers() {
  const h = {}
  if (window.csrf_token) h['X-Frappe-CSRF-Token'] = window.csrf_token
  return h
}

async function unwrap(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(frappeErrorMessage(err))
  }
  const data = await res.json()
  return data.message !== undefined ? data.message : data
}

function query(params) {
  const entries = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null)
  if (!entries.length) return ''
  return '?' + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
}

export const RASTER_DATASETS = ['orthophoto', 'dsm', 'dtm']

/**
 * `{orthophoto, dsm, dtm}` → metadata dict or null (task has no such raster).
 * With `dataset` the single dict. `refresh` forces re-extraction of a Failed row.
 */
export function getRasterMetadata(taskName, { dataset, refresh } = {}) {
  const params = { task_name: taskName, dataset, refresh: refresh ? 1 : undefined }
  return fetch(`/api/method/webodm_core.api.task.get_raster_metadata${query(params)}`, {
    method: 'GET',
    headers: headers(),
  }).then(unwrap)
}

// --------------------------------------------------------------------------
// Pure helpers
// --------------------------------------------------------------------------

const METRIC_UNITS = new Set(['metre', 'meter', 'm', 'metres', 'meters'])

export function isExtracted(meta) {
  return !!meta && meta.status !== 'Failed' && Number.isFinite(meta.width) && meta.width > 0
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || bytes === '') return '—'
  const b = Number(bytes)
  if (!Number.isFinite(b) || b < 0) return '—'
  if (b < 1024) return `${b} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = b / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

export function formatDimensions(meta) {
  if (!isExtracted(meta)) return '—'
  const n = x => Number(x).toLocaleString('en-US')
  return `${n(meta.width)} × ${n(meta.height)} px`
}

export function formatMegapixels(meta) {
  if (!isExtracted(meta) || !meta.height) return '—'
  const mp = (meta.width * meta.height) / 1e6
  return mp >= 100 ? `${mp.toFixed(0)} MP` : `${mp.toFixed(1)} MP`
}

/** Ground sample distance in metres per pixel, or null when unknown / non-metric. */
export function gsdMetres(meta) {
  if (!meta || !Array.isArray(meta.pixel_size)) return null
  const units = String(meta.crs?.units || '').toLowerCase()
  if (!METRIC_UNITS.has(units)) return null
  const [x, y] = meta.pixel_size.map(Number)
  if (!(x > 0) || !(y > 0)) return null
  return (x + y) / 2
}

/** "5.0 cm/px" for metric CRSs, "0.000010°/px" for geographic, "—" otherwise. */
export function formatPixelSize(meta) {
  if (!meta || !Array.isArray(meta.pixel_size)) return '—'
  const gsd = gsdMetres(meta)
  if (gsd !== null) {
    if (gsd < 1) return `${(gsd * 100).toFixed(gsd * 100 < 10 ? 2 : 1)} cm/px`
    return `${gsd.toFixed(2)} m/px`
  }
  const [x] = meta.pixel_size.map(Number)
  if (!(x > 0)) return '—'
  const units = String(meta.crs?.units || '').toLowerCase()
  if (units === 'degree') return `${x.toFixed(6)}°/px`
  return `${x.toPrecision(4)} ${meta.crs?.units || 'units'}/px`
}

export function crsLabel(meta) {
  if (!meta) return '—'
  const epsg = meta.crs?.epsg
  if (epsg) return `EPSG:${epsg}`
  if (meta.crs?.wkt) return 'Custom (WKT)'
  if (meta.georeference === 'no_crs') return 'Missing'
  return 'None'
}

export function georeferenceLabel(meta) {
  switch (meta?.georeference) {
    case 'full': return 'Georeferenced'
    case 'no_crs': return 'No CRS (pixel coordinates only in a local grid)'
    case 'no_transform': return 'CRS only, no geotransform'
    default: return 'Not georeferenced'
  }
}

export function formatNodata(meta) {
  if (!meta) return '—'
  const v = meta.nodata
  if (v === null || v === undefined) return 'None'
  return typeof v === 'number' ? String(v) : String(v).toUpperCase()
}

export function formatBands(meta) {
  if (!isExtracted(meta)) return '—'
  const n = meta.band_count
  const ci = Array.isArray(meta.color_interpretation) ? meta.color_interpretation.filter(Boolean) : []
  const dtype = meta.dtype ? ` ${meta.dtype}` : ''
  if (!ci.length) return `${n}${dtype}`
  const names = ci.map(c => c === 'palette' ? 'palette' : c.toUpperCase()[0] + c.slice(1))
  return `${n}${dtype} (${names.join(', ')})`
}

export function formatLayout(meta) {
  if (!isExtracted(meta)) return '—'
  const parts = []
  if (meta.is_tiled && meta.block_size) parts.push(`${meta.block_size[0]}×${meta.block_size[1]} tiles`)
  else parts.push('strips')
  if (meta.compression) parts.push(meta.compression.toUpperCase())
  if (meta.overview_count) parts.push(`${meta.overview_count} overview${meta.overview_count === 1 ? '' : 's'}`)
  else parts.push('no overviews')
  if (meta.is_cog) parts.push('COG')
  return parts.join(' · ')
}

/** Native bounds "minx, miny – maxx, maxy" with sensible precision for the CRS. */
export function formatBounds(meta) {
  const b = meta?.bounds
  if (!Array.isArray(b) || b.length !== 4 || !b.every(Number.isFinite)) return '—'
  const digits = String(meta.crs?.units || '').toLowerCase() === 'degree' ? 6 : 2
  const f = v => v.toFixed(digits)
  return `${f(b[0])}, ${f(b[1])} – ${f(b[2])}, ${f(b[3])}`
}

/** One-line summary for a task card: "14,718 × 11,640 px · 4 bands · 5.0 cm/px · EPSG:32632 · 204 MB". */
export function summarize(meta) {
  if (!meta) return ''
  if (meta.status === 'Failed') return 'Metadata unavailable'
  if (!isExtracted(meta)) return ''
  const parts = [formatDimensions(meta)]
  if (meta.band_count) parts.push(`${meta.band_count} band${meta.band_count === 1 ? '' : 's'}`)
  const px = formatPixelSize(meta)
  if (px !== '—') parts.push(px)
  parts.push(crsLabel(meta))
  if (meta.file_size) parts.push(formatBytes(meta.file_size))
  return parts.join(' · ')
}

/** Label/value rows for a details list. Failed rows yield the error instead. */
export function detailRows(meta) {
  if (!meta) return []
  if (meta.status === 'Failed') {
    return [{ label: 'Status', value: 'Extraction failed' }, { label: 'Error', value: meta.error || 'unknown error' }]
  }
  if (!isExtracted(meta)) return []
  const rows = [
    { label: 'Size', value: `${formatDimensions(meta)} (${formatMegapixels(meta)})` },
    { label: 'Bands', value: formatBands(meta) },
    { label: 'Pixel size', value: formatPixelSize(meta) },
    { label: 'CRS', value: `${crsLabel(meta)}${meta.crs?.units ? ` · ${meta.crs.units}` : ''}` },
    { label: 'Georeference', value: georeferenceLabel(meta) },
    { label: 'Bounds', value: formatBounds(meta) },
    { label: 'NoData', value: formatNodata(meta) },
    { label: 'Layout', value: formatLayout(meta) },
    { label: 'File', value: `${meta.driver || 'raster'} · ${formatBytes(meta.file_size)}` },
  ]
  if (meta.software) rows.push({ label: 'Software', value: meta.software })
  return rows.filter(r => r.value && r.value !== '—')
}

// --------------------------------------------------------------------------
// Tile-layer derivation
// --------------------------------------------------------------------------

const WEB_MERCATOR_M_PER_PX_Z0 = 156543.03392804097
export const DEFAULT_MAX_NATIVE_ZOOM = 22

/**
 * Smallest web-mercator zoom whose tile pixels are at least as fine as the
 * raster's ground sample distance at its latitude. Beyond it Leaflet upscales
 * client-side instead of requesting tiles the tiler can only upsample.
 * Falls back to `DEFAULT_MAX_NATIVE_ZOOM` when the GSD is unknown.
 */
export function maxNativeZoomFor(meta, { min = 1, max = 24, fallback = DEFAULT_MAX_NATIVE_ZOOM } = {}) {
  const gsd = gsdMetres(meta)
  if (gsd === null) return fallback
  let lat = 0
  const b = meta.bounds_4326
  if (Array.isArray(b) && b.length === 4 && Number.isFinite(b[1]) && Number.isFinite(b[3])) {
    lat = (b[1] + b[3]) / 2
  }
  const cosLat = Math.max(Math.cos((lat * Math.PI) / 180), 0.05)
  const z = Math.ceil(Math.log2((WEB_MERCATOR_M_PER_PX_Z0 * cosLat) / gsd))
  if (!Number.isFinite(z)) return fallback
  return Math.min(max, Math.max(min, z))
}
