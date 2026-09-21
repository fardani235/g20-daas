// Browser checks for the 3D viewer. Not part of `npm test` (needs a running
// stack and a real browser); see e2e/README.md.
//
//   E2E_BASE_URL   SPA origin+base (default http://localhost:8081/assets/webodm_frontend/frontend)
//   E2E_USER / E2E_PASSWORD (or E2E_PASSWORD_FILE)
//   E2E_PROJECT    project name (route param)
//   E2E_TASK       task name (route param) of a Completed task with a model
//   E2E_TASK_TITLE task title as shown on the project map card
//   E2E_CHROME     optional path to a Chrome/Chromium binary
//   E2E_SHOTS      optional directory for screenshots
//   E2E_PLAYWRIGHT_DIR  directory whose node_modules holds playwright (default: this project)
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(process.env.E2E_PLAYWRIGHT_DIR
  ? path.join(path.resolve(process.env.E2E_PLAYWRIGHT_DIR), 'package.json')
  : import.meta.url)
const { chromium } = require('playwright')

const env = (k, d) => process.env[k] ?? d
const BASE = env('E2E_BASE_URL', 'http://localhost:8081/assets/webodm_frontend/frontend')
const USER = env('E2E_USER', 'Administrator')
const PASSWORD = process.env.E2E_PASSWORD ?? (process.env.E2E_PASSWORD_FILE ? fs.readFileSync(process.env.E2E_PASSWORD_FILE, 'utf8').trim() : '')
const PROJECT = env('E2E_PROJECT', '')
const TASK = env('E2E_TASK', '')
const TASK_TITLE = env('E2E_TASK_TITLE', '')
const SHOTS = env('E2E_SHOTS', '')
if (!PASSWORD || !PROJECT || !TASK || !TASK_TITLE) {
  console.error('Set E2E_PASSWORD (or E2E_PASSWORD_FILE), E2E_PROJECT, E2E_TASK and E2E_TASK_TITLE')
  process.exit(2)
}
const MODEL_URL = `${BASE}/project/${encodeURIComponent(PROJECT)}/task/${encodeURIComponent(TASK)}/model`
const shot = (page, name) => (SHOTS ? page.screenshot({ path: path.join(SHOTS, name) }) : Promise.resolve())

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
}
const approx = (a, b, tol) => Math.abs(a - b) <= tol
const snap = page => page.evaluate(() => window.__modelViewer?.snapshot())
const waitReady = (page, timeout = 120000) =>
  page.waitForFunction(() => window.__modelViewer?.state.status === 'ready', null, { timeout })
const settle = page => page.waitForFunction(() => !window.__modelViewer?.snapshot()?.tweening, null, { timeout: 5000 })
// MapView shows a task's action buttons only once its card is selected.
async function openModelFromMap(page) {
  await page.waitForSelector(`text="${TASK_TITLE}"`, { timeout: 20000 })
  for (let i = 0; i < 3 && !(await page.isVisible('button:has-text("3D")')); i++) {
    await page.click(`text="${TASK_TITLE}"`)
    await page.waitForTimeout(500)
  }
  await page.click('button:has-text("3D")', { timeout: 10000 })
}

;(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.E2E_CHROME || undefined,
    headless: true,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-certificate-errors', '--no-sandbox'],
  })
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, ignoreHTTPSErrors: true })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', e => consoleErrors.push('pageerror: ' + e.message))

  // ---- login
  await page.goto(`${BASE}/login`)
  await page.fill('#username', USER)
  await page.fill('#password', PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL(u => !u.toString().includes('/login'), { timeout: 20000 })
  check('login', true)

  // ---- load the real model
  let t0 = Date.now()
  await page.goto(MODEL_URL)
  await page.waitForSelector('text=Downloading model', { timeout: 15000 }).catch(() => {})
  await waitReady(page)
  const loadMs = Date.now() - t0
  let s = await snap(page)
  check('model loads to ready', s.status === 'ready', `${loadMs} ms, ${s.triangles} triangles`)
  check('model is centred at the origin', approx(s.center[0], 0, 1) && approx(s.center[1], 0, 1) && approx(s.center[2], 0, 1), JSON.stringify(s.center))
  check('camera framed at the fit distance', approx(s.distance, s.framingDistance, 0.01 * s.framingDistance), `${s.distance.toFixed(1)} vs ${s.framingDistance.toFixed(1)}`)
  check('camera is above the model (Z-up corrected)', s.position[1] > s.center[1], `y=${s.position[1].toFixed(1)}`)
  const stats = await page.textContent('p[title*="vertices"]')
  check('stats chip shows triangles and size', /tris/.test(stats) && /MB/.test(stats), stats.trim())
  check('mode hint is visible after load', await page.isVisible('text=Drag to rotate'))
  await shot(page, '01-ready.png')

  // ---- toolbar: zoom, reset, views
  const iso = s
  await page.click('button[aria-label="Zoom in"]')
  await settle(page)
  s = await snap(page)
  check('zoom in moves the camera closer', s.distance < iso.distance * 0.8, `${iso.distance.toFixed(1)} -> ${s.distance.toFixed(1)}`)
  await page.click('button[aria-label="Zoom out"]')
  await page.click('button[aria-label="Zoom out"]')
  await settle(page)
  s = await snap(page)
  check('zoom out moves the camera away', s.distance > iso.distance * 1.1, `${s.distance.toFixed(1)}`)
  await page.click('button[aria-label="Reset view"]')
  await settle(page)
  s = await snap(page)
  check('reset restores the default framing', s.position.every((v, i) => approx(v, iso.position[i], 0.01)) && s.target.every((v, i) => approx(v, iso.target[i], 0.01)))

  await page.click('button[aria-label="Preset views"]')
  await page.click('text=Top-down')
  await settle(page)
  s = await snap(page)
  check('top view looks straight down', approx(s.position[0], s.center[0], 0.01 * s.distance) && approx(s.position[2], s.center[2], 0.01 * s.distance) && s.position[1] > s.center[1], JSON.stringify(s.position.map(v => +v.toFixed(2))))
  await shot(page, '02-top.png')

  // ---- modes
  await page.click('button[aria-label="Pan"]')
  s = await snap(page)
  check('pan mode remaps the left button', s.mode === 'pan' && s.leftButton === 2 /* MOUSE.PAN */, `left=${s.leftButton}`)
  check('hint text follows the mode', await page.isVisible('text=Drag to pan'))
  await page.click('button[aria-label="Rotate"]')
  s = await snap(page)
  check('rotate mode restores the left button', s.mode === 'rotate' && s.leftButton === 0)

  // ---- mouse drag rotates and pans
  const canvas = await page.$('canvas')
  const box = await canvas.boundingBox()
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2
  await page.click('button[aria-label="Reset view"]')
  await settle(page)
  const before = await snap(page)
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  await page.mouse.move(cx + 200, cy + 40, { steps: 10 })
  await page.mouse.up()
  await page.waitForTimeout(600)
  s = await snap(page)
  check('left-drag rotates around the target', !approx(s.position[0], before.position[0], 1) && s.target.every((v, i) => approx(v, before.target[i], 0.01)))
  await page.mouse.move(cx, cy)
  await page.mouse.down({ button: 'right' })
  await page.mouse.move(cx + 150, cy, { steps: 8 })
  await page.mouse.up({ button: 'right' })
  await page.waitForTimeout(600)
  const afterPan = await snap(page)
  check('right-drag pans the target', !afterPan.target.every((v, i) => approx(v, s.target[i], 0.01)))
  check('camera never dips below the ground plane', afterPan.position[1] > afterPan.target[1] - 0.5)
  check('hint hides after interaction', !(await page.isVisible('text=Drag to rotate')))

  // ---- wheel zoom
  const wheelBefore = await snap(page)
  await page.mouse.move(cx, cy)
  await page.mouse.wheel(0, -600)
  await page.waitForTimeout(700)
  s = await snap(page)
  check('scroll wheel zooms in', s.distance < wheelBefore.distance)

  // ---- double-click focus
  await page.click('button[aria-label="Reset view"]')
  await settle(page)
  const dcBefore = await snap(page)
  await page.mouse.dblclick(cx + 120, cy + 60)
  await settle(page)
  s = await snap(page)
  check('double-click re-targets the orbit on the model', !s.target.every((v, i) => approx(v, dcBefore.target[i], 0.01)))

  // ---- keyboard
  await page.click('button[aria-label="Reset view"]')
  await settle(page)
  await page.mouse.click(cx, cy) // focus the stage
  const kb = await snap(page)
  await page.keyboard.press('ArrowLeft')
  await page.waitForTimeout(100)
  s = await snap(page)
  check('arrow keys pan', !s.target.every((v, i) => approx(v, kb.target[i], 0.01)))
  await page.keyboard.press('+')
  await settle(page)
  s = await snap(page)
  check('+ zooms in', s.distance < kb.distance)
  await page.keyboard.press('2')
  await settle(page)
  s = await snap(page)
  check('2 switches to the top view', approx(s.position[0], s.center[0], 0.01 * s.distance) && s.position[1] > s.center[1], JSON.stringify(s.position.map(v => +v.toFixed(2))))
  await page.keyboard.press('r')
  await settle(page)
  s = await snap(page)
  check('R resets the view', s.position.every((v, i) => approx(v, kb.position[i], 0.05)), `${JSON.stringify(kb.position.map(v => +v.toFixed(3)))} -> ${JSON.stringify(s.position.map(v => +v.toFixed(3)))} tween=${s.tweening}`)
  await page.keyboard.press('g')
  s = await snap(page)
  check('G hides the grid', s.gridVisible === false)
  await page.keyboard.press('g')
  await page.keyboard.press('?')
  check('? opens the help dialog', await page.isVisible('text=Viewer controls'))
  await page.keyboard.press('Escape')
  await page.waitForTimeout(300)
  check('Escape closes the help dialog', !(await page.isVisible('text=Viewer controls')))

  // ---- dataset switcher hidden with a single model
  check('dataset switcher hidden when only one model exists', (await page.$$('select[aria-label="Dataset"]')).length === 0)

  // ---- error state + retry
  await page.route('**/private/files/*.glb', route => route.fulfill({ status: 500, body: 'boom' }))
  await page.click('button[aria-label="Back to project"]')
  await openModelFromMap(page)
  await page.waitForSelector('text=could not be displayed', { timeout: 30000 })
  check('server error shows the error state', await page.isVisible('text=HTTP 500'))
  await shot(page, '03-error.png')
  await page.unroute('**/private/files/*.glb')
  await page.click('button:has-text("Retry")')
  await waitReady(page)
  check('retry recovers after the error', true)

  // ---- missing task -> empty state
  await page.goto(`${BASE}/project/${encodeURIComponent(PROJECT)}/task/does-not-exist/model`)
  await page.waitForSelector('text=Task not found', { timeout: 20000 })
  check('unknown task shows the not-found state', true)
  await shot(page, '04-missing.png')

  // ---- navigate away and back repeatedly (disposal / context reuse)
  let stable = true
  for (let i = 0; i < 6; i++) {
    await page.goto(MODEL_URL)
    try {
      await waitReady(page, 60000)
    } catch { stable = false; break }
    await page.click('button[aria-label="Back to project"]')
    await openModelFromMap(page)
    try {
      await waitReady(page, 60000)
    } catch { stable = false; break }
    await page.click('button:has-text("Console")')
    await page.waitForSelector('text=Refresh', { timeout: 15000 })
  }
  check('6x load/leave/reload cycles stay stable', stable)

  // ---- abort mid-load by leaving immediately
  await page.goto(MODEL_URL)
  await page.waitForSelector('text=Downloading model', { timeout: 15000 }).catch(() => {})
  await page.click('button[aria-label="Back to project"]')
  await page.waitForSelector(`text="${TASK_TITLE}"`, { timeout: 15000 })
  await page.waitForTimeout(1500)
  check('leaving mid-download does not throw', !consoleErrors.some(e => /pageerror/.test(e)))

  // ---- small screen
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(MODEL_URL)
  await waitReady(page)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  check('no horizontal overflow on a phone-sized viewport', !overflow)
  check('toolbar visible on a phone-sized viewport', await page.isVisible('button[aria-label="Rotate"]'))
  s = await snap(page)
  check('camera re-framed for portrait aspect', approx(s.distance, s.framingDistance, 0.01 * s.framingDistance))
  await shot(page, '05-mobile.png')

  // ---- resize re-fits the canvas
  await page.setViewportSize({ width: 1000, height: 600 })
  await page.waitForTimeout(400)
  const canvasSize = await page.evaluate(() => { const c = document.querySelector('canvas'); const r = c.parentElement.getBoundingClientRect(); return [c.clientWidth, Math.round(r.width), c.clientHeight, Math.round(r.height)] })
  check('canvas tracks its container on resize', canvasSize[0] === canvasSize[1] && canvasSize[2] === canvasSize[3], canvasSize.join('x'))

  // Ignore the auth-guard 403 before login, favicon 404s, three.js noting ODM's
  // CESIUM_RTC extension, and the HTTP 500 this script injects on purpose.
  const realErrors = consoleErrors.filter(e => !/favicon|Failed to load resource.*(403|404|500)|HTTP 500|THREE.GLTFLoader: Unknown extension|Download the Vue Devtools/.test(e))
  check('no console errors during the session', realErrors.length === 0, realErrors.slice(0, 5).join(' | '))

  await browser.close()
  const failed = results.filter(r => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
  process.exit(failed.length ? 1 : 0)
})().catch(e => { console.error('E2E crashed:', e); process.exit(2) })
