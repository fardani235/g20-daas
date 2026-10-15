import { describe, it, expect } from 'vitest'
import fixture from './__fixtures__/flightplan.json'
import { pathSegments, visibleWaypoints } from './planLayers'

const { mission } = fixture

describe('pathSegments', () => {
  it('alternates flight lines (camera on) and turns', () => {
    const segs = pathSegments(mission.waypoints)
    // 7 lines and the 6 turns between them.
    expect(segs.map(s => s.photo)).toEqual([true, false, true, false, true, false, true, false, true, false, true, false, true])
    for (const s of segs) expect(s.latlngs.length).toBeGreaterThanOrEqual(2)
    // Consecutive segments share their joint, as [lat, lng].
    expect(segs[0].latlngs.at(-1)).toEqual(segs[1].latlngs[0])
    expect(segs[0].latlngs[0]).toEqual([mission.waypoints[0].lat, mission.waypoints[0].lng])
  })

  it('keeps a line with intermediate waypoints as one segment', () => {
    const wps = [
      { lat: 0, lng: 0, leg: 0, photo: true, role: 'start' },
      { lat: 0, lng: 1, leg: 0, photo: true, role: 'via' },
      { lat: 0, lng: 2, leg: 0, photo: false, role: 'end' },
      { lat: 1, lng: 2, leg: 1, photo: true, role: 'start' },
      { lat: 1, lng: 0, leg: 1, photo: false, role: 'end' },
    ]
    const segs = pathSegments(wps)
    expect(segs.map(s => [s.photo, s.latlngs.length])).toEqual([[true, 3], [false, 2], [true, 2]])
  })

  it('is empty for no waypoints', () => {
    expect(pathSegments([])).toEqual([])
    expect(pathSegments(null)).toEqual([])
    expect(pathSegments([{ lat: 0, lng: 0, leg: 0, photo: true }])).toEqual([])
  })
})

describe('visibleWaypoints', () => {
  const many = Array.from({ length: 2000 }, (_, i) => ({ index: i, role: i % 10 === 0 ? 'start' : i % 10 === 9 ? 'end' : 'via' }))

  it('shows everything on a small mission', () => {
    expect(visibleWaypoints(mission.waypoints)).toHaveLength(mission.waypoints.length)
  })

  it('drops terrain waypoints first on a large one', () => {
    const shown = visibleWaypoints(many, { max: 600 })
    expect(shown).toHaveLength(400)
    expect(shown.every(w => w.role !== 'via')).toBe(true)
  })

  it('thins the line ends too when even those are too many, keeping the last', () => {
    const shown = visibleWaypoints(many, { max: 100 })
    expect(shown.length).toBeLessThanOrEqual(101)
    expect(shown.at(-1).index).toBe(1999)
  })
})
