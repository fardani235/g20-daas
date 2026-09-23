import { describe, it, expect } from 'vitest'
import { MOUSE, TOUCH } from 'three'
import {
  MODES,
  VIEW_PRESETS,
  hintFor,
  mouseButtonsFor,
  touchesFor,
  framingFor,
  presetPosition,
  dollyDistance,
  panStep,
  needsVertexRecenter,
  gridSizeFor,
  pixelRatioFor,
  pickModelEntry,
  resolveZipUri,
  keyAction,
  emptyStateFor,
  formatBytes,
  formatCount,
  progressPercent,
} from './modelViewer'

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)

describe('interaction modes', () => {
  it('maps the primary button to the chosen action and keeps the others conventional', () => {
    expect(mouseButtonsFor('rotate').LEFT).toBe(MOUSE.ROTATE)
    expect(mouseButtonsFor('rotate').RIGHT).toBe(MOUSE.PAN)
    expect(mouseButtonsFor('pan').LEFT).toBe(MOUSE.PAN)
    expect(mouseButtonsFor('pan').RIGHT).toBe(MOUSE.ROTATE)
    expect(mouseButtonsFor('zoom').LEFT).toBe(MOUSE.DOLLY)
    expect(mouseButtonsFor('unknown')).toEqual(mouseButtonsFor('rotate'))
  })

  it('hints follow the mode and the pointer type', () => {
    expect(hintFor('pan')).toMatch(/Drag to pan/)
    expect(hintFor('pan', true)).toMatch(/One finger to pan/)
    expect(hintFor('bogus', true)).toBe(hintFor('rotate', true))
  })

  it('one finger follows the mode; two fingers always pinch-zoom', () => {
    expect(touchesFor('pan').ONE).toBe(TOUCH.PAN)
    expect(touchesFor('rotate').ONE).toBe(TOUCH.ROTATE)
    for (const m of MODES) expect([TOUCH.DOLLY_PAN, TOUCH.DOLLY_ROTATE]).toContain(touchesFor(m).TWO)
  })
})

describe('camera framing', () => {
  const size = { x: 335, y: 27, z: 276 }

  it('frames the bounding sphere with padding and derives depth/orbit limits', () => {
    const f = framingFor(size, 16 / 9, 50)
    const radius = Math.hypot(335, 27, 276) / 2
    expect(f.radius).toBeCloseTo(radius, 5)
    expect(f.distance).toBeGreaterThan(radius) // the sphere fits
    expect(f.near).toBeLessThan(f.minDistance) // nothing clips when zoomed in fully
    expect(f.far).toBeGreaterThan(f.maxDistance + f.radius) // nothing clips when zoomed out fully
  })

  it('backs off further for narrow (portrait) viewports', () => {
    expect(framingFor(size, 0.5).distance).toBeGreaterThan(framingFor(size, 2).distance)
  })

  it('never divides by zero for degenerate sizes', () => {
    const f = framingFor({ x: 0, y: 0, z: 0 }, 1)
    expect(Number.isFinite(f.distance)).toBe(true)
    expect(f.distance).toBeGreaterThan(0)
  })

  it('places every preset at the framing distance from the center', () => {
    const center = { x: 10, y: -3, z: 7 }
    for (const p of VIEW_PRESETS) {
      expect(dist(presetPosition(p, center, 100), center)).toBeCloseTo(100, 6)
    }
  })

  it('top looks straight down and north/east sit on their axes', () => {
    const c = { x: 0, y: 0, z: 0 }
    const top = presetPosition('top', c, 100)
    expect(top.y).toBeCloseTo(100, 2)
    expect(Math.abs(top.x)).toBeLessThan(1)
    const north = presetPosition('north', c, 100)
    expect(north.z).toBeGreaterThan(0)
    expect(north.x).toBe(0)
    const east = presetPosition('east', c, 100)
    expect(east.x).toBeLessThan(0)
    expect(east.z).toBe(0)
  })

  it('dolly respects the orbit limits', () => {
    expect(dollyDistance(100, 0.5, 10, 1000)).toBe(50)
    expect(dollyDistance(15, 0.5, 10, 1000)).toBe(10)
    expect(dollyDistance(900, 2, 10, 1000)).toBe(1000)
  })

  it('keyboard pan step scales with camera distance', () => {
    expect(panStep(100, 1, 0).right).toBeCloseTo(8)
    expect(panStep(200, 0, -1).up).toBeCloseTo(-16)
  })
})

describe('placement', () => {
  it('bakes the offset only for projected (UTM-scale) coordinates', () => {
    expect(needsVertexRecenter({ x: 4.5, y: -3, z: 1742 })).toBe(false) // ODM RTC model
    expect(needsVertexRecenter({ x: 501234, y: 4395000, z: 1742 })).toBe(true)
  })

  it('picks a nice grid size at least 1.5x the footprint', () => {
    expect(gridSizeFor(335)).toBe(1000)
    expect(gridSizeFor(60)).toBe(100)
    expect(gridSizeFor(12)).toBe(20)
    expect(gridSizeFor(0)).toBe(2)
  })

  it('lowers the pixel ratio for very dense meshes', () => {
    expect(pixelRatioFor(3, 100_000)).toBe(2)
    expect(pixelRatioFor(3, 2_000_000)).toBe(1.5)
    expect(pixelRatioFor(3, 5_000_000)).toBe(1)
    expect(pixelRatioFor(1, 5_000_000)).toBe(1)
  })
})

describe('zip packages', () => {
  it('prefers .glb over .gltf and ignores directories', () => {
    expect(pickModelEntry(['odm_texturing/', 'a.gltf', 'a.bin', 'b.GLB'])).toBe('b.GLB')
    expect(pickModelEntry(['odm_texturing/', 'odm_texturing/model.gltf', 'x.bin'])).toBe('odm_texturing/model.gltf')
    expect(pickModelEntry(['readme.txt'])).toBeNull()
  })

  it('resolves relative and percent-encoded URIs against the member map', () => {
    const files = { 'odm_texturing/tex 1.png': 'blob:a', 'model.bin': 'blob:b' }
    expect(resolveZipUri('./tex%201.png', files, 'odm_texturing/')).toBe('blob:a')
    expect(resolveZipUri('model.bin', files, 'odm_texturing/')).toBe('blob:b')
    expect(resolveZipUri('missing.png', files)).toBeNull()
  })
})

describe('keyboard', () => {
  const key = (k, extra = {}) => ({ key: k, target: { tagName: 'DIV' }, ...extra })

  it('maps navigation keys to actions', () => {
    expect(keyAction(key('ArrowLeft'))).toBe('panLeft')
    expect(keyAction(key('d'))).toBe('panRight')
    expect(keyAction(key('+'))).toBe('zoomIn')
    expect(keyAction(key('-'))).toBe('zoomOut')
    expect(keyAction(key('r'))).toBe('reset')
    expect(keyAction(key('2'))).toBe('viewTop')
    expect(keyAction(key('g'))).toBe('toggleGrid')
    expect(keyAction(key('f'))).toBe('toggleFullscreen')
    expect(keyAction(key('?'))).toBe('toggleHelp')
    expect(keyAction(key('Escape'))).toBe('escape')
    expect(keyAction(key('x'))).toBeNull()
  })

  it('ignores modifier combos and typing in form fields', () => {
    expect(keyAction(key('r', { ctrlKey: true }))).toBeNull()
    expect(keyAction({ key: 'r', target: { tagName: 'INPUT' } })).toBeNull()
    expect(keyAction({ key: 'r', target: { tagName: 'SELECT' } })).toBeNull()
  })
})

describe('page state', () => {
  it('returns null when a model exists', () => {
    expect(emptyStateFor({ status: 'Completed', model: '/private/files/x.glb' })).toBeNull()
  })

  it('describes processing, failed, missing and no-model tasks', () => {
    expect(emptyStateFor({ status: 'Running' }).kind).toBe('processing')
    expect(emptyStateFor({ status: 'Queued' }).kind).toBe('processing')
    expect(emptyStateFor({ status: 'Failed' }).kind).toBe('failed')
    expect(emptyStateFor({ status: 'Cancelled' }).title).toBe('Task cancelled')
    expect(emptyStateFor({ status: 'Completed' }).kind).toBe('none')
    expect(emptyStateFor(null).kind).toBe('missing')
  })

  it('formats sizes, counts and progress', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(34483916)).toBe('33 MB')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(-1)).toBe('')
    expect(formatCount(71139)).toBe('71.1k')
    expect(formatCount(2_300_000)).toBe('2.3M')
    expect(formatCount(950)).toBe('950')
    expect(progressPercent(50, 200)).toBe(25)
    expect(progressPercent(10, 0)).toBeNull()
    expect(progressPercent(300, 200)).toBe(100)
  })
})

import { runEmptyStateFor, modelSourceOptions, runSummary, formatCount as fc } from './modelViewer'

describe('plugin-run model sources', () => {
  const done = { name: 'R1', plugin: 'acme.3d', output_kind: 'model', status: 'Completed', output_file: '/private/files/R1.glb' }

  it('reports the run state the way task states are reported', () => {
    expect(runEmptyStateFor(null).kind).toBe('missing')
    expect(runEmptyStateFor(done)).toBeNull()
    expect(runEmptyStateFor({ ...done, status: 'Running', output_file: null }).kind).toBe('processing')
    const failed = runEmptyStateFor({ ...done, status: 'Failed', output_file: null, error: 'no usable input' }, 'Recon')
    expect(failed.kind).toBe('failed')
    expect(failed.title).toBe('Recon failed')
    expect(failed.detail).toBe('no usable input')
    expect(runEmptyStateFor({ ...done, status: 'Cancelled', output_file: null }).kind).toBe('failed')
    expect(runEmptyStateFor({ ...done, output_file: null }).kind).toBe('none')
  })

  it('offers the ODM model and completed model runs as sources', () => {
    const task = { model: '/private/files/model.glb' }
    const runs = [done, { ...done, name: 'R2', status: 'Running', output_file: null }, { ...done, name: 'R3', output_kind: 'raster' }]
    expect(modelSourceOptions(task, runs, r => `Plugin ${r.name}`)).toEqual([
      { value: '', label: 'ODM textured model' },
      { value: 'R1', label: 'Plugin R1' },
    ])
    expect(modelSourceOptions({ model: null }, runs)).toEqual([{ value: 'R1', label: 'acme.3d' }])
    expect(modelSourceOptions(null, [])).toEqual([])
  })

  it('summarises run metadata from an object or a JSON string', () => {
    expect(runSummary({ output_metadata: { workflow: 'terrain', triangles: 480000, tiles: 4, epsg: 32633 } }))
      .toBe(`terrain mesh · ${fc(480000)} tris · 4 textures · EPSG:32633`)
    expect(runSummary({ output_metadata: JSON.stringify({ workflow: 'optimize-model', triangles: 12, textures: 1 }) }))
      .toBe('optimized ODM model · 12 tris · 1 texture')
    expect(runSummary({ output_metadata: '{bad' })).toBe('')
    expect(runSummary(null)).toBe('')
  })
})
