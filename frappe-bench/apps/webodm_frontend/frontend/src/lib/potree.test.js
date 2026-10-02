import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  availableColorModes,
  budgetCapFor,
  canFinish,
  centroid,
  clampElevationFilter,
  classificationLegend,
  classificationsPresent,
  colorModeOptions,
  computeVolumeNative,
  conversionLabel,
  createPotreeRequestManager,
  defaultColorMode,
  elevationRangeOf,
  getPotreeState,
  intensityRangeOf,
  isFullElevationRange,
  loadPointCloudSettings,
  localToNative,
  measurementText,
  pointCloudRoute,
  polygonArea,
  polylineLength,
  potreeBaseUrl,
  potreeFileFor,
  requestHeaders,
  savePointCloudSettings,
} from './potree'

// A metadata.json as PotreeConverter 2.1 writes it (trimmed).
const META = {
  version: '2.0',
  points: 1948233,
  projection: 'EPSG:32632',
  boundingBox: { min: [-23.73, -20.35, -5.21], max: [19.39, 22.77, 37.91] },
  attributes: [
    { name: 'position', type: 'int32', numElements: 3, min: [-23.73, -20.35, -5.21], max: [19.39, 22.71, 10.92] },
    { name: 'intensity', type: 'uint16', numElements: 1, min: [0], max: [0] },
    { name: 'classification', type: 'uint8', numElements: 1, min: [0], max: [0], histogram: [1948233, ...new Array(255).fill(0)] },
    { name: 'rgb', type: 'uint16', numElements: 3, min: [4, 5, 4], max: [255, 255, 255] },
  ],
}

const classified = {
  ...META,
  attributes: META.attributes.map(a => {
    if (a.name === 'intensity') return { ...a, min: [0], max: [412] }
    if (a.name === 'classification') {
      const histogram = new Array(256).fill(0)
      histogram[2] = 1000; histogram[6] = 400; histogram[5] = 7
      return { ...a, min: [2], max: [6], histogram }
    }
    return a
  }),
}

describe('attribute detection', () => {
  it('offers only colour modes whose attribute carries information', () => {
    expect(availableColorModes(META)).toEqual(['rgb', 'elevation'])
    expect(availableColorModes(classified)).toEqual(['rgb', 'elevation', 'intensity', 'classification'])
    expect(colorModeOptions(META).map(m => m.label)).toEqual(['RGB', 'Elevation'])
  })

  it('drops rgb when the cloud has none and falls back to elevation by default', () => {
    const noRgb = { ...META, attributes: META.attributes.filter(a => a.name !== 'rgb') }
    expect(availableColorModes(noRgb)).toEqual(['elevation'])
    expect(defaultColorMode(noRgb)).toBe('elevation')
    expect(defaultColorMode(META)).toBe('rgb')
    expect(availableColorModes(null)).toEqual([])
  })

  it('reads the tight elevation range from the position attribute', () => {
    expect(elevationRangeOf(META)).toEqual([-5.21, 10.92])
    expect(elevationRangeOf({ boundingBox: META.boundingBox })).toEqual([-5.21, 37.91])
    expect(elevationRangeOf({})).toEqual([0, 0])
    expect(intensityRangeOf(classified)).toEqual([0, 412])
    expect(intensityRangeOf(META)).toEqual([0, 1])
  })

  it('lists the classes present from the histogram, with ASPRS labels and colours', () => {
    expect(classificationsPresent(classified)).toEqual([2, 5, 6])
    expect(classificationsPresent(META)).toEqual([0])
    expect(classificationsPresent({ attributes: [{ name: 'classification', min: [1], max: [3] }] })).toEqual([1, 2, 3])
    const legend = classificationLegend(classified)
    expect(legend.map(l => l.label)).toEqual(['Ground', 'High vegetation', 'Building'])
    expect(legend[0].color).toBe('rgb(161,82,46)')
  })
})

describe('filters and settings', () => {
  it('clamps the elevation filter into the cloud range', () => {
    expect(clampElevationFilter([-100, 5], [-5, 10])).toEqual([-5, 5])
    expect(clampElevationFilter([8, 2], [-5, 10])).toEqual([2, 8])
    expect(clampElevationFilter(null, [-5, 10])).toEqual([-5, 10])
    expect(isFullElevationRange(null, [0, 1])).toBe(true)
    expect(isFullElevationRange([0, 1], [0, 1])).toBe(true)
    expect(isFullElevationRange([0.2, 1], [0, 1])).toBe(false)
  })

  it('persists size, budget and background and ignores junk', () => {
    const store = new Map()
    const storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }
    savePointCloudSettings({ pointSize: 3.5, pointBudget: 2_000_000, background: 'white', colorMode: 'rgb' }, storage)
    expect(loadPointCloudSettings(storage)).toEqual({ pointSize: 3.5, pointBudget: 2_000_000, background: 'white' })
    storage.setItem('pointcloud_viewer_settings', JSON.stringify({ pointSize: 99, pointBudget: 7, background: 'pink' }))
    expect(loadPointCloudSettings(storage)).toEqual({ pointSize: 8, pointBudget: 1_000_000, background: 'dark' })
    expect(loadPointCloudSettings({ getItem: () => { throw new Error('nope') } })).toEqual({ pointSize: 2, pointBudget: 1_000_000, background: 'dark' })
  })

  it('caps the point budget on small devices', () => {
    expect(budgetCapFor(undefined, false)).toBe(10_000_000)
    expect(budgetCapFor(16, false)).toBe(10_000_000)
    expect(budgetCapFor(4, false)).toBe(2_000_000)
    expect(budgetCapFor(2, false)).toBe(1_000_000)
    expect(budgetCapFor(16, true)).toBe(1_000_000)
  })
})

describe('loader request manager', () => {
  const files = {
    'metadata.json': '/private/files/t1_potree_metadata.json',
    'hierarchy.bin': '/private/files/t1_potree_hierarchy.bin',
    'octree.bin': '/private/files/t1_potree_octree.bin',
  }

  it('maps the virtual octree URLs to the private files with cookies and Range intact', async () => {
    const calls = []
    const fetchImpl = vi.fn(async (url, init) => { calls.push({ url, init }); return { ok: true, status: 206 } })
    const rm = createPotreeRequestManager(files, fetchImpl)
    const base = potreeBaseUrl('t1')
    expect(base).toBe('potree://t1/metadata.json')
    expect(await rm.getUrl(base)).toBe(base)
    expect(potreeFileFor(base.replace('/metadata.json', '/octree.bin'))).toBe('octree.bin')

    await rm.fetch(base)
    await rm.fetch(base.replace('/metadata.json', '/octree.bin'), { headers: { Range: 'bytes=10-19', 'content-type': 'multipart/byteranges' } })
    expect(calls[0].url).toBe(files['metadata.json'])
    expect(calls[0].init.credentials).toBe('include')
    expect(calls[1].url).toBe(files['octree.bin'])
    expect(calls[1].init.headers).toEqual({ Range: 'bytes=10-19' })
    const mapLike = new Map([['Range', 'bytes=0-1'], ['Content-Type', 'x']])
    expect(requestHeaders(mapLike)).toEqual({ Range: 'bytes=0-1' })
    expect(requestHeaders([['Range', 'bytes=2-3'], ['content-type', 'x']])).toEqual({ Range: 'bytes=2-3' })
  })

  it('rejects unknown files and surfaces auth failures', async () => {
    const rm = createPotreeRequestManager(files, async () => ({ ok: false, status: 403 }))
    await expect(rm.fetch('potree://t1/octree.bin')).rejects.toThrow(/permission/)
    await expect(rm.fetch('https://evil.example/x')).rejects.toThrow(/Unexpected/)
    const partial = createPotreeRequestManager({ 'metadata.json': '/m' }, async () => ({ ok: true, status: 200 }))
    await expect(partial.fetch('potree://t1/hierarchy.bin')).rejects.toThrow(/missing hierarchy.bin/)
  })
})

describe('measurements', () => {
  const square = [[0, 0, 10], [10, 0, 10], [10, 10, 12], [0, 10, 12]]

  it('measures 3D length and horizontal area', () => {
    expect(polylineLength([[0, 0, 0], [3, 4, 0], [3, 4, 12]])).toBe(17)
    expect(polygonArea(square)).toBe(100)
    expect(polygonArea(square.slice(0, 2))).toBe(0)
    expect(centroid(square)).toEqual([5, 5, 11])
    expect(localToNative([1, 2, 3], [500000, 4500000, 100])).toEqual([500001, 4500002, 103])
  })

  it('formats live readouts and knows when a shape can be finished', () => {
    expect(measurementText('distance', [[0, 0, 0], [3, 4, 0]])).toBe('5.0 m')
    expect(measurementText('area', square)).toMatch(/^100\.0 m²/)
    expect(measurementText('volume', square.slice(0, 2))).toMatch(/at least 3/)
    expect(measurementText('volume', square)).toMatch(/finish to compute/)
    expect(canFinish('distance', square.slice(0, 1))).toBe(false)
    expect(canFinish('distance', square.slice(0, 2))).toBe(true)
    expect(canFinish('area', square.slice(0, 2))).toBe(false)
    expect(canFinish('volume', square.slice(0, 3))).toBe(true)
  })
})

describe('backend calls', () => {
  beforeEach(() => { window.csrf_token = 'tok' })

  it('asks for the octree state and starts the conversion when opening', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ message: { status: 'Queued' } }) }))
    const state = await getPotreeState('t1', { start: true })
    expect(state.status).toBe('Queued')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/method/webodm_core.api.pointcloud.potree_state')
    expect(JSON.parse(opts.body)).toEqual({ task_name: 't1', start: 1, retry: 0 })
    expect(opts.headers['X-Frappe-CSRF-Token']).toBe('tok')
  })

  it('sends volume polygons in the cloud CRS to the DSM endpoint and closes the ring', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ message: { volume: 12.4, fill: 12.4, cut: 0, area: 100 } }) }))
    const text = await computeVolumeNative('t1', [[0, 0, 1], [10, 0, 1], [10, 10, 1]], 'EPSG:32632', 'plane')
    expect(text).toMatch(/12 m³/)
    const body = JSON.parse(global.fetch.mock.calls[0][1].body)
    expect(body.task_name).toBe('t1')
    expect(body.method).toBe('plane')
    expect(body.polygon_crs).toBe('EPSG:32632')
    expect(JSON.parse(body.polygon).coordinates[0]).toEqual([[0, 0], [10, 0], [10, 10], [0, 0]])
  })

  it('describes conversion states and builds the route', () => {
    expect(conversionLabel({ status: 'Queued' })).toMatch(/queued/)
    expect(conversionLabel({ status: 'Running' })).toMatch(/converting/)
    expect(conversionLabel({ status: 'Ready', cache: 'warming' })).toMatch(/storage/)
    expect(pointCloudRoute('P 1', 't1')).toBe('/project/P%201/task/t1/model?source=pointcloud')
  })
})
