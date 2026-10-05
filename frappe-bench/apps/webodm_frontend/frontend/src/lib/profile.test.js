import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  bandPath,
  binAt,
  classLayers,
  csvFilename,
  elevationExtent,
  fetchProfile,
  groundAt,
  groundMethodLabel,
  groundSeries,
  hoverInfo,
  linePath,
  loadProfileSettings,
  makeScales,
  profileCsv,
  samplingNote,
  saveProfileSettings,
  statRows,
  ticks,
} from './profile'

// A trimmed backend document: 6 bins of 1 m, a gap at bin 2, interpolated
// ground under a 2-bin "building" (class 6) at bins 3-4, a wire at bin 5.
const PROFILE = {
  crs: 'EPSG:32632', length: 6, step: 1, bins: 6, width: 1,
  line: [[0, 0], [6, 0]],
  vertices: [{ distance: 0, x: 0, y: 0 }, { distance: 6, x: 6, y: 0 }],
  distance: [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
  x: [0.5, 1.5, 2.5, 3.5, 4.5, 5.5], y: [0, 0, 0, 0, 0, 0],
  count: [10, 12, 0, 20, 22, 9],
  min: [100, 100.1, null, 108, 108.1, 100.5],
  max: [100.2, 100.3, null, 108.2, 108.3, 112.5],
  ground: [100, 100.1, null, null, null, 100.5],
  ground_fill: [null, null, 100.2, 100.3, 100.4, null],
  top: [100.2, 100.3, null, 108.2, 108.3, 112.5],
  height: [0.2, 0.2, null, 7.9, 7.9, 12],
  classes: [
    { code: 6, points: 42, bins: 2, count: [0, 0, 0, 20, 22, 0], min: [null, null, null, 108, 108.1, null],
      max: [null, null, null, 108.2, 108.3, null], median: [null, null, null, 108.1, 108.2, null],
      height: [null, null, null, 7.9, 7.9, null], max_height: 7.9, max_height_at: 3.5 },
    { code: 14, points: 3, bins: 1, count: [0, 0, 0, 0, 0, 3], min: [null, null, null, null, null, 112.4],
      max: [null, null, null, null, null, 112.5], median: [null, null, null, null, null, 112.5],
      height: [null, null, null, null, null, 12], max_height: 12, max_height_at: 5.5 },
  ],
  gaps: [[2, 3]],
  classification: { available: true, ground_method: 'class2_median', noise_excluded: [7, 18] },
  sampling: { points_read: 500, points_available: 500, full_resolution: true, spacing: 0.1 },
  stats: {
    length: 6, length_3d: 6.1, segments: 1, bins: 6, bins_with_data: 5, coverage: 0.8333, points: 73,
    ground_start: 100, ground_end: 100.5, ground_min: 100, ground_min_at: 0.5, ground_max: 100.5, ground_max_at: 5.5,
    top_max: 112.5, top_max_at: 5.5, height_max: 12, height_max_at: 5.5, height_mean: 5.6,
    ascent: 0.5, descent: 0, max_grade_pct: 10, max_grade_at: 3, mean_grade_pct: 8.3,
  },
}

describe('series helpers', () => {
  it('reads ground as measured, else interpolated, else null', () => {
    expect(groundAt(PROFILE, 0)).toBe(100)
    expect(groundAt(PROFILE, 3)).toBe(100.3)
    expect(groundAt({ ground: [null], ground_fill: [null] }, 0)).toBeNull()
  })

  it('finds the elevation extent over every series with padding', () => {
    const [lo, hi] = elevationExtent(PROFILE, { pad: 0 })
    expect(lo).toBe(100)
    expect(hi).toBe(112.5)
    expect(elevationExtent({ ground: [null], top: [null] })).toBeNull()
    // Flat data still gets a 1 m window.
    expect(elevationExtent({ ground: [50, 50], top: [50, 50] }, { pad: 0 })).toEqual([49.5, 50.5])
  })

  it('maps distance to bins and back', () => {
    expect(binAt(PROFILE, 0)).toBe(0)
    expect(binAt(PROFILE, 2.4)).toBe(2)
    expect(binAt(PROFILE, 6)).toBe(5)
    expect(binAt(PROFILE, -1)).toBe(-1)
    expect(binAt(PROFILE, 7)).toBe(-1)
  })

  it('splits ground into measured and interpolated runs that touch', () => {
    const { measured, filled } = groundSeries(PROFILE)
    expect(measured).toEqual([100, 100.1, null, null, null, 100.5])
    // The dashed run is extended onto the neighbouring measured samples so the lines meet.
    expect(filled).toEqual([null, 100.1, 100.2, 100.3, 100.4, 100.5])
  })
})

describe('scales and paths', () => {
  const scales = makeScales(PROFILE, { width: 600, height: 300, margin: { top: 0, right: 0, bottom: 0, left: 0 } })

  it('scales distance and elevation into the plot area', () => {
    expect(scales.x(0)).toBe(0)
    expect(scales.x(6)).toBe(600)
    expect(scales.y(scales.zLo)).toBe(300)
    expect(scales.y(scales.zHi)).toBe(0)
    expect(scales.invertX(300)).toBeCloseTo(3)
    // 100 px/m along, 300 px over the padded 14.5 m window: the auto view squashes this tall profile.
    const [lo, hi] = elevationExtent(PROFILE)
    expect(scales.exaggeration).toBeCloseTo((300 / (hi - lo)) / 100, 6)
  })

  it('can force a 1:1 vertical scale', () => {
    const s = makeScales(PROFILE, { width: 600, height: 300, margin: { top: 0, right: 0, bottom: 0, left: 0 }, exaggeration: 1 })
    expect(s.exaggeration).toBeCloseTo(1, 6)
    // Taller than long: the horizontal scale shrinks to the vertical one and the line no longer spans the width.
    const [lo, hi] = elevationExtent(PROFILE)
    expect(s.usedW).toBeCloseTo(6 * 300 / (hi - lo), 6)
    expect(s.usedW).toBeLessThan(600)
    expect(s.zHi - s.zLo).toBeCloseTo(hi - lo, 6)
    // Longer than tall: the elevation window grows instead and the full width is used.
    const flat = { ...PROFILE, length: 600, top: [100.2, 100.3, null, 101, 101, 101.5], max: [100.2, 100.3, null, 101, 101, 101.5],
      height: [0.2, 0.2, null, 0.7, 0.6, 1], classes: [] }
    const f = makeScales(flat, { width: 600, height: 300, margin: { top: 0, right: 0, bottom: 0, left: 0 }, exaggeration: 1 })
    expect(f.exaggeration).toBeCloseTo(1, 6)
    expect(f.usedW).toBe(600)
    expect(f.zHi - f.zLo).toBeCloseTo(300, 3)
  })

  it('breaks a line path at gaps and bands at gaps', () => {
    const d = linePath(PROFILE.distance, PROFILE.top, scales)
    expect(d.match(/M/g)).toHaveLength(2) // before and after the gap
    expect(d).not.toMatch(/NaN/)
    const band = bandPath(PROFILE.distance, PROFILE.min, PROFILE.max, scales)
    expect(band.match(/Z/g)).toHaveLength(2)
    // A single-bin layer (the wire) still produces a visible sliver.
    const wire = bandPath(PROFILE.distance, PROFILE.classes[1].min, PROFILE.classes[1].max, scales)
    expect(wire.match(/Z/g)).toHaveLength(1)
    expect(linePath([], [], scales)).toBe('')
  })

  it('produces nice ticks', () => {
    expect(ticks(0, 100, 5)).toEqual([0, 20, 40, 60, 80, 100])
    expect(ticks(100.2, 112.9, 5)).toEqual([102, 104, 106, 108, 110, 112])
    expect(ticks(5, 5)).toEqual([])
  })
})

describe('layers, hover and stats', () => {
  it('describes class layers with labels and colours', () => {
    const layers = classLayers(PROFILE)
    expect(layers.map(l => l.label)).toEqual(['Building', 'Wire – conductor'])
    expect(layers[0].color).toMatch(/^rgb\(/)
    expect(layers[1].maxHeight).toBe(12)
  })

  it('explains how the ground was derived', () => {
    expect(groundMethodLabel(PROFILE)).toMatch(/classified ground/)
    expect(groundMethodLabel({ classification: { ground_method: 'low_percentile_filtered' } })).toMatch(/no classification/)
    expect(groundMethodLabel({})).toBe('')
  })

  it('builds the hover readout, flagging gaps and interpolated ground', () => {
    const h = hoverInfo(PROFILE, 3)
    expect(h.distance).toBe(3.5)
    expect(h.ground).toBe(100.3)
    expect(h.groundInterpolated).toBe(true)
    expect(h.height).toBe(7.9)
    expect(h.classes.map(c => c.code)).toEqual([6])
    const gap = hoverInfo(PROFILE, 2)
    expect(gap.gap).toBe(true)
    expect(gap.top).toBeNull()
    expect(hoverInfo(PROFILE, 9)).toBeNull()
  })

  it('formats the stat rows', () => {
    const rows = Object.fromEntries(statRows(PROFILE).map(r => [r[0], r[1]]))
    expect(rows.Length).toBe('6.0 m')
    expect(rows.Elevation).toBe('100.00 m – 100.50 m')
    expect(rows['Ascent / descent']).toBe('+0.50 / −0.00 m')
    expect(rows['Steepest grade']).toBe('+10.0 %')
    expect(rows['Max height']).toBe('12.00 m')
    expect(rows.Coverage).toBe('83 %')
    expect(statRows({})).toEqual([])
    const empty = Object.fromEntries(statRows({ stats: { bins: 3, bins_with_data: 0, points: 0 } }).map(r => [r[0], r[1]]))
    expect(empty.Elevation).toBe('—')
  })

  it('notes reduced sampling only when levels were dropped', () => {
    expect(samplingNote(PROFILE)).toBe('')
    expect(samplingNote({ sampling: { full_resolution: false, spacing: 0.4, points_read: 1000, points_available: 9000 } })).toMatch(/0\.4 m spacing/)
  })
})

describe('export and settings', () => {
  it('writes one CSV row per bin with the ground source and class columns', () => {
    const csv = profileCsv(PROFILE).trim().split('\n')
    expect(csv).toHaveLength(7)
    expect(csv[0]).toBe('distance_m,x,y,count,ground_m,ground_source,top_m,height_m,min_m,max_m,class_6_max_m,class_6_height_m,class_6_count,class_14_max_m,class_14_height_m,class_14_count')
    expect(csv[1]).toBe('0.5,0.5,0,10,100,measured,100.2,0.2,100,100.2,,,0,,,0')
    expect(csv[3]).toBe('2.5,2.5,0,0,100.2,interpolated,,,,,,,0,,,0')
    expect(csv[6]).toMatch(/,112\.5,12,3$/)
    expect(csvFilename('t1')).toBe('t1_profile.csv')
    expect(profileCsv(null)).toBe('')
  })

  it('remembers width, step and exaggeration, ignoring junk', () => {
    const store = new Map()
    const storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }
    expect(loadProfileSettings(storage)).toEqual({ width: 1, step: 0, exaggeration: 'auto' })
    saveProfileSettings({ width: 2, step: 0.5, exaggeration: 1, other: 'x' }, storage)
    expect(loadProfileSettings(storage)).toEqual({ width: 2, step: 0.5, exaggeration: 1 })
    storage.setItem('pointcloud_profile_settings', JSON.stringify({ width: 99, step: 'a', exaggeration: 3 }))
    expect(loadProfileSettings(storage)).toEqual({ width: 1, step: 0, exaggeration: 'auto' })
    storage.setItem('pointcloud_profile_settings', '{not json')
    expect(loadProfileSettings(storage)).toEqual({ width: 1, step: 0, exaggeration: 'auto' })
  })
})

describe('backend call', () => {
  beforeEach(() => { window.csrf_token = 'tok' })

  it('posts the 2D line, width and optional step', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ message: PROFILE }) }))
    const out = await fetchProfile('t1', [[1, 2, 3], [4, 5, 6]], { width: 2, step: 0.5 })
    expect(out.bins).toBe(6)
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/method/webodm_core.api.pointcloud.profile')
    const body = JSON.parse(opts.body)
    expect(body.task_name).toBe('t1')
    expect(JSON.parse(body.line)).toEqual([[1, 2], [4, 5]])
    expect(body.width).toBe(2)
    expect(body.step).toBe(0.5)
    expect(body.ground_window).toBeUndefined()
    expect(opts.headers['X-Frappe-CSRF-Token']).toBe('tok')
    await fetchProfile('t1', [[1, 2], [4, 5]], { width: 1, step: 0, groundWindow: 0 })
    const auto = JSON.parse(global.fetch.mock.calls[1][1].body)
    expect(auto.step).toBeUndefined()
    expect(auto.ground_window).toBe(0)
  })
})
