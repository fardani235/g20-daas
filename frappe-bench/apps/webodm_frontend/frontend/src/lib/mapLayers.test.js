import { describe, it, expect } from 'vitest'
import { BASEMAPS, getBasemapDef, createBasemap } from '@/lib/mapLayers'

describe('BASEMAPS', () => {
  it('defines exactly the three approved basemaps', () => {
    expect(BASEMAPS.map(b => b.id)).toEqual(['osm', 'esri-satellite', 'esri-topo'])
  })
  it('gives every basemap a url and label', () => {
    for (const b of BASEMAPS) {
      expect(b.url).toMatch(/\{z\}.*\{x\}.*\{y\}|\{z\}.*\{y\}.*\{x\}/)
      expect(b.label.length).toBeGreaterThan(0)
    }
  })
})

describe('getBasemapDef', () => {
  it('resolves a known id', () => {
    expect(getBasemapDef('esri-satellite').label).toBe('Satellite')
  })
  it('falls back to osm for an unknown id', () => {
    expect(getBasemapDef('nope').id).toBe('osm')
  })
})

describe('createBasemap', () => {
  it('returns a Leaflet tile layer with the def url', () => {
    const layer = createBasemap('osm')
    expect(layer).toBeTruthy()
    expect(layer._url).toBe(getBasemapDef('osm').url)
  })
})
