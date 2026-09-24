import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { createApp, nextTick } from 'vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { taskId: 'T1' } }) }))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import Console from './Console.vue'

const ORTHO = {
  dataset: 'orthophoto', status: 'Extracted', driver: 'GTiff', width: 14718, height: 11640,
  band_count: 4, dtype: 'uint8', crs: { epsg: 32632, wkt: null, units: 'metre' }, georeference: 'full',
  pixel_size: [0.05, 0.05], bounds: [321875.39, 5157673.86, 322611.26, 5158255.82],
  bounds_4326: [6.676, 46.549, 6.686, 46.554], nodata: null, is_tiled: true, block_size: [256, 256],
  compression: 'deflate', overviews: [2, 4], overview_count: 2, is_cog: true,
  color_interpretation: ['red', 'green', 'blue', 'alpha'], file_size: 214119658, software: 'ODM 3.5.6',
}
const DSM_FAILED = { dataset: 'dsm', status: 'Failed', error: 'cannot open raster: boom' }

let task
let metaResponse
let calls

function respond(url) {
  calls.push(url)
  if (url.includes('get_task_progress')) return { message: task }
  if (url.includes('get_task_console')) return { message: { lines: [], next_line: 0 } }
  if (url.includes('get_raster_metadata')) {
    if (url.includes('dataset=dsm')) return { message: { ...DSM_FAILED, ...metaResponse.dsmRetry } }
    return { message: metaResponse.all }
  }
  return {}
}

let mounted = []
function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(Console)
  app.mount(el)
  mounted.push({ app, el })
  return el
}
const flush = async () => { for (let i = 0; i < 6; i++) await nextTick(); await new Promise(r => setTimeout(r, 0)) }

beforeEach(() => {
  calls = []
  task = { name: 'T1', title: 'Survey', status: 'Completed', resolution: 0, images: [],
    orthophoto: '/private/files/o.tif', dsm: '/private/files/d.tif' }
  metaResponse = { all: { orthophoto: ORTHO, dsm: DSM_FAILED, dtm: null }, dsmRetry: {} }
  global.window.csrf_token = 'x'
  global.fetch = vi.fn(url => Promise.resolve({ ok: true, json: () => Promise.resolve(respond(url)) }))
})
afterEach(() => {
  mounted.forEach(({ app, el }) => { app.unmount(); el.remove() })
  mounted = []
})

describe('Console raster metadata panel', () => {
  it('renders a card per raster with the normalized facts', async () => {
    const el = mount()
    await flush()
    expect(calls.some(u => u.includes('get_raster_metadata?task_name=T1'))).toBe(true)

    const details = el.querySelector('details')
    expect(details).not.toBeNull()
    expect(details.querySelector('summary').textContent).toMatch(/Raster metadata/)
    const cards = [...details.querySelectorAll('[data-raster]')]
    expect(cards.map(c => c.querySelector('span.font-medium').textContent)).toEqual(['Orthophoto', 'DSM'])

    const ortho = cards[0].textContent
    expect(ortho).toContain('14,718 × 11,640 px (171 MP)')
    expect(ortho).toContain('4 uint8 (Red, Green, Blue, Alpha)')
    expect(ortho).toContain('5.00 cm/px')
    expect(ortho).toContain('EPSG:32632 · metre')
    expect(ortho).toContain('256×256 tiles · DEFLATE · 2 overviews · COG')
    expect(ortho).toContain('GTiff · 204 MB')
    expect(ortho).toContain('ODM 3.5.6')
    expect(ortho).toContain('None') // nodata
  })

  it('header resolution falls back to the orthophoto GSD', async () => {
    const el = mount()
    await flush()
    expect(el.textContent).toContain('Resolution: 5.00 cm/px')
  })

  it('a Failed row shows the error and a retry that re-extracts just that dataset', async () => {
    const el = mount()
    await flush()
    const dsm = el.querySelector('[data-raster="dsm"]')
    expect(dsm.textContent).toContain('Unavailable')
    expect(dsm.textContent).toContain('cannot open raster: boom')
    const retry = [...dsm.querySelectorAll('button')].find(b => b.textContent.includes('Retry'))
    expect(retry).toBeTruthy()

    metaResponse.dsmRetry = { ...ORTHO, dataset: 'dsm', band_count: 1, dtype: 'float32', nodata: -9999,
      color_interpretation: ['gray'], error: null }
    retry.click()
    await flush()
    const url = calls.find(u => u.includes('dataset=dsm'))
    expect(url).toContain('refresh=1')
    const dsmAfter = el.querySelector('[data-raster="dsm"]')
    expect(dsmAfter.textContent).toContain('1 float32 (Gray)')
    expect(dsmAfter.textContent).toContain('-9999')
    expect(dsmAfter.textContent).not.toContain('Unavailable')
  })

  it('does not fetch or render metadata for an unfinished task', async () => {
    task = { ...task, status: 'Running' }
    const el = mount()
    await flush()
    expect(calls.some(u => u.includes('get_raster_metadata'))).toBe(false)
    expect(el.querySelector('details')).toBeNull()
  })

  it('a metadata endpoint failure leaves the console usable', async () => {
    global.fetch = vi.fn(url => {
      calls.push(url)
      if (url.includes('get_raster_metadata')) return Promise.resolve({ ok: false, json: () => Promise.resolve({ exception: 'boom' }) })
      return Promise.resolve({ ok: true, json: () => Promise.resolve(respond(url)) })
    })
    const el = mount()
    await flush()
    expect(el.querySelector('details')).toBeNull()
    expect(el.textContent).toContain('Survey')
  })
})
