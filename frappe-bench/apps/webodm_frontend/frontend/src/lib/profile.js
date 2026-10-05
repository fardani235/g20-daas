// Elevation profile helpers: the backend call, chart geometry (scales and
// SVG paths with gaps), hover lookup, formatting and CSV export. Pure
// functions, no DOM — unit-tested in jsdom; components/ProfileChart.vue and
// ProfilePanel.vue render what these produce.
import { postMethod } from '@/lib/api'
import { formatDistance } from '@/lib/format'
import { classificationColor, classificationLabel, cssColor } from '@/lib/potree'

// ---------------------------------------------------------------------------
// Backend
// ---------------------------------------------------------------------------

/**
 * Profile along `line` (`[[x, y], ...]` or `[[x, y, z], ...]`, cloud CRS).
 * `width` is the corridor width in metres; `step` the bin size (null = auto).
 */
export function fetchProfile(taskName, line, { width = 1, step = null, groundWindow = null } = {}) {
  const body = { task_name: taskName, line: JSON.stringify(line.map(p => [p[0], p[1]])), width }
  if (step) body.step = step
  if (groundWindow !== null && groundWindow !== undefined) body.ground_window = groundWindow
  return postMethod('webodm_core.api.pointcloud.profile', body)
}

// ---------------------------------------------------------------------------
// Settings (remembered in the browser)
// ---------------------------------------------------------------------------

export const PROFILE_WIDTHS = [0.25, 0.5, 1, 2, 5, 10]
export const PROFILE_STEPS = [{ value: 0, label: 'Auto' }, { value: 0.1, label: '0.1 m' }, { value: 0.25, label: '0.25 m' },
  { value: 0.5, label: '0.5 m' }, { value: 1, label: '1 m' }, { value: 2, label: '2 m' }, { value: 5, label: '5 m' }]
export const DEFAULT_PROFILE_SETTINGS = Object.freeze({ width: 1, step: 0, exaggeration: 'auto' })
const SETTINGS_KEY = 'pointcloud_profile_settings'

export function loadProfileSettings(storage = globalThis.localStorage) {
  try {
    const parsed = JSON.parse(storage?.getItem(SETTINGS_KEY) || '{}')
    return {
      width: PROFILE_WIDTHS.includes(parsed.width) ? parsed.width : DEFAULT_PROFILE_SETTINGS.width,
      step: PROFILE_STEPS.some(s => s.value === parsed.step) ? parsed.step : DEFAULT_PROFILE_SETTINGS.step,
      exaggeration: parsed.exaggeration === 1 || parsed.exaggeration === 'auto' ? parsed.exaggeration : DEFAULT_PROFILE_SETTINGS.exaggeration,
    }
  } catch {
    return { ...DEFAULT_PROFILE_SETTINGS }
  }
}

export function saveProfileSettings(settings, storage = globalThis.localStorage) {
  try {
    const { width, step, exaggeration } = settings
    storage?.setItem(SETTINGS_KEY, JSON.stringify({ width, step, exaggeration }))
  } catch {
    // Persistence is a convenience.
  }
}

// ---------------------------------------------------------------------------
// Series helpers
// ---------------------------------------------------------------------------

const isNum = v => typeof v === 'number' && Number.isFinite(v)

/** Ground elevation at bin `i`: measured, else interpolated, else null. */
export function groundAt(profile, i) {
  const g = profile?.ground?.[i]
  if (isNum(g)) return g
  const f = profile?.ground_fill?.[i]
  return isNum(f) ? f : null
}

/** Elevation range [min, max] over every drawn series (ground, top, classes), with padding. */
export function elevationExtent(profile, { pad = 0.08 } = {}) {
  let lo = Infinity, hi = -Infinity
  const scan = arr => { for (const v of arr || []) if (isNum(v)) { if (v < lo) lo = v; if (v > hi) hi = v } }
  scan(profile?.min)
  scan(profile?.max)
  scan(profile?.ground)
  scan(profile?.ground_fill)
  scan(profile?.top)
  for (const c of profile?.classes || []) { scan(c.min); scan(c.max) }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null
  if (hi - lo < 1) { const mid = (lo + hi) / 2; lo = mid - 0.5; hi = mid + 0.5 }
  const p = (hi - lo) * pad
  return [lo - p, hi + p]
}

/**
 * Linear scales for a chart of `width` x `height` px with the given margins.
 * `exaggeration: 'auto'` fills the plot area; `1` uses the same metres per
 * pixel on both axes (true shape): the elevation window grows to fill the
 * height, or — when the data is taller than it is long — the horizontal
 * scale shrinks and the line no longer spans the full width.
 */
export function makeScales(profile, { width, height, margin = { top: 8, right: 12, bottom: 22, left: 48 }, exaggeration = 'auto' }) {
  const plotW = Math.max(width - margin.left - margin.right, 1)
  const plotH = Math.max(height - margin.top - margin.bottom, 1)
  const length = Math.max(profile?.length || 0, 1e-6)
  let [zLo, zHi] = elevationExtent(profile) || [0, 1]
  let pxX = plotW / length
  let pxY = plotH / (zHi - zLo)
  if (exaggeration === 1) {
    const k = Math.min(pxX, pxY)
    const mid = (zLo + zHi) / 2
    const window = plotH / k
    zLo = mid - window / 2
    zHi = mid + window / 2
    pxX = pxY = k
  }
  const x = d => margin.left + d * pxX
  const y = z => margin.top + plotH - (z - zLo) * pxY
  return {
    x, y, plotW, plotH, margin, zLo, zHi, length,
    usedW: length * pxX,
    invertX: px => (px - margin.left) / pxX,
    exaggeration: pxY / pxX,
  }
}

/** Bin index nearest to `distance`, or -1 outside the line. */
export function binAt(profile, distance) {
  const n = profile?.bins || 0
  if (!n || !isNum(distance)) return -1
  if (distance < 0 || distance > profile.length) return -1
  return Math.min(n - 1, Math.max(0, Math.floor(distance / profile.step)))
}

/**
 * SVG path of a series with gaps: consecutive numeric samples are joined,
 * a null breaks the line. `values` is indexed like `profile.distance`.
 */
export function linePath(distance, values, scales) {
  let d = ''
  let pen = false
  for (let i = 0; i < (values?.length || 0); i++) {
    const v = values[i]
    if (!isNum(v)) { pen = false; continue }
    d += `${pen ? 'L' : 'M'}${scales.x(distance[i]).toFixed(1)},${scales.y(v).toFixed(1)}`
    pen = true
  }
  return d
}

/** Closed band between `lower` and `upper` (one polygon per gap-free run). */
export function bandPath(distance, lower, upper, scales) {
  let d = ''
  let run = []
  const flush = () => {
    if (run.length < 1) { run = []; return }
    const top = run.map(i => `${scales.x(distance[i]).toFixed(1)},${scales.y(upper[i]).toFixed(1)}`)
    const bottom = run.slice().reverse().map(i => `${scales.x(distance[i]).toFixed(1)},${scales.y(lower[i]).toFixed(1)}`)
    if (run.length === 1) {
      // A single bin: a thin vertical sliver so a wire crossing is still visible.
      const i = run[0], hw = Math.max(scales.plotW / Math.max(distance.length, 1) / 2, 1)
      const x0 = scales.x(distance[i]) - hw, x1 = scales.x(distance[i]) + hw
      d += `M${x0.toFixed(1)},${scales.y(upper[i]).toFixed(1)}L${x1.toFixed(1)},${scales.y(upper[i]).toFixed(1)}L${x1.toFixed(1)},${scales.y(lower[i]).toFixed(1)}L${x0.toFixed(1)},${scales.y(lower[i]).toFixed(1)}Z`
    } else {
      d += `M${top.join('L')}L${bottom.join('L')}Z`
    }
    run = []
  }
  for (let i = 0; i < (lower?.length || 0); i++) {
    if (isNum(lower[i]) && isNum(upper[i])) run.push(i)
    else flush()
  }
  flush()
  return d
}

/** Per-bin ground series for drawing: measured only, and interpolated only (null elsewhere). */
export function groundSeries(profile) {
  const n = profile?.bins || 0
  const measured = [], filled = []
  for (let i = 0; i < n; i++) {
    const g = profile.ground?.[i], f = profile.ground_fill?.[i]
    measured.push(isNum(g) ? g : null)
    filled.push(isNum(f) ? f : null)
  }
  // Connect each interpolated run to the measured samples on both sides so the dashed line meets the solid one.
  for (let i = 0; i < n; i++) {
    if (filled[i] === null) continue
    if (i > 0 && filled[i - 1] === null && measured[i - 1] !== null) filled[i - 1] = measured[i - 1]
    if (i + 1 < n && filled[i + 1] === null && measured[i + 1] !== null) filled[i + 1] = measured[i + 1]
  }
  return { measured, filled }
}

/** "Nice" axis ticks covering [lo, hi] with about `count` steps. */
export function ticks(lo, hi, count = 5) {
  if (!isNum(lo) || !isNum(hi) || hi <= lo) return []
  // d3's tickStep: the 1 / 2 / 5 multiple closest (in log space) to the raw step.
  const raw = (hi - lo) / count
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const err = raw / mag
  const step = mag * (err >= Math.sqrt(50) ? 10 : err >= Math.sqrt(10) ? 5 : err >= Math.sqrt(2) ? 2 : 1)
  const out = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(6)))
  return out
}

// ---------------------------------------------------------------------------
// Layers and legend
// ---------------------------------------------------------------------------

/** Class layers with label and colour, in the order they are drawn. */
export function classLayers(profile) {
  return (profile?.classes || []).map(c => ({
    code: c.code,
    label: classificationLabel(c.code),
    color: cssColor(classificationColor(c.code)),
    points: c.points,
    maxHeight: c.max_height,
    maxHeightAt: c.max_height_at,
    layer: c,
  }))
}

export function groundMethodLabel(profile) {
  const m = profile?.classification?.ground_method
  if (m === 'class2_median') return 'Ground: median of classified ground points'
  if (m === 'low_percentile_filtered') return 'Ground: lowest points, objects filtered (cloud has no classification)'
  if (m === 'low_percentile') return 'Ground: lowest points (cloud has no classification)'
  return ''
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

export function fmtM(v, digits = 2) {
  return isNum(v) ? `${v.toFixed(digits)} m` : '—'
}

export function fmtPct(v) {
  return isNum(v) ? `${v > 0 ? '+' : ''}${v.toFixed(1)} %` : '—'
}

/** Summary rows for the stats strip: [label, value, hint]. */
export function statRows(profile) {
  const s = profile?.stats
  if (!s) return []
  const at = d => (isNum(d) ? ` at ${formatDistance(d)}` : '')
  const rows = [
    ['Length', formatDistance(s.length), isNum(s.length_3d) ? `${fmtM(s.length_3d, 1)} along the ground` : ''],
    ['Elevation', isNum(s.ground_min) && isNum(s.ground_max) ? `${fmtM(s.ground_min)} – ${fmtM(s.ground_max)}` : '—',
      `ground, ${fmtM(s.ground_start)} → ${fmtM(s.ground_end)}`],
    ['Ascent / descent', `${isNum(s.ascent) ? `+${s.ascent.toFixed(2)}` : '—'} / ${isNum(s.descent) ? `−${s.descent.toFixed(2)}` : '—'} m`,
      'changes smaller than 0.25 m are not counted'],
    ['Steepest grade', fmtPct(s.max_grade_pct), isNum(s.max_grade_at) ? `over 2 m${at(s.max_grade_at)} · mean ${fmtPct(s.mean_grade_pct)}` : ''],
    ['Max height', fmtM(s.height_max), isNum(s.height_max_at) ? `above ground${at(s.height_max_at)} · mean ${fmtM(s.height_mean)}` : ''],
    ['Coverage', isNum(s.coverage) ? `${Math.round(s.coverage * 100)} %` : '—',
      `${s.bins_with_data} of ${s.bins} bins · ${(s.points || 0).toLocaleString()} points`],
  ]
  return rows
}

/** Hover readout for bin `i`. */
export function hoverInfo(profile, i) {
  if (!profile || i < 0 || i >= profile.bins) return null
  const ground = groundAt(profile, i)
  const top = profile.top?.[i]
  const classes = classLayers(profile)
    .map(c => ({ ...c, height: c.layer.height?.[i], max: c.layer.max?.[i], count: c.layer.count?.[i] }))
    .filter(c => isNum(c.max))
  return {
    index: i,
    distance: profile.distance[i],
    x: profile.x?.[i],
    y: profile.y?.[i],
    ground,
    groundInterpolated: ground !== null && !isNum(profile.ground?.[i]),
    top: isNum(top) ? top : null,
    height: isNum(profile.height?.[i]) ? profile.height[i] : null,
    count: profile.count?.[i] || 0,
    gap: !profile.count?.[i],
    classes,
  }
}

/** Sampling note shown under the chart, or '' when the profile is at full resolution. */
export function samplingNote(profile) {
  const s = profile?.sampling
  if (!s) return ''
  if (s.full_resolution) return ''
  return `Sampled at ${s.spacing} m spacing (${(s.points_read || 0).toLocaleString()} of ${(s.points_available || 0).toLocaleString()} points under the corridor) — shorten the line or narrow the corridor for full detail.`
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

/** CSV of the per-bin series (distance, bin centre, ground, top, height, counts, per-class). */
export function profileCsv(profile) {
  if (!profile) return ''
  const classes = profile.classes || []
  const head = ['distance_m', 'x', 'y', 'count', 'ground_m', 'ground_source', 'top_m', 'height_m', 'min_m', 'max_m',
    ...classes.flatMap(c => [`class_${c.code}_max_m`, `class_${c.code}_height_m`, `class_${c.code}_count`])]
  const cell = v => (isNum(v) ? String(Math.round(v * 1000) / 1000) : '')
  const rows = [head.join(',')]
  for (let i = 0; i < profile.bins; i++) {
    const g = profile.ground?.[i], f = profile.ground_fill?.[i]
    const source = isNum(g) ? 'measured' : isNum(f) ? 'interpolated' : ''
    rows.push([
      cell(profile.distance[i]), cell(profile.x?.[i]), cell(profile.y?.[i]), profile.count?.[i] ?? 0,
      cell(isNum(g) ? g : f), source, cell(profile.top?.[i]), cell(profile.height?.[i]), cell(profile.min?.[i]), cell(profile.max?.[i]),
      ...classes.flatMap(c => [cell(c.max?.[i]), cell(c.height?.[i]), c.count?.[i] ?? 0]),
    ].join(','))
  }
  return rows.join('\n') + '\n'
}

export function csvFilename(taskName) {
  return `${taskName || 'pointcloud'}_profile.csv`
}
