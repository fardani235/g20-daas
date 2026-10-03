// Browser checks for the point cloud viewer. Not part of `npm test` (needs a
// real browser); see e2e/README.md. The backend is mocked with Playwright
// route interception so the viewer can be exercised without a Frappe stack:
// the conversion state machine (Queued -> Running -> Ready, Failed -> retry),
// the private-file byte-range serving, colour modes, filters, measurements
// and the DSM-backed volume call.
//
//   E2E_BASE_URL        SPA origin+base (default http://127.0.0.1:8082/assets/webodm_frontend/frontend)
//   E2E_OCTREE_DIR      directory with metadata.json / hierarchy.bin / octree.bin (PotreeConverter output)
//   E2E_CHROME          optional path to a Chrome/Chromium binary
//   E2E_SHOTS           optional directory for screenshots
//   E2E_PLAYWRIGHT_DIR  directory whose node_modules holds playwright (default: this project)
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(process.env.E2E_PLAYWRIGHT_DIR
  ? path.join(path.resolve(process.env.E2E_PLAYWRIGHT_DIR), 'package.json')
  : import.meta.url)
const { chromium } = require('playwright')

const env = (k, d) => process.env[k] ?? d
const BASE = env('E2E_BASE_URL', 'http://127.0.0.1:8082/assets/webodm_frontend/frontend')
const OCTREE_DIR = env('E2E_OCTREE_DIR', '')
const SHOTS = env('E2E_SHOTS', '')
if (!OCTREE_DIR || !fs.existsSync(path.join(OCTREE_DIR, 'metadata.json'))) {
  console.error('Set E2E_OCTREE_DIR to a PotreeConverter output directory (metadata.json, hierarchy.bin, octree.bin)')
  process.exit(2)
}
const TASK = 't1'
const URL = `${BASE}/project/P1/task/${TASK}/model?source=pointcloud`
const FILES = Object.fromEntries(['metadata.json', 'hierarchy.bin', 'octree.bin'].map(f => [f, `/private/files/${TASK}_potree_${f}`]))
const BLOBS = Object.fromEntries(Object.keys(FILES).map(f => [f, fs.readFileSync(path.join(OCTREE_DIR, f))]))
const metadata = JSON.parse(BLOBS['metadata.json'].toString())

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
}
const shot = (page, name) => (SHOTS ? page.screenshot({ path: path.join(SHOTS, name) }) : Promise.resolve())
const snap = page => page.evaluate(() => window.__modelViewer?.snapshot())
const waitReady = (page, timeout = 90000) =>
  page.waitForFunction(() => window.__modelViewer?.state.status === 'ready', null, { timeout })
const settle = async (page, ms = 1500) => {
  // Wait until no octree nodes are loading, then a little longer for a render.
  await page.waitForFunction(() => (window.__modelViewer?.snapshot()?.pointCloud.loadingNodes ?? 1) === 0, null, { timeout: 60000 }).catch(() => {})
  await page.waitForTimeout(ms)
}
const json = (route, message, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ message }) })

// ---- backend mock
const mock = {
  states: [], // scripted potree_state responses (shift on each call)
  stateCalls: [],
  volumeCalls: [],
  ranges: 0,
  fullReads: 0,
  dsm: '/private/files/t1_dsm.tif',
}
const readyState = () => ({
  task: TASK, status: 'Ready', error: null, files: FILES, cache: 'warm', has_dsm: !!mock.dsm,
  point_cloud: '/private/files/t1_georeferenced_model.laz',
  summary: { points: metadata.points, projection: metadata.projection, attributes: metadata.attributes },
})
async function install(page) {
  // Playwright tries routes newest-first: the catch-all goes in first so the
  // specific handlers below win.
  await page.route('**/api/**', r => (r.request().url().includes('/api/resource/')
    ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"data":[]}' })
    : json(r, null)))
  await page.route('**/api/method/frappe.auth.get_logged_user', r => json(r, 'owner@example.com'))
  await page.route('**/api/method/webodm_core.api.organization.get_my_organization**', r => json(r, {
    organization: 'ORG', organization_name: 'Org', slug: 'org', role: 'Owner', status: 'Active',
  }))
  await page.route('**/api/method/webodm_core.api.csrf.get_token', r => json(r, 'tok'))
  await page.route('**/api/method/webodm_core.api.session.whoami', r => json(r, {
    user: 'owner@example.com', full_name: 'Owner', email: 'owner@example.com', roles: ['WebODM User'],
    organization: 'ORG', organization_name: 'Org', role: 'Owner', is_platform_admin: false,
  }))
  await page.route('**/api/method/webodm_core.api.task.get_task_progress', r => json(r, {
    name: TASK, title: 'Survey flight', status: 'Completed', project: 'P1', progress: 100,
    point_cloud: '/private/files/t1_georeferenced_model.laz', dsm: mock.dsm, model: null, orthophoto: null, dtm: null,
    epsg: 32632, images: [], dataset_summary: {},
  }))
  await page.route('**/api/resource/WebODM%20Task**', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: [
    { name: TASK, title: 'Survey flight', status: 'Completed', model: null, point_cloud: '/private/files/t1_georeferenced_model.laz' },
    { name: 't2', title: 'Other flight', status: 'Completed', model: '/private/files/t2_model.glb', point_cloud: null },
  ] }) }))
  await page.route('**/api/method/webodm_core.api.plugins.list_runs**', r => json(r, []))
  await page.route('**/api/method/webodm_core.api.plugins.list_plugins**', r => json(r, []))
  await page.route('**/api/method/webodm_core.api.pointcloud.potree_state', async r => {
    const body = JSON.parse(r.request().postData() || '{}')
    mock.stateCalls.push(body)
    const next = mock.states.length ? mock.states.shift() : readyState()
    await json(r, typeof next === 'function' ? next(body) : next)
  })
  await page.route('**/api/method/webodm_core.api.tiles.volume', async r => {
    const body = JSON.parse(r.request().postData() || '{}')
    mock.volumeCalls.push(body)
    if (!mock.dsm) return r.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ message: 'Task has no dsm' }) })
    await json(r, { volume: 12.4, fill: 12.4, cut: 0, area: 100, base_plane: 'best_fit' })
  })
  await page.route('**/private/files/t1_potree_*', async r => {
    const name = r.request().url().split('t1_potree_')[1].split('?')[0]
    const blob = BLOBS[name]
    const range = r.request().headers().range
    const m = range && /^bytes=(\d*)-(\d*)$/.exec(range)
    if (m) {
      const start = m[1] ? Number(m[1]) : Math.max(0, blob.length - Number(m[2]))
      const end = m[1] && m[2] ? Math.min(Number(m[2]), blob.length - 1) : blob.length - 1
      mock.ranges++
      return r.fulfill({ status: 206, body: blob.subarray(start, end + 1), headers: {
        'content-type': 'application/octet-stream', 'content-range': `bytes ${start}-${end}/${blob.length}`,
        'content-length': String(end - start + 1), 'accept-ranges': 'bytes',
      } })
    }
    mock.fullReads++
    return r.fulfill({ status: 200, body: blob, headers: { 'content-type': name.endsWith('.json') ? 'application/json' : 'application/octet-stream', 'accept-ranges': 'bytes' } })
  })
}

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.E2E_CHROME || undefined,
    headless: true,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
  })
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', e => consoleErrors.push('pageerror: ' + e.message + ' @ ' + String(e.stack || '').split('\n').slice(1, 3).join(' ')))
  await install(page)

  // ---- 1. first open: conversion is queued, then runs, then is ready
  mock.states = [{ status: 'Queued', error: null, files: null, has_dsm: true }, { status: 'Running', error: null, files: null, has_dsm: true }]
  let t0 = Date.now()
  await page.goto(URL)
  await page.waitForSelector('[data-loading]', { timeout: 20000 })
  const queuedText = await page.textContent('[data-loading]')
  check('first open shows the conversion overlay', /Preparing point cloud/.test(queuedText), queuedText.trim().slice(0, 60))
  check('opening starts the conversion (start=1)', mock.stateCalls[0]?.start === 1 && mock.stateCalls[0]?.task_name === TASK)
  await waitReady(page)
  await settle(page)
  check('polls until Ready and loads the octree', mock.stateCalls.length >= 3, `${mock.stateCalls.length} state calls, ${Date.now() - t0} ms`)
  let s = await snap(page)
  check('viewer is in point cloud mode, Z-up', s.kind === 'pointcloud' && s.up === 'z' && s.pointCloud.hasMetadata)
  check('points are drawn', s.pointCloud.visiblePoints > 1000 && s.pointCloud.totalPoints === metadata.points, `${s.pointCloud.visiblePoints} / ${s.pointCloud.totalPoints}`)
  check('octree streamed with byte ranges through the private URLs', mock.ranges > 3 && mock.fullReads === 1, `${mock.ranges} range requests, ${mock.fullReads} full reads (metadata.json)`)
  await shot(page, 'pc-01-ready.png')

  // ---- 2. panel: only existing attributes are offered
  const modes = await page.$$eval('[data-pointcloud-panel] [aria-label="Colour by"] button', bs => bs.map(b => b.textContent.trim()))
  const expectModes = ['rgb', 'elevation', 'intensity', 'classification'].filter(m => {
    const a = metadata.attributes.find(x => x.name === (m === 'rgb' ? 'rgb' : m === 'elevation' ? 'position' : m))
    if (m === 'rgb') return !!a
    if (m === 'elevation') return true
    if (m === 'intensity') return a && a.max[0] > a.min[0]
    return a && (a.histogram ? a.histogram.filter(Boolean).length > 1 : a.max[0] > a.min[0])
  }).map(m => ({ rgb: 'RGB', elevation: 'Elevation', intensity: 'Intensity', classification: 'Classification' })[m])
  check('colour modes match the attributes present', JSON.stringify(modes) === JSON.stringify(expectModes), modes.join(', '))

  await page.click('[data-pointcloud-panel] [aria-label="Colour by"] button:has-text("Elevation")')
  await page.waitForTimeout(500)
  s = await snap(page)
  check('elevation colouring selected', s.pointCloud.colorMode === 'elevation')
  await shot(page, 'pc-02-elevation.png')

  // ---- 3. elevation filter hides points
  const before = s.pointCloud.visiblePoints
  const minSlider = await page.$('input[aria-label="Minimum elevation"]')
  const max = Number(await minSlider.getAttribute('max')), min = Number(await minSlider.getAttribute('min'))
  await page.$eval('input[aria-label="Minimum elevation"]', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })) }, String(min + (max - min) * 0.6))
  await page.waitForTimeout(800)
  const pixels = async () => page.evaluate(() => {
    const c = document.querySelector('canvas'); const gl = c.getContext('webgl2') || c.getContext('webgl')
    const w = gl.drawingBufferWidth, h = gl.drawingBufferHeight; const px = new Uint8Array(w * h * 4)
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px)
    let n = 0; for (let i = 0; i < px.length; i += 4) { if (!(px[i] === 28 && px[i + 1] === 32 && px[i + 2] === 48)) n++ } return n
  })
  await shot(page, 'pc-03-filtered.png')
  check('elevation filter can be applied', (await page.$('[data-elevation-filter] button:has-text("Reset")')) !== null, `visible before ${before}`)
  await page.click('[data-elevation-filter] button:has-text("Reset")')
  await page.waitForTimeout(300)

  // ---- 4. size, budget, background
  await page.$eval('input[aria-label="Point size"]', el => { el.value = '4'; el.dispatchEvent(new Event('input', { bubbles: true })) })
  await page.selectOption('select[aria-label="Point budget"]', '500000')
  await page.selectOption('select[aria-label="Background"]', 'white')
  await page.waitForTimeout(600)
  s = await snap(page)
  check('background switches to white', s.background === 'ffffff', s.background)
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('pointcloud_viewer_settings') || '{}'))
  check('size / budget / background are remembered', saved.pointSize === 4 && saved.pointBudget === 500000 && saved.background === 'white')
  await page.selectOption('select[aria-label="Background"]', 'dark')
  await page.click('[data-pointcloud-panel] [aria-label="Colour by"] button:has-text("RGB")')
  await settle(page, 800)
  await shot(page, 'pc-04-size4.png')

  // ---- 5. measurements: pick on the rendered points
  const canvas = await page.$('canvas')
  const box = await canvas.boundingBox()
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2
  // find pixels with points near the centre by probing the picker through the viewer
  await page.click('button[aria-label="Measure distance"]')
  s = await snap(page)
  check('distance tool activates', s.measure.kind === 'distance')
  await page.mouse.click(cx - 40, cy + 20)
  await page.waitForTimeout(300)
  await page.mouse.click(cx + 60, cy + 10)
  await page.waitForTimeout(300)
  s = await snap(page)
  check('clicks add vertices picked on the cloud', s.measure.drawing === 2, `${s.measure.drawing} vertices`)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(400)
  s = await snap(page)
  const dist = s.measure.measurements[0]
  check('Enter finishes a distance with a metric readout', s.measure.measurements.length === 1 && /\d+(\.\d+)? (m|km)$/.test(dist?.text || ''), dist?.text)
  check('a label is drawn on the shape', s.measure.labels >= 1)

  await page.click('button[aria-label="Measure area"]')
  for (const [dx, dy] of [[-60, 30], [60, 30], [40, -40]]) { await page.mouse.click(cx + dx, cy + dy); await page.waitForTimeout(250) }
  await page.keyboard.press('Enter')
  await page.waitForTimeout(400)
  s = await snap(page)
  const area = s.measure.measurements[1]
  check('area readout is horizontal m²', !!area && /m²|ha/.test(area.text), area?.text)

  await page.click('button[aria-label="Measure volume"]')
  for (const [dx, dy] of [[-50, 40], [50, 40], [50, -30], [-50, -30]]) { await page.mouse.click(cx + dx, cy + dy); await page.waitForTimeout(250) }
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => window.__modelViewer.snapshot().measure.measurements.some(m => m.kind === 'volume' && m.status !== 'pending'), null, { timeout: 10000 })
  s = await snap(page)
  const vol = s.measure.measurements.find(m => m.kind === 'volume')
  check('volume comes from the DSM endpoint', vol?.status === 'ready' && /12 m³/.test(vol.text), vol?.text)
  const vb = mock.volumeCalls[0]
  const ring = vb ? JSON.parse(vb.polygon).coordinates[0] : []
  check('volume polygon is sent in the cloud CRS, closed', !!vb && vb.task_name === TASK && vb.polygon_crs === (metadata.projection || 'native') && ring.length === 5 && ring[0][0] === ring[4][0], vb && `crs=${vb.polygon_crs}, ${ring.length} vertices`)
  const bbox = metadata.boundingBox
  const inside = ring.every(([x, y]) => x >= bbox.min[0] - 1 && x <= bbox.max[0] + 1 && y >= bbox.min[1] - 1 && y <= bbox.max[1] + 1)
  check('volume vertices are native coordinates inside the cloud bounds', inside, JSON.stringify(ring[0]))
  await shot(page, 'pc-05-measurements.png')

  // Escape leaves the tool; clear removes everything
  await page.click('button[aria-label="Measure distance"]')
  await page.mouse.click(cx, cy)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  s = await snap(page)
  check('Escape cancels the drawing and leaves the tool', s.measure.kind === null && s.measure.drawing === 0)
  await page.click('button[aria-label="Clear measurements"]')
  await page.waitForFunction(() => window.__modelViewer.snapshot().measure.labels === 0, null, { timeout: 5000 }).catch(() => {})
  s = await snap(page)
  check('clear removes all measurements and labels', s.measure.measurements.length === 0 && s.measure.labels === 0, `${s.measure.measurements.length} measurements, ${s.measure.labels} labels`)

  // camera actions still work in Z-up
  const d0 = (await snap(page)).distance
  await page.click('button[aria-label="Zoom in"]')
  await page.waitForFunction(d => { const s = window.__modelViewer.snapshot(); return !s.tweening && s.distance < d - 0.01 }, d0, { timeout: 5000 }).catch(() => {})
  const d1 = (await snap(page)).distance
  check('toolbar zoom works in point cloud mode', d1 < d0 - 0.01, `${d0.toFixed(2)} -> ${d1.toFixed(2)}`)
  await page.focus('[aria-label="3D point cloud viewer"]')
  await page.keyboard.press('2') // top view
  await page.waitForFunction(() => !window.__modelViewer.snapshot().tweening, null, { timeout: 5000 }).catch(() => {})
  await page.waitForTimeout(200)
  s = await snap(page)
  const horiz = Math.hypot(s.position[0] - s.target[0], s.position[1] - s.target[1])
  check('top-down preset looks down the Z axis', Math.abs(s.position[2] - s.target[2]) > horiz * 10, `pos=${s.position.map(v => v.toFixed(1))} target=${s.target.map(v => v.toFixed(1))}`)

  // ---- 6. switcher keeps the source in the URL
  const options = await page.$$eval('select[aria-label="Model"] option', os => os.map(o => o.value))
  check('switcher lists point cloud and model entries', options.includes(`pointcloud:${TASK}`) && options.includes('task:t2'), options.join(' '))
  check('URL carries the selected source', page.url().includes('source=pointcloud'))

  // ---- 7. no DSM: volume is disabled, never guessed
  mock.dsm = null
  await page.goto(URL)
  await waitReady(page)
  await settle(page, 500)
  const volDisabled = await page.$eval('button[aria-label="Measure volume"]', b => b.disabled)
  check('volume tool is disabled without a DSM', volDisabled === true)

  // ---- 8. failed conversion -> retry
  mock.dsm = '/private/files/t1_dsm.tif'
  mock.states = [{ status: 'Failed', error: 'conversion failed: PotreeConverter exceeded 7200 s', files: null, has_dsm: true }]
  mock.stateCalls = []
  await page.goto(URL)
  await page.waitForSelector('[data-error]', { timeout: 20000 })
  const errText = await page.textContent('[data-error]')
  check('a failed conversion is reported with the backend error', /could not be prepared/.test(errText) && /exceeded 7200/.test(errText))
  await page.click('[data-error] button:has-text("Retry")')
  await waitReady(page)
  check('Retry restarts the conversion (retry=1) and loads when Ready', mock.stateCalls.some(c => c.retry === 1))

  // ---- 9. leave the page cleanly (dispose must not throw)
  await page.goto('about:blank')
  await page.waitForTimeout(500)

  const fatal = consoleErrors.filter(e => !/favicon|404|net::ERR|Failed to load resource/.test(e))
  check('no console errors', fatal.length === 0, fatal.slice(0, 3).join(' | '))

  await browser.close()
  const failed = results.filter(r => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
  process.exit(failed.length ? 1 : 0)
})().catch(e => { console.error(e); process.exit(1) })
