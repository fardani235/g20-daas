// Browser checks for the flight planner against a running stack. Not part of
// `npm test` (needs a real browser and a backend); see e2e/README.md.
//
// Drives the whole flow a pilot goes through: find the site, draw an area,
// read the estimates, change aircraft and parameters, follow terrain, hit a
// capability limit, save, export (blocked, acknowledged, boundary), plan a
// corridor, edit the shape with the mouse, reuse a plan, and add custom
// equipment. Every plan and profile it creates is deleted at the end.
//
//   E2E_BASE_URL        site origin (default http://127.0.0.1:8080)
//   E2E_USER            a user who belongs to an organization
//   E2E_PASSWORD        that user's password (or E2E_PASSWORD_FILE)
//   E2E_PROJECT         name of an existing project of that organization
//   E2E_OFFLINE         "1" to skip the checks that need the address and elevation services
//   E2E_SITE            "lat,lng" of a site with relief for the terrain checks (default: hills near Bandung)
//   E2E_CHROME          optional path to a Chrome/Chromium binary
//   E2E_SHOTS           optional directory for screenshots
//   E2E_PLAYWRIGHT_DIR  directory whose node_modules holds playwright (default: this project)
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(process.env.E2E_PLAYWRIGHT_DIR
  ? path.join(path.resolve(process.env.E2E_PLAYWRIGHT_DIR), 'package.json')
  : import.meta.url)
const { chromium } = require('playwright')

const env = (k, d) => process.env[k] ?? d
const ORIGIN = env('E2E_BASE_URL', 'http://127.0.0.1:8080').replace(/\/$/, '')
const APP = `${ORIGIN}/assets/webodm_frontend/frontend`
const USER = env('E2E_USER', '')
const PASSWORD = process.env.E2E_PASSWORD_FILE
  ? fs.readFileSync(process.env.E2E_PASSWORD_FILE, 'utf8').trim()
  : env('E2E_PASSWORD', '')
const PROJECT = env('E2E_PROJECT', '')
const OFFLINE = env('E2E_OFFLINE', '') === '1'
const SITE = env('E2E_SITE', '-6.7900, 107.6180')
const SHOTS = env('E2E_SHOTS', '')
if (!USER || !PASSWORD || !PROJECT) {
  console.error('Set E2E_USER, E2E_PASSWORD (or E2E_PASSWORD_FILE) and E2E_PROJECT')
  process.exit(2)
}
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true })
const STAMP = new Date().toISOString().replace(/[-:T]/g, '').slice(4, 12)
const P = encodeURIComponent(PROJECT)

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok: !!ok })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
}
const shot = (page, name) => (SHOTS ? page.screenshot({ path: path.join(SHOTS, name) }) : Promise.resolve())

const browser = await chromium.launch({
  executablePath: process.env.E2E_CHROME || undefined,
  args: ['--no-sandbox'],
})
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true })
const page = await context.newPage()
const consoleErrors = []
// The site ships no favicon; the browser's own request for one is not the planner's.
page.on('console', m => { if (m.type() === 'error' && !/favicon\.ico/.test(m.location()?.url || '')) consoleErrors.push(m.text()) })
page.on('pageerror', e => consoleErrors.push(`pageerror: ${e.message}`))
page.on('dialog', d => d.accept())
const failedRequests = []
page.on('response', res => { if (res.status() >= 400) failedRequests.push(`${res.status()} ${res.request().method()} ${res.url().replace(ORIGIN, '')}`) })

// Every preview request and answer, so checks can look at what was actually sent.
const previews = []
page.on('response', async res => {
  if (!res.url().includes('webodm_core.api.flightplan.preview')) return
  try {
    const body = JSON.parse(res.request().postData() || '{}')
    const answer = (await res.json()).message
    previews.push({ request: body, answer })
  } catch { /* navigation aborted the request */ }
})
const lastMission = () => [...previews].reverse().find(p => p.answer?.ok)?.answer.mission
const lastRequest = () => previews.at(-1)?.request

const text = sel => page.locator(sel).first().innerText()
const estimate = key => text(`[data-estimate="${key}"] dd`)
const findings = () => page.locator('[data-finding]').evaluateAll(ns => ns.map(n => n.dataset.finding))

// Wait until the planner is idle: no debounce pending, no request in flight.
async function idle(timeout = 60000) {
  await page.waitForTimeout(450)
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-plan-estimates]')
    return el && !/Computing|Loading terrain/.test(el.textContent)
  }, null, { timeout })
  await page.waitForTimeout(150)
}

async function fill(selector, value) {
  const input = page.locator(selector)
  await input.fill(String(value))
  await input.blur()
  await idle()
}

const map = () => page.locator('[data-plan-map]')
async function mapPoint(fx, fy) {
  const box = await map().boundingBox()
  return { x: box.x + box.width * fx, y: box.y + box.height * fy }
}
async function clickMap(fx, fy) {
  const p = await mapPoint(fx, fy)
  await page.mouse.click(p.x, p.y)
  await page.waitForTimeout(120)
}

async function api(method, body) {
  return page.evaluate(async ({ method, body }) => {
    const res = await fetch(`/api/method/webodm_core.api.flightplan.${method}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': window.csrf_token },
      body: JSON.stringify(body || {}),
    })
    return (await res.json()).message
  }, { method, body })
}

async function download(trigger) {
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 30000 }), trigger()])
  const file = path.join(os.tmpdir(), `fp-e2e-${STAMP}-${dl.suggestedFilename()}`)
  await dl.saveAs(file)
  return { name: dl.suggestedFilename(), data: fs.readFileSync(file) }
}

try {
  // ---- 1. sign in ---------------------------------------------------------------
  await page.goto(`${APP}/login`)
  await page.fill('#username', USER)
  await page.fill('#password', PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL(/\/dashboard/, { timeout: 30000 })
  check('signs in', true)

  // ---- 2. navigation -------------------------------------------------------------
  await page.goto(`${APP}/flight-plans`)
  await page.waitForSelector('h1')
  check('Flight Plans is a primary tab', await page.locator('nav[aria-label="Main"] a[aria-current="page"]').innerText().then(t => t.includes('Flight Plans')))
  await page.goto(`${APP}/project/${P}`)
  await page.waitForSelector('[data-plan-flight]')
  await page.click('[data-plan-flight]')
  await page.waitForURL(/\/plans$/)
  check('the project map links to its flight plans', true)
  check('the project tab row marks Flight plans', (await page.locator('nav[aria-label="Project sections"] a[aria-current="page"]').innerText()).trim() === 'Flight plans')
  await page.click('[data-new-plan]')
  await page.waitForURL(/\/plans\/new$/)
  await page.waitForSelector('.leaflet-container')
  const defaulted = await page.waitForFunction(() => document.querySelector('[data-plan-drone]')?.value === 'dji-mavic-3e', null, { timeout: 15000 })
    .then(() => true).catch(() => false)
  check('a new plan opens the planner with a default aircraft', defaulted)
  check('the planner says how to begin', (await text('[data-plan-hint]')).includes('Find your site'))

  // ---- 3. find the site ------------------------------------------------------------
  await page.fill('[data-place-search] input', SITE)
  await page.click('[data-place-search] button[type=submit]')
  await page.waitForTimeout(1500)
  await shot(page, '01-site-found.png')
  if (!OFFLINE) {
    await page.fill('[data-place-search] input', 'Gedung Sate, Bandung')
    await page.click('[data-place-search] button[type=submit]')
    const found = await page.waitForSelector('[data-place-results] li button', { timeout: 20000 }).then(() => true).catch(() => false)
    check('an address search lists places', found, found ? await text('[data-place-results] li') : await text('[data-place-results]').catch(() => 'no results panel'))
    // Back to the terrain site for the rest.
    await page.fill('[data-place-search] input', SITE)
    await page.click('[data-place-search] button[type=submit]')
    await page.waitForTimeout(1500)
  }

  // ---- 4. draw an area ---------------------------------------------------------------
  await page.click('[data-plan-draw]')
  check('drawing explains itself', (await text('[data-plan-draw-hint]')).includes('Click each corner'))
  const corners = [[0.30, 0.30], [0.70, 0.30], [0.70, 0.68], [0.30, 0.68]]
  for (const [x, y] of corners) await clickMap(x, y)
  await clickMap(...corners[0])                       // clicking the first corner closes the area
  await idle()
  const drawn = lastRequest()?.definition
  const [siteLat, siteLng] = SITE.split(',').map(Number)
  const ring = drawn?.geometry?.coordinates?.[0] || []
  check('the drawn outline is sent as a closed polygon at the searched site',
    drawn?.geometry?.type === 'Polygon' && ring.length === 5
    && ring.every(([lng, lat]) => Math.abs(lat - siteLat) < 0.02 && Math.abs(lng - siteLng) < 0.02),
    `${ring.length} points`)
  const m1 = lastMission()
  check('estimates appear for the area', !!m1 && (await estimate('photos')) === m1.stats.photo_count.toLocaleString('en-US'),
    m1 ? `${m1.stats.line_count} lines, ${m1.stats.photo_count} photos, ${(m1.stats.area_m2 / 1e4).toFixed(1)} ha` : '')
  check('they are labelled as estimates', (await text('[data-plan-estimates]')).includes('Planning estimates'))
  check('flight lines are drawn on the map', (await page.locator('.leaflet-overlay-pane path').count()) >= m1.stats.line_count)
  check('the assurance table says airspace and obstacles were not checked',
    (await page.getAttribute('[data-assurance-topic="airspace"]', 'data-status')) === 'not_checked'
    && (await page.getAttribute('[data-assurance-topic="obstacles"]', 'data-status')) === 'not_checked')
  check('an unset takeoff point is pointed out', (await findings()).includes('TAKEOFF_ASSUMED'))
  await shot(page, '02-area-planned.png')

  // ---- 5. terrain --------------------------------------------------------------------
  if (!OFFLINE) {
    const t = m1.terrain
    check('elevation data is loaded and described', !!t && (await text('[data-plan-terrain]')).includes('(estimate)'),
      t ? `${t.source.label}, ground ${t.ground_min_m}–${t.ground_max_m} m, AGL ${t.agl_min_m}–${t.agl_max_m} m` : 'no terrain')
    check('the terrain profile is drawn', await page.locator('[data-terrain-profile] svg').isVisible())
    check('terrain is reported as an estimate', (await page.getAttribute('[data-assurance-topic="terrain"]', 'data-status')) === 'estimated')
    await page.click('[data-plan-altitude-mode] [data-mode="terrain"]')
    await idle()
    const follow = lastMission()
    check('terrain following varies the waypoint heights',
      follow.terrain.following && follow.stats.max_altitude_m - follow.stats.min_altitude_m > 5,
      `${follow.stats.waypoint_count} waypoints, ${follow.stats.min_altitude_m}–${follow.stats.max_altitude_m} m above takeoff, AGL ${follow.terrain.agl_min_m}–${follow.terrain.agl_max_m} m`)
    check('...and keeps the height above ground near the target',
      Math.abs(follow.terrain.agl_min_m - 100) <= follow.terrain.tolerance_m + 1 && Math.abs(follow.terrain.agl_max_m - 100) <= follow.terrain.tolerance_m + 1)
    await shot(page, '03-terrain-following.png')
    // Terrain following stays on: over this relief a constant altitude comes
    // too close to the ground, which the planner (rightly) refuses to export.
  } else {
    await page.selectOption('[data-plan-elevation]', 'none')
    await idle()
    check('without elevation data terrain is reported as not checked', (await page.getAttribute('[data-assurance-topic="terrain"]', 'data-status')) === 'not_checked')
  }

  // ---- 6. takeoff point ----------------------------------------------------------------
  await page.click('[data-plan-takeoff]')
  await clickMap(0.25, 0.75)
  await idle()
  check('a takeoff point is sent with the plan', Array.isArray(lastRequest().definition.takeoff))
  check('...and the assumption is gone', !(await findings()).includes('TAKEOFF_ASSUMED'))

  // ---- 7. height and resolution are linked ------------------------------------------------
  await fill('[data-plan-gsd] input', 2)
  check('entering a resolution derives the height', Math.abs(Number(await page.inputValue('[data-plan-altitude] input')) - 73.2) < 0.2,
    `${await page.inputValue('[data-plan-altitude] input')} m for 2 cm/px`)
  check('...and that is what the server planned', Math.abs(lastMission().camera.gsd_cm - 2) < 0.01)
  await fill('[data-plan-altitude] input', 100)
  check('entering a height derives the resolution', (await page.inputValue('[data-plan-gsd] input')) === '2.73')

  // ---- 8. other aircraft and cameras ---------------------------------------------------------
  const base = lastMission()
  await page.selectOption('[data-plan-drone]', 'dji-matrice-350-rtk')
  await idle()
  const p1 = lastMission()
  check('another aircraft brings its own camera', (await page.inputValue('[data-plan-camera]')) === 'dji-zenmuse-p1-35mm')
  check('...and a different plan for the same area', p1.camera.gsd_cm < base.camera.gsd_cm && p1.stats.line_count > base.stats.line_count,
    `M3E ${base.camera.gsd_cm} cm/px, ${base.stats.line_count} lines → M350 + P1 35 mm ${p1.camera.gsd_cm} cm/px, ${p1.stats.line_count} lines`)
  await page.selectOption('[data-plan-camera]', 'dji-zenmuse-p1-50mm')
  await idle()
  check('a longer lens resolves finer and needs more lines', lastMission().camera.gsd_cm < p1.camera.gsd_cm && lastMission().stats.line_count > p1.stats.line_count)
  await page.selectOption('[data-plan-drone]', 'dji-mini-4-pro')
  await idle()
  const mini = lastMission()
  check('a small aircraft needs more batteries for the same area', mini.stats.batteries >= base.stats.batteries,
    `Mini 4 Pro: ${mini.stats.batteries} batteries, ${Math.round(mini.stats.total_duration_s / 60)} min; M3E: ${base.stats.batteries}, ${Math.round(base.stats.total_duration_s / 60)} min`)
  check('its unofficial mission support is stated', (await text('[data-plan-support]')).includes('unofficial'))
  await page.selectOption('[data-plan-drone]', 'dji-mini-3')
  await idle()
  check('an aircraft without waypoint flight is flagged', (await findings()).includes('NO_WAYPOINT_SUPPORT'))
  await page.selectOption('[data-plan-drone]', 'wingtra-wingtraone-gen2')
  await idle()
  check('a vendor-planned aircraft is labelled', (await findings()).includes('BOUNDARY_ONLY'))
  await page.selectOption('[data-plan-drone]', 'dji-mavic-3e')
  await idle()

  // ---- 9. a capability limit ----------------------------------------------------------------
  await fill('[data-plan-speed] input', 18)
  check('exceeding the aircraft speed limit is an error', (await findings()).includes('SPEED_ABOVE_MAX'))
  check('...and the plan status says so', (await text('[data-plan-status]')).includes('problem'))
  await shot(page, '04-blocked.png')

  // ---- 10. save and export a blocked plan -------------------------------------------------------
  const title = `E2E area ${STAMP}`
  await page.fill('[data-plan-title]', title)
  await page.click('[data-plan-save]')
  await page.waitForURL(/\/plans\/(?!new)[^/]+$/, { timeout: 20000 })
  const planName = decodeURIComponent(page.url().split('/').pop())
  check('saving gives the plan its own address', planName && planName !== 'new', planName)
  await page.click('[data-plan-export]')
  await page.waitForSelector('[data-export-dialog]')
  check('a blocked plan offers no mission file', await page.locator('[data-export-blocked]').isVisible())
  check('...the download is disabled', await page.locator('[data-export-download]').isDisabled())
  check('formats for other ecosystems are shown as unavailable', (await page.getAttribute('[data-format="qgc-plan"]', 'data-available')) === 'false')
  await page.click('[data-format="kml-boundary"] input')
  const kml = await download(() => page.click('[data-export-download]'))
  check('the boundary is still exportable', kml.name.endsWith('.kml') && kml.data.toString().includes('<Polygon>'), kml.name)
  await shot(page, '05-export-blocked.png')
  await page.keyboard.press('Escape')

  // ---- 11. fix it, export a mission file ---------------------------------------------------------
  await fill('[data-plan-speed] input', 8)
  check('fixing the speed clears the error', !(await findings()).includes('SPEED_ABOVE_MAX'))
  const errorsLeft = await page.locator('[data-finding][data-severity="error"]').evaluateAll(ns => ns.map(n => n.dataset.finding))
  check('no blocking finding is left', errorsLeft.length === 0, errorsLeft.join(', '))
  check('the plan shows unsaved changes', (await page.locator('aside').innerText()).includes('Unsaved changes'))
  await page.click('[data-plan-export]')                 // saves first
  await page.waitForSelector('[data-export-dialog]')
  check('the aircraft format is preselected and supported', await page.locator('[data-format="dji-wpml"] input').isChecked()
    && (await text('[data-format="dji-wpml"]')).includes('Supported'))
  const needsAck = await page.locator('[data-export-acknowledge]').isVisible()
  if (needsAck) {
    check('warnings must be acknowledged before download', await page.locator('[data-export-download]').isDisabled())
    await page.click('[data-export-acknowledge] input')
  }
  const kmz = await download(() => page.click('[data-export-download]'))
  const JSZip = require(path.join(path.dirname(new URL(import.meta.url).pathname), '..', 'node_modules', 'jszip'))
  const zip = await JSZip.loadAsync(kmz.data)
  const names = Object.keys(zip.files).sort()
  const waylines = names.includes('wpmz/waylines.wpml') ? await zip.file('wpmz/waylines.wpml').async('string') : ''
  check('the mission file is a DJI WPML archive', names.join() === 'wpmz/template.kml,wpmz/waylines.wpml', `${kmz.name}: ${names.join(', ')}`)
  check('...for this aircraft, with one placemark per waypoint',
    waylines.includes('<wpml:droneEnumValue>77</wpml:droneEnumValue>')
    && (waylines.match(/<Placemark>/g) || []).length === lastMission().stats.waypoint_count,
    `${(waylines.match(/<Placemark>/g) || []).length} waypoints`)
  await shot(page, '06-export-ready.png')
  await page.keyboard.press('Escape')

  // ---- 12. reopen -------------------------------------------------------------------------------
  const before = previews.length
  await page.reload()
  await page.waitForSelector('[data-estimate="photos"]')
  await page.waitForTimeout(800)
  check('a saved plan reopens as saved, without recomputing', previews.length === before && (await page.locator('aside').innerText()).includes('Saved'))
  check('...with its title and shape', (await page.inputValue('[data-plan-title]')) === title && (await page.locator('.shape-handle-vertex').count()) === 4)

  // ---- 13. edit the shape with the mouse -----------------------------------------------------------
  const handle = page.locator('.shape-handle-vertex').first()
  const hb = await handle.boundingBox()
  await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2)
  await page.mouse.down()
  await page.mouse.move(hb.x - 60, hb.y - 40, { steps: 8 })
  await page.mouse.up()
  await idle()
  const moved = lastRequest().definition.geometry.coordinates[0]
  check('dragging a corner recomputes once, when the drag ends', previews.length - before <= 2 && JSON.stringify(moved) !== JSON.stringify(ring), `${previews.length - before} request(s)`)
  const mid = page.locator('.shape-handle-mid').first()
  const mb = await mid.boundingBox()
  await page.mouse.move(mb.x + mb.width / 2, mb.y + mb.height / 2)
  await page.mouse.down()
  await page.mouse.move(mb.x + 10, mb.y - 50, { steps: 6 })
  await page.mouse.up()
  await idle()
  check('dragging a midpoint adds a corner', (await page.locator('.shape-handle-vertex').count()) === 5)
  await page.locator('.shape-handle-vertex').nth(1).click({ button: 'right' })
  await idle()
  check('right-clicking a corner removes it', (await page.locator('.shape-handle-vertex').count()) === 4)
  await page.click('[data-plan-save]')
  await page.waitForFunction(() => document.querySelector('aside').innerText.includes('Saved'), null, { timeout: 15000 })

  // ---- 14. corridor -----------------------------------------------------------------------------
  await page.goto(`${APP}/project/${P}/plans/new`)
  await page.waitForSelector('.leaflet-container')
  await page.fill('[data-place-search] input', SITE)
  await page.click('[data-place-search] button[type=submit]')
  await page.waitForTimeout(1500)
  await page.click('[data-plan-type] [data-type="corridor"]')
  await page.click('[data-plan-draw]')
  await clickMap(0.25, 0.60)
  await clickMap(0.50, 0.45)
  await clickMap(0.75, 0.55)
  await page.click('[data-plan-finish]')
  await idle()
  const c1 = lastMission()
  check('a corridor is planned along the drawn line', lastRequest().definition.geometry.type === 'LineString' && c1.type === 'corridor',
    `${c1.layout.passes} passes for ${lastRequest().definition.corridor_width_m} m, ${Math.round(c1.stats.survey_distance_m)} m`)
  await fill('#fp-width', 400)
  const c2 = lastMission()
  check('a wider corridor needs more passes', c2.layout.passes > c1.layout.passes, `${c1.layout.passes} → ${c2.layout.passes}`)
  await fill('#fp-width', 40)
  check('a single pass is pointed out', (await findings()).includes('CORRIDOR_SINGLE_PASS'))
  await fill('#fp-passes', 2)
  check('...and the minimum passes setting fixes it', lastMission().layout.passes === 2 && !(await findings()).includes('CORRIDOR_SINGLE_PASS'))
  const corridorTitle = `E2E corridor ${STAMP}`
  await page.fill('[data-plan-title]', corridorTitle)
  await page.click('[data-plan-save]')
  await page.waitForURL(/\/plans\/(?!new)[^/]+$/, { timeout: 20000 })
  await shot(page, '07-corridor.png')

  // ---- 15. the list: reuse and delete ----------------------------------------------------------------
  await page.goto(`${APP}/project/${P}/plans`)
  await page.waitForSelector('[data-plan-row]')
  const rowTexts = await page.locator('[data-plan-row]').allInnerTexts()
  check('both plans are listed with their estimates', rowTexts.some(t => t.includes(title)) && rowTexts.some(t => t.includes(corridorTitle) && t.includes('Corridor')))
  const row = page.locator('[data-plan-row]', { hasText: title })
  await row.locator('button[title^="Duplicate"]').click()
  await page.fill('#dup-title', `${title} reuse`)
  await page.locator('button', { hasText: /^\s*Duplicate\s*$/ }).click()
  await page.waitForSelector(`[data-plan-row]:has-text("${title} reuse")`, { timeout: 15000 })
  check('a plan can be duplicated for reuse', true)
  await shot(page, '08-plans-list.png')

  // ---- 16. custom equipment ---------------------------------------------------------------------------
  await page.goto(`${APP}/flight-plans/equipment`)
  await page.waitForSelector('[data-drone-row]')
  check('the catalog lists aircraft from several manufacturers', (await page.locator('[data-drone-row]').count()) >= 20)
  await page.click('[data-tab="cameras"]')
  await page.click('[data-add-camera]')
  const camName = `E2E cam ${STAMP}`
  await page.fill('#cam-name', camName)
  for (const [id, v] of [['cam-sw', 13.2], ['cam-sh', 8.8], ['cam-iw', 5472], ['cam-ih', 3648], ['cam-f', 8.8]]) await page.fill(`#${id}`, String(v))
  check('the camera form shows what the numbers mean', (await text('[data-camera-check]')).includes('2.74 cm/px'))
  await page.locator('button', { hasText: 'Save camera' }).click()
  await page.waitForSelector(`[data-camera-row]:has-text("${camName}")`, { timeout: 15000 })
  await page.click('[data-tab="drones"]')
  await page.click('[data-add-drone]')
  const droneModel = `E2E hexa ${STAMP}`
  await page.fill('#drone-model', droneModel)
  await page.fill('#drone-time', '30')
  await page.fill('#drone-vmax', '14')
  await page.fill('#drone-vcruise', '6')
  await page.locator('[data-drone-cameras] label', { hasText: camName }).click()
  await page.selectOption('#drone-ecosystem', 'mavlink')
  await page.locator('button', { hasText: 'Save drone' }).click()
  await page.waitForSelector(`[data-drone-row]:has-text("${droneModel}")`, { timeout: 15000 })
  check('a custom drone and camera can be added', (await page.locator(`[data-drone-row]:has-text("${droneModel}")`).innerText()).includes('Yours'))
  await shot(page, '09-equipment.png')

  await page.goto(`${APP}/project/${P}/plans/${encodeURIComponent(planName)}`)
  await page.waitForSelector('[data-estimate="photos"]')
  const droneId = await page.locator('[data-plan-drone] option', { hasText: droneModel }).getAttribute('value')
  await page.selectOption('[data-plan-drone]', droneId)
  await idle()
  const custom = lastMission()
  // 30 min rated x 0.8 endurance factor x 75 % (25 % reserve) = 18 min usable.
  check('the custom drone plans with its own figures', custom.equipment.drone.source === 'custom'
    && Math.abs(custom.stats.usable_flight_time_s - 1080) < 1 && Math.abs(custom.camera.gsd_cm - 2.74) < 0.01,
    `${custom.camera.gsd_cm} cm/px, ${Math.round(custom.stats.usable_flight_time_s / 60)} min usable per battery, ${custom.stats.batteries} batteries`)
  await page.click('[data-plan-export]')
  await page.waitForSelector('[data-export-dialog]')
  check('its exports follow its flight app family',
    (await page.getAttribute('[data-format="qgc-plan"]', 'data-available')) === 'true'
    && (await page.getAttribute('[data-format="dji-wpml"]', 'data-available')) === 'false')
  if (await page.locator('[data-export-acknowledge]').isVisible()) await page.click('[data-export-acknowledge] input')
  if (await page.locator('[data-export-download]').isEnabled()) {
    const plan = await download(() => page.click('[data-export-download]'))
    let parsed = null
    try { parsed = JSON.parse(plan.data.toString()) } catch { /* a zip when several flights are needed */ }
    check('a QGroundControl plan downloads for it', plan.name.endsWith('.plan') ? parsed?.fileType === 'Plan' : plan.name.endsWith('.zip'), plan.name)
  } else {
    check('a QGroundControl plan downloads for it', false, 'download disabled: ' + (await text('[data-export-detail]')).slice(0, 200))
  }
  await page.keyboard.press('Escape')

  // ---- 17. small screen ---------------------------------------------------------------------------------
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(500)
  const aside = await page.locator('aside').boundingBox()
  const mapBox = await map().boundingBox()
  check('on a phone the form sits above a usable map', aside.width <= 390 && mapBox.width >= 380 && mapBox.height > 300 && mapBox.y >= aside.y + aside.height - 1,
    `form ${Math.round(aside.width)}×${Math.round(aside.height)}, map ${Math.round(mapBox.width)}×${Math.round(mapBox.height)}`)
  check('...with no horizontal scrolling', await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
  await shot(page, '10-phone.png')
  await page.setViewportSize({ width: 1440, height: 900 })

  check('no failed requests', failedRequests.length === 0, failedRequests.slice(0, 4).join(' | '))
  check('no errors in the browser console', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))
} catch (e) {
  check('run completed', false, e.stack || String(e))
  await shot(page, '99-failure.png').catch(() => {})
} finally {
  // ---- clean up everything this run created ----------------------------------------------------------------
  try {
    const plans = (await api('list_plans', {})) || []
    for (const p of plans.filter(p => p.title.includes(STAMP))) await api('delete_plan', { name: p.name })
    const catalog = await api('catalog', {})
    for (const d of catalog.drones.filter(d => d.editable && d.label.includes(STAMP))) await api('delete_drone', { docname: d.docname })
    for (const c of catalog.cameras.filter(c => c.editable && c.label.includes(STAMP))) await api('delete_camera', { docname: c.docname })
  } catch (e) {
    console.log('clean-up failed:', e.message)
  }
  await browser.close()
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
