import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as rm from './rasterMetadata.js'

const ORTHO = {
  dataset: 'orthophoto', status: 'Extracted', driver: 'GTiff',
  width: 14718, height: 11640, band_count: 4, dtype: 'uint8',
  crs: { epsg: 32632, wkt: null, units: 'metre' }, georeference: 'full',
  pixel_size: [0.049997992101743646, 0.049996591718544175],
  bounds: [321875.39, 5157673.86, 322611.26, 5158255.82],
  bounds_4326: [6.676, 46.549, 6.686, 46.554],
  nodata: null, is_tiled: true, block_size: [256, 256], compression: 'deflate',
  overviews: [2, 4, 8, 16, 32, 64], overview_count: 6, is_cog: true,
  color_interpretation: ['red', 'green', 'blue', 'alpha'], file_size: 214119658,
  software: 'ODM 3.5.6',
}
const DSM = { ...ORTHO, dataset: 'dsm', band_count: 1, dtype: 'float32', nodata: -9999,
  color_interpretation: ['gray'], overview_count: 2, overviews: [2, 4] }
const FAILED = { dataset: 'dtm', status: 'Failed', error: 'cannot open raster', width: null }
const NO_CRS = { ...ORTHO, crs: { epsg: null, wkt: null, units: null }, georeference: 'no_crs',
  bounds_4326: null }

describe('getRasterMetadata', () => {
  beforeEach(() => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ message: { orthophoto: ORTHO } }) }))
    global.window = { csrf_token: 'x' }
  })

  it('GETs all datasets for a task', async () => {
    const out = await rm.getRasterMetadata('T1')
    expect(out.orthophoto.width).toBe(14718)
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toContain('webodm_core.api.task.get_raster_metadata?task_name=T1')
    expect(url).not.toContain('dataset=')
    expect(opts.method).toBe('GET')
  })

  it('passes dataset and refresh', async () => {
    await rm.getRasterMetadata('T1', { dataset: 'dsm', refresh: true })
    const [url] = global.fetch.mock.calls[0]
    expect(url).toContain('dataset=dsm')
    expect(url).toContain('refresh=1')
  })

  it('surfaces a Frappe error message', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: false, json: () => Promise.resolve({ exception: 'PermissionError: nope' }) }))
    await expect(rm.getRasterMetadata('T1')).rejects.toThrow()
  })
})

describe('formatting', () => {
  it('formatBytes', () => {
    expect(rm.formatBytes(0)).toBe('0 B')
    expect(rm.formatBytes(2048)).toBe('2.0 KB')
    expect(rm.formatBytes(214119658)).toBe('204 MB')
    expect(rm.formatBytes(3.5 * 1024 ** 3)).toBe('3.5 GB')
    expect(rm.formatBytes(null)).toBe('—')
  })

  it('dimensions and megapixels', () => {
    expect(rm.formatDimensions(ORTHO)).toBe('14,718 × 11,640 px')
    expect(rm.formatMegapixels(ORTHO)).toBe('171 MP')
    expect(rm.formatMegapixels({ ...ORTHO, width: 2820, height: 2876 })).toBe('8.1 MP')
    expect(rm.formatDimensions(FAILED)).toBe('—')
  })

  it('pixel size in cm for metric CRS, degrees for geographic', () => {
    expect(rm.formatPixelSize(ORTHO)).toBe('5.00 cm/px')
    expect(rm.formatPixelSize({ ...ORTHO, pixel_size: [0.3, 0.3] })).toBe('30.0 cm/px')
    expect(rm.formatPixelSize({ ...ORTHO, pixel_size: [2, 2] })).toBe('2.00 m/px')
    expect(rm.formatPixelSize({ ...ORTHO, crs: { epsg: 4326, units: 'degree' }, pixel_size: [0.00001, 0.00001] }))
      .toBe('0.000010°/px')
    expect(rm.formatPixelSize(NO_CRS)).toBe('0.05000 units/px')
    expect(rm.formatPixelSize(FAILED)).toBe('—')
  })

  it('gsdMetres only for metric units', () => {
    expect(rm.gsdMetres(ORTHO)).toBeCloseTo(0.049997, 5)
    expect(rm.gsdMetres(NO_CRS)).toBeNull()
    expect(rm.gsdMetres({ ...ORTHO, crs: { epsg: 4326, units: 'degree' } })).toBeNull()
  })

  it('crs and georeference labels', () => {
    expect(rm.crsLabel(ORTHO)).toBe('EPSG:32632')
    expect(rm.crsLabel({ ...ORTHO, crs: { epsg: null, wkt: 'PROJCS[...]' } })).toBe('Custom (WKT)')
    expect(rm.crsLabel(NO_CRS)).toBe('Missing')
    expect(rm.georeferenceLabel(ORTHO)).toBe('Georeferenced')
    expect(rm.georeferenceLabel(NO_CRS)).toMatch(/No CRS/)
    expect(rm.georeferenceLabel(FAILED)).toBe('Not georeferenced')
  })

  it('nodata: none, number, NaN string', () => {
    expect(rm.formatNodata(ORTHO)).toBe('None')
    expect(rm.formatNodata(DSM)).toBe('-9999')
    expect(rm.formatNodata({ ...DSM, nodata: 'nan' })).toBe('NAN')
  })

  it('bands and layout', () => {
    expect(rm.formatBands(ORTHO)).toBe('4 uint8 (Red, Green, Blue, Alpha)')
    expect(rm.formatBands(DSM)).toBe('1 float32 (Gray)')
    expect(rm.formatLayout(ORTHO)).toBe('256×256 tiles · DEFLATE · 6 overviews · COG')
    expect(rm.formatLayout({ ...ORTHO, is_tiled: false, block_size: [14718, 1], compression: null,
      overview_count: 0, is_cog: false })).toBe('strips · no overviews')
  })

  it('bounds precision follows the CRS', () => {
    expect(rm.formatBounds(ORTHO)).toBe('321875.39, 5157673.86 – 322611.26, 5158255.82')
    expect(rm.formatBounds({ ...ORTHO, crs: { epsg: 4326, units: 'degree' }, bounds: [6.1, 46.1, 6.2, 46.2] }))
      .toBe('6.100000, 46.100000 – 6.200000, 46.200000')
    expect(rm.formatBounds(FAILED)).toBe('—')
  })

  it('summarize', () => {
    expect(rm.summarize(ORTHO)).toBe('14,718 × 11,640 px · 4 bands · 5.00 cm/px · EPSG:32632 · 204 MB')
    expect(rm.summarize(FAILED)).toBe('Metadata unavailable')
    expect(rm.summarize(null)).toBe('')
  })

  it('detailRows lists facts, or the error for a Failed row', () => {
    const labels = rm.detailRows(ORTHO).map(r => r.label)
    expect(labels).toEqual(['Size', 'Bands', 'Pixel size', 'CRS', 'Georeference', 'Bounds', 'NoData', 'Layout', 'File', 'Software'])
    expect(rm.detailRows(ORTHO).find(r => r.label === 'CRS').value).toBe('EPSG:32632 · metre')
    expect(rm.detailRows(FAILED)).toEqual([
      { label: 'Status', value: 'Extraction failed' },
      { label: 'Error', value: 'cannot open raster' },
    ])
    expect(rm.detailRows(null)).toEqual([])
  })
})

describe('maxNativeZoomFor', () => {
  it('derives the zoom from GSD and latitude', () => {
    // 5 cm at 46.5°N: 156543 * cos(46.5°) / 0.05 ≈ 2.15e6 → log2 ≈ 21.04 → 22
    expect(rm.maxNativeZoomFor(ORTHO)).toBe(22)
    // 30 cm coarse output → 19/20
    expect(rm.maxNativeZoomFor({ ...ORTHO, pixel_size: [0.3, 0.3] })).toBe(19)
    // 1 cm ultra-fine, capped
    expect(rm.maxNativeZoomFor({ ...ORTHO, pixel_size: [0.01, 0.01] })).toBe(24)
  })

  it('falls back when GSD is unknown', () => {
    expect(rm.maxNativeZoomFor(null)).toBe(rm.DEFAULT_MAX_NATIVE_ZOOM)
    expect(rm.maxNativeZoomFor(NO_CRS)).toBe(rm.DEFAULT_MAX_NATIVE_ZOOM)
    expect(rm.maxNativeZoomFor(FAILED)).toBe(rm.DEFAULT_MAX_NATIVE_ZOOM)
  })

  it('uses the equator when 4326 bounds are missing', () => {
    const z = rm.maxNativeZoomFor({ ...ORTHO, bounds_4326: null })
    expect(z).toBe(22) // log2(156543/0.05) = 21.6 → 22
  })
})
