import { describe, it, expect, vi, afterEach } from 'vitest'
import fixture from './__fixtures__/flightplan.json'
import {
  MIN_VERTICES,
  basisInfo,
  buildDefinition,
  cameraAfterDroneChange,
  cameraSpecRows,
  camerasFor,
  canPreview,
  defaultParams,
  detailRows,
  droneSpecRows,
  emptyPlanState,
  estimateRows,
  exportPlan,
  exportVerdict,
  filenameFromDisposition,
  formatAreaHa,
  formatDirection,
  formatDuration,
  formatGsd,
  formatLength,
  geometryFor,
  groupDrones,
  groupIssues,
  gsdCm,
  heightForGsd,
  insertVertex,
  listPlans,
  midpoints,
  missionSupportSummary,
  moveVertex,
  planFingerprint,
  planStatus,
  previewPlan,
  profilePaths,
  removeVertex,
  stateFromPlan,
  supportLevel,
  verticesFrom,
} from './flightplan'

const { catalog, mission } = fixture
const specs = catalog.params
const drone = id => catalog.drones.find(d => d.id === id)
const camera = id => catalog.cameras.find(c => c.id === id)

afterEach(() => { vi.restoreAllMocks() })

describe('photogrammetry (mirrors the server)', () => {
  it('matches the vendors published resolution figures', () => {
    // Phantom 4 Pro and Mavic 3E: H / 36.5 cm/px; Zenmuse P1 35 mm: H / 80.
    expect(gsdCm(camera('dji-mavic-3e-wide'), 100)).toBeCloseTo(100 / 36.5, 1)
    expect(gsdCm(camera('dji-zenmuse-p1-35mm'), 100)).toBeCloseTo(1.25, 2)
  })

  it('agrees with the mission the server computed', () => {
    expect(gsdCm(camera('dji-mavic-3e-wide'), mission.camera.altitude_m)).toBeCloseTo(mission.camera.gsd_cm, 2)
  })

  it('height and resolution invert each other', () => {
    const cam = camera('dji-mini-4-pro-12mp')
    for (const h of [30, 80, 120]) expect(heightForGsd(cam, gsdCm(cam, h))).toBeCloseTo(h, 6)
  })

  it('returns null rather than nonsense without a camera or a positive input', () => {
    expect(gsdCm(null, 100)).toBeNull()
    expect(gsdCm(camera('dji-mavic-3e-wide'), 0)).toBeNull()
    expect(heightForGsd(camera('dji-mavic-3e-wide'), -1)).toBeNull()
  })
})

describe('equipment helpers', () => {
  it('groups drones by manufacturer with custom profiles first and generic last', () => {
    const custom = { ...drone('dji-mavic-3e'), id: 'custom:x', label: 'Acme Hexa', source: 'custom', manufacturer: 'Acme' }
    const groups = groupDrones([...catalog.drones, custom])
    expect(groups[0].label).toBe('Your organization')
    expect(groups[0].items.map(d => d.id)).toEqual(['custom:x'])
    expect(groups[groups.length - 1].label).toBe('Generic')
    const dji = groups.find(g => g.label === 'DJI')
    expect(dji.items.map(d => d.label)).toEqual([...dji.items.map(d => d.label)].sort())
  })

  it('lists the cameras a drone carries, default first', () => {
    expect(camerasFor(drone('dji-mavic-3e'), catalog.cameras).map(c => c.id)).toEqual(['dji-mavic-3e-wide'])
    const m350 = camerasFor(drone('dji-matrice-350-rtk'), catalog.cameras)
    expect(m350[0].id).toBe('dji-zenmuse-p1-35mm')
    expect(m350.length).toBeGreaterThan(2)
    expect(camerasFor(null, catalog.cameras)).toEqual([])
  })

  it('keeps the camera when the new drone carries it, else falls back to its default', () => {
    const m350 = drone('dji-matrice-350-rtk')
    expect(cameraAfterDroneChange(m350, catalog.cameras, 'dji-zenmuse-p1-50mm')).toBe('dji-zenmuse-p1-50mm')
    expect(cameraAfterDroneChange(m350, catalog.cameras, 'dji-mavic-3e-wide')).toBe('dji-zenmuse-p1-35mm')
    expect(cameraAfterDroneChange(null, catalog.cameras, 'x')).toBeNull()
  })

  it('says how missions reach each kind of aircraft', () => {
    expect(missionSupportSummary(drone('dji-mavic-3e'))).toBe('Mission files: DJI Pilot 2 / FlightHub 2')
    expect(missionSupportSummary(drone('dji-mini-4-pro'))).toBe('Mission files (unofficial): DJI Fly (Waypoint Flight)')
    expect(missionSupportSummary(drone('wingtra-wingtraone-gen2'))).toBe('WingtraPilot plans its own lines from the boundary.')
    expect(missionSupportSummary(drone('dji-mini-3'))).toMatch(/No autonomous waypoint flight/)
    expect(missionSupportSummary(null)).toBe('')
  })

  it('never shows an unknown figure as zero', () => {
    const rows = Object.fromEntries(droneSpecRows(drone('generic-mavlink-multirotor')).map(r => [r.label, r.value]))
    expect(rows['Wind limit']).toBe('not published')
    expect(rows['Service ceiling']).toBe('not published')
    expect(rows['Rated flight time']).toBe('25 min')
    const cam = Object.fromEntries(cameraSpecRows(camera('sony-a6000-20mm')).map(r => [r.label, r.value]))
    expect(cam['Shortest photo interval']).toBe('not published (2 s assumed)')
    expect(cam.Image).toBe('6000 × 4000 px (24 MP)')
    expect(droneSpecRows(null)).toEqual([])
  })

  it('maps support levels to a label and a tone', () => {
    expect(supportLevel('official')).toMatchObject({ label: 'Supported', variant: 'success' })
    expect(supportLevel('community')).toMatchObject({ label: 'Unofficial', variant: 'warning' })
    expect(supportLevel('boundary').label).toBe('Boundary import')
    expect(supportLevel('reference').label).toBe('Reference')
    expect(supportLevel('whatever').label).toBe('')
  })
})

describe('plan definition', () => {
  const square = [{ lat: 0, lng: 0 }, { lat: 0, lng: 0.001 }, { lat: 0.001, lng: 0.001 }, { lat: 0.001, lng: 0 }]

  it('starts from the servers defaults', () => {
    const d = defaultParams(specs)
    expect(d.altitude_m).toBe(100)
    expect(d.front_overlap).toBe(80)
    expect(d.direction).toBe('auto')
    expect(d.speed_ms).toBeNull()
    const state = emptyPlanState(specs)
    expect(state.type).toBe('area')
    expect(state.vertices).toEqual([])
    expect(state.elevationSource).toBe('auto')
  })

  it('builds GeoJSON only once there are enough corners', () => {
    expect(geometryFor('area', square.slice(0, 2))).toBeNull()
    expect(geometryFor('corridor', square.slice(0, 1))).toBeNull()
    const poly = geometryFor('area', square)
    expect(poly.type).toBe('Polygon')
    expect(poly.coordinates[0]).toHaveLength(5)
    expect(poly.coordinates[0][0]).toEqual(poly.coordinates[0][4])       // closed
    expect(poly.coordinates[0][1]).toEqual([0.001, 0])                    // [lng, lat]
    expect(geometryFor('corridor', square.slice(0, 2))).toEqual({ type: 'LineString', coordinates: [[0, 0], [0.001, 0]] })
    expect(MIN_VERTICES).toEqual({ area: 3, corridor: 2 })
  })

  it('round-trips vertices through GeoJSON', () => {
    expect(verticesFrom(geometryFor('area', square))).toEqual(square)
    expect(verticesFrom(geometryFor('corridor', square))).toEqual(square)
    expect(verticesFrom(null)).toEqual([])
  })

  it('sends only what the user changed, and only the input half of height / resolution', () => {
    const state = emptyPlanState(specs)
    Object.assign(state, { vertices: square, drone: 'dji-mavic-3e', camera: 'dji-mavic-3e-wide', takeoff: { lat: 0.0005, lng: -0.001 } })
    state.params.gsd_cm = 2.73            // the derived read-out
    let d = buildDefinition(state, specs)
    expect(d.params).toEqual({})
    expect(d.takeoff).toEqual([-0.001, 0.0005])
    expect(d.corridor_width_m).toBeNull()
    expect(d.elevation_source).toBe('auto')

    state.params.altitude_m = 60
    state.params.side_overlap = 75
    state.params.speed_ms = 8
    expect(buildDefinition(state, specs).params).toEqual({ altitude_m: 60, side_overlap: 75, speed_ms: 8 })

    state.params.altitude_from = 'gsd'
    state.params.gsd_cm = 2
    d = buildDefinition(state, specs)
    expect(d.params.altitude_from).toBe('gsd')
    expect(d.params.gsd_cm).toBe(2)
    expect(d.params).not.toHaveProperty('altitude_m')

    state.type = 'corridor'
    state.corridorWidth = '80'
    d = buildDefinition(state, specs)
    expect(d.corridor_width_m).toBe(80)
    expect(d.geometry.type).toBe('LineString')
  })

  it('restores the editor from a saved plan and fills the derived read-out', () => {
    const plan = {
      title: 'North field', description: 'Block 1',
      definition: { ...fixture.definition, params: { altitude_m: 100 } },
      mission,
    }
    const state = stateFromPlan(plan, specs)
    expect(state.title).toBe('North field')
    expect(state.vertices).toHaveLength(4)
    expect(state.takeoff).toEqual({ lat: fixture.definition.takeoff[1], lng: fixture.definition.takeoff[0] })
    expect(state.drone).toBe('dji-mavic-3e')
    expect(state.params.altitude_m).toBe(100)
    expect(state.params.front_overlap).toBe(80)                           // default kept
    expect(state.params.gsd_cm).toBe(mission.camera.gsd_cm)               // shown, not stored
    // Saving it again stores exactly what was stored.
    expect(buildDefinition(state, specs).params).toEqual({})
  })

  it('fingerprints everything a save stores', () => {
    const state = emptyPlanState(specs)
    Object.assign(state, { vertices: square, drone: 'dji-mavic-3e', camera: 'dji-mavic-3e-wide', title: 'A' })
    const before = planFingerprint(state, specs)
    state.params.gsd_cm = 9.9                                             // derived: not a change
    expect(planFingerprint(state, specs)).toBe(before)
    state.title = 'B'
    expect(planFingerprint(state, specs)).not.toBe(before)
    state.title = 'A'
    state.params.speed_ms = 7
    expect(planFingerprint(state, specs)).not.toBe(before)
  })

  it('knows when there is enough to compute', () => {
    const state = emptyPlanState(specs)
    expect(canPreview(state)).toBe(false)
    state.drone = 'dji-mavic-3e'
    state.vertices = square.slice(0, 2)
    expect(canPreview(state)).toBe(false)
    state.type = 'corridor'
    expect(canPreview(state)).toBe(true)
  })
})

describe('vertex editing', () => {
  const tri = [{ lat: 0, lng: 0 }, { lat: 0, lng: 2 }, { lat: 2, lng: 0 }]

  it('moves, inserts and removes without mutating', () => {
    const moved = moveVertex(tri, 1, { lat: 5, lng: 5 })
    expect(moved[1]).toEqual({ lat: 5, lng: 5 })
    expect(tri[1]).toEqual({ lat: 0, lng: 2 })
    const more = insertVertex(tri, 0, { lat: 0, lng: 1 })
    expect(more.map(v => v.lng)).toEqual([0, 1, 2, 0])
    expect(removeVertex(more, 1, 'area')).toEqual(tri)
  })

  it('never goes below the minimum for the shape', () => {
    expect(removeVertex(tri, 0, 'area')).toBe(tri)
    expect(removeVertex(tri, 0, 'corridor')).toHaveLength(2)
    expect(removeVertex(tri.slice(0, 2), 0, 'corridor')).toHaveLength(2)
  })

  it('offers a midpoint on every edge, closing a polygon', () => {
    expect(midpoints(tri, 'area')).toEqual([
      { after: 0, lat: 0, lng: 1 }, { after: 1, lat: 1, lng: 1 }, { after: 2, lat: 1, lng: 0 },
    ])
    expect(midpoints(tri, 'corridor')).toHaveLength(2)
    expect(midpoints(tri.slice(0, 1), 'area')).toEqual([])
  })
})

describe('formatting', () => {
  it('formats durations, lengths, areas and resolution', () => {
    expect(formatDuration(42)).toBe('42 s')
    expect(formatDuration(600)).toBe('10 min')
    expect(formatDuration(3900)).toBe('1 h 05 min')
    expect(formatDuration(-1)).toBe('—')
    expect(formatLength(640)).toBe('640 m')
    expect(formatLength(3624)).toBe('3.62 km')
    expect(formatLength(23400)).toBe('23.4 km')
    expect(formatAreaHa(8200)).toBe('8,200 m²')
    expect(formatAreaHa(120000)).toBe('12.0 ha')
    expect(formatAreaHa(45000)).toBe('4.50 ha')
    expect(formatAreaHa(2500000)).toBe('2.50 km²')
    expect(formatGsd(2.731)).toBe('2.73 cm/px')
    expect(formatGsd(0)).toBe('—')
    expect(formatDirection(90)).toBe('90° (E–W)')
    expect(formatDirection(0)).toBe('0° (N–S)')
    expect(formatDirection(45)).toBe('45° (NE–SW)')
  })

  it('summarises a mission as estimates', () => {
    const rows = Object.fromEntries(estimateRows(mission).map(r => [r.key, r.value]))
    expect(rows.area).toBe('18.0 ha')
    expect(rows.photos).toBe('196')
    expect(rows.batteries).toBe('1')
    // Over sloping ground the resolution is a range, not one number.
    expect(rows.gsd).toMatch(/^\d+\.\d+–\d+\.\d+ cm\/px$/)
    expect(estimateRows(null)).toEqual([])
    const flat = { ...mission, terrain: null }
    expect(Object.fromEntries(estimateRows(flat).map(r => [r.key, r.value])).gsd).toBe('2.73 cm/px')
  })

  it('lists the details', () => {
    const rows = Object.fromEntries(detailRows(mission).map(r => [r.label, r.value]))
    expect(rows['Flight height']).toBe('100 m')
    expect(rows['Flight lines']).toBe('7, 90° (E–W), automatic')
    expect(rows['Photo spacing']).toMatch(/m \(every .* s\)$/)
    expect(detailRows(null)).toEqual([])
  })
})

describe('findings', () => {
  it('words the basis so an estimate cannot pass for a fact', () => {
    expect(basisInfo('checked').label).toBe('Checked against profile')
    expect(basisInfo('estimated').label).toBe('Estimate')
    expect(basisInfo('advisory').label).toBe('Advisory')
    expect(basisInfo('not_checked').label).toBe('Not checked')
    // Guidance on using a file is not a check and carries no badge.
    expect(basisInfo('note').label).toBe('')
    // Anything unknown is treated as not checked, never as checked.
    expect(basisInfo('verified').label).toBe('Not checked')
    expect(basisInfo(undefined).label).toBe('Not checked')
    for (const b of ['checked', 'estimated', 'advisory', 'not_checked']) {
      expect(basisInfo(b).label.toLowerCase()).not.toContain('verified')
    }
  })

  it('groups by severity and summarises the plan', () => {
    const g = groupIssues(mission.issues)
    expect(g.error).toHaveLength(0)
    expect(g.warning.map(i => i.code).sort()).toEqual(['TERRAIN_OVERLAP', 'VLOS_DISTANCE'])
    expect(planStatus(mission.summary)).toEqual({ label: '2 warnings to review', variant: 'warning' })
    expect(planStatus({ error: 1, warning: 3 })).toEqual({ label: '1 problem to fix', variant: 'destructive' })
    expect(planStatus({ error: 0, warning: 0 })).toEqual({ label: 'No problems found', variant: 'success' })
    expect(planStatus(null).label).toBe('Not computed')
  })

  it('states the export verdict per format', () => {
    const byId = Object.fromEntries(fixture.export.formats.map(f => [f.id, f]))
    expect(exportVerdict(byId['dji-wpml']).state).toBe('acknowledge')
    expect(exportVerdict(byId['qgc-plan'])).toMatchObject({ state: 'unavailable' })
    expect(exportVerdict(byId['qgc-plan']).text).toMatch(/does not read this format/)
    expect(exportVerdict(byId['kml-boundary']).state).toBe('ready')
    const blocked = fixture.export_blocked.formats.find(f => f.id === 'dji-wpml')
    const v = exportVerdict(blocked)
    expect(v.state).toBe('blocked')
    expect(v.text).toMatch(/^Blocked by 1 problem: Flight speed 18 m\/s/)
    expect(exportVerdict({ ...byId.kml, files: 3 }).text).toBe('3 files in a zip, one per flight.')
  })
})

describe('terrain profile paths', () => {
  it('scales the profile into the box', () => {
    const p = profilePaths(mission.terrain.profile, 300, 100)
    expect(p.ground.startsWith('M')).toBe(true)
    expect(p.flight.split('L').length).toBe(mission.terrain.profile.length)
    expect(p.area.endsWith('Z')).toBe(true)
    expect(p.max).toBeGreaterThan(p.min)
    // Flight is above ground: a smaller y at the same x.
    const row = mission.terrain.profile[3]
    expect(p.y(row[2])).toBeLessThan(p.y(row[1]))
    expect(p.x(mission.terrain.profile[0][0])).toBeCloseTo(4)
    expect(p.x(mission.terrain.profile.at(-1)[0])).toBeCloseTo(296)
  })

  it('returns null without a usable profile', () => {
    expect(profilePaths(null, 300, 100)).toBeNull()
    expect(profilePaths([[0, 1, 2]], 300, 100)).toBeNull()
    expect(profilePaths([[5, 1, 2], [5, 1, 2]], 300, 100)).toBeNull()
  })

  it('handles perfectly flat data', () => {
    const p = profilePaths([[0, 100, 100], [50, 100, 100]], 300, 100)
    expect(Number.isFinite(p.y(100))).toBe(true)
  })
})

describe('client', () => {
  it('reads the file name from Content-Disposition', () => {
    expect(filenameFromDisposition('attachment; filename="north-field-dji-wpml.kmz"')).toBe('north-field-dji-wpml.kmz')
    expect(filenameFromDisposition("attachment; filename*=UTF-8''plan%20a.kml")).toBe('plan a.kml')
    expect(filenameFromDisposition(null)).toBe('')
  })

  it('posts the definition with the CSRF token', async () => {
    window.csrf_token = 'tok'
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ message: { ok: true, mission } }) }))
    vi.stubGlobal('fetch', fetchMock)
    const out = await previewPlan({ definition: fixture.definition, project: 'P1', terrain: 'cached' })
    expect(out.ok).toBe(true)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/method/webodm_core.api.flightplan.preview')
    expect(init.method).toBe('POST')
    expect(init.headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(JSON.parse(init.body)).toMatchObject({ project: 'P1', terrain: 'cached', definition: { type: 'area' } })
    vi.unstubAllGlobals()
  })

  it('waits for the session token before the first request of a fresh page', async () => {
    window.csrf_token = null
    const fetchMock = vi.fn(async url => (String(url).includes('csrf.get_token')
      ? { ok: true, json: async () => ({ message: 'fresh' }) }
      : { ok: true, json: async () => ({ message: [] }) }))
    vi.stubGlobal('fetch', fetchMock)
    await listPlans('P1')
    expect(fetchMock.mock.calls.map(c => String(c[0]).split('.').pop())).toEqual(['get_token', 'list_plans'])
    expect(fetchMock.mock.calls[1][1].headers['X-Frappe-CSRF-Token']).toBe('fresh')
    await listPlans('P1')
    expect(fetchMock).toHaveBeenCalledTimes(3)                            // the token is fetched once
    vi.unstubAllGlobals()
  })

  it('downloads an export and surfaces a refusal', async () => {
    window.csrf_token = 'tok'
    const blob = new Blob(['x'])
    const headers = { get: k => ({ 'Content-Disposition': 'attachment; filename="a.kmz"', 'X-Flight-Plan-Files': '2' }[k]) }
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => blob, headers })))
    const out = await exportPlan('PLAN1', { format: 'dji-wpml', trigger: 'distance', acknowledged: true })
    expect(out).toEqual({ blob, filename: 'a.kmz', files: 2 })
    const body = JSON.parse(fetch.mock.calls[0][1].body)
    expect(body).toMatchObject({ name: 'PLAN1', format: 'dji-wpml', trigger: 'distance', acknowledged: 1 })

    const refusal = { _server_messages: JSON.stringify([JSON.stringify({ message: 'This plan has warnings that must be acknowledged' })]) }
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => refusal })))
    await expect(exportPlan('PLAN1', { format: 'dji-wpml' })).rejects.toThrow('must be acknowledged')
    vi.unstubAllGlobals()
  })
})
