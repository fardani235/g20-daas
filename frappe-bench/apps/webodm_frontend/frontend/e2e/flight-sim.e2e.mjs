// Browser checks for the flight simulator against a running stack. Not part
// of `npm test` (needs a real browser, WebGL and a backend); see e2e/README.md.
//
// Opens saved plans from the plan list with "Simulate", and checks what a
// pilot would see: the dock and its wording, playback (play, pause, stop,
// restart, speed, scrubbing, the profile), that the 2D map and the 3D scene
// draw the same moment, every camera, terrain and imagery in 3D, coverage,
// a multi-battery mission, a fixed-wing, a plan without elevation data, an
// edit while simulating, a mission near the planner's size limits, a phone
// viewport - and that simulating changes nothing: the plan and its export
// options are byte-for-byte what they were. Every plan it creates is deleted.
//
//   E2E_BASE_URL        site origin (default http://127.0.0.1:8080)
//   E2E_USER            a user who belongs to an organization
//   E2E_PASSWORD        that user's password (or E2E_PASSWORD_FILE)
//   E2E_PROJECT         name of an existing project of that organization
//   E2E_OFFLINE         "1" to plan without elevation data and not to expect map imagery
//   E2E_SITE            "lat,lng" of a site with relief (default: hills near Bandung)
//   E2E_GPU             "1" to use the machine's GPU instead of software WebGL (realistic frame rates)
//   E2E_DSM_TASK        optional: name of a Completed task of the project with a DSM, to simulate over a survey's
//                       own surface model (needs the geospatial service); E2E_DSM_CENTER="lat,lng" inside it
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
const ORIGIN = env('E2E_BASE_URL', 'http://127.0.0.1:8080').replace(/\/$/, '')
const APP = `${ORIGIN}/assets/webodm_frontend/frontend`
const USER = env('E2E_USER', '')
const PASSWORD = process.env.E2E_PASSWORD_FILE
  ? fs.readFileSync(process.env.E2E_PASSWORD_FILE, 'utf8').trim()
  : env('E2E_PASSWORD', '')
const PROJECT = env('E2E_PROJECT', '')
const OFFLINE = env('E2E_OFFLINE', '') === '1'
const GPU = env('E2E_GPU', '') === '1'
const [SITE_LAT, SITE_LNG] = env('E2E_SITE', '-6.7900, 107.6180').split(',').map(Number)
const DSM_TASK = env('E2E_DSM_TASK', '')
const DSM_CENTER = env('E2E_DSM_CENTER', '').split(',').map(Number)
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
const near = (a, b, tol) => Math.abs(a - b) <= tol

const browser = await chromium.launch({
  executablePath: process.env.E2E_CHROME || undefined,
  args: ['--no-sandbox', '--ignore-gpu-blocklist',
    ...(GPU ? ['--enable-gpu', '--use-angle=gl-egl'] : ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])],
})
const context = await browser.newContext({ viewport: { width: 1600, height: 950 } })
const page = await context.newPage()
const consoleErrors = []
page.on('console', m => {
  if (m.type() !== 'error') return
  const url = m.location()?.url || ''
  // A map tile that fails to load is the tile server's business (the ground is then shown plain), as is the missing favicon.
  if (/favicon\.ico/.test(url) || (/^https?:/.test(url) && !url.startsWith(ORIGIN))) return
  consoleErrors.push(m.text())
})
page.on('pageerror', e => consoleErrors.push(`pageerror: ${e.message}`))
page.on('dialog', d => d.accept())
const failedRequests = []
page.on('response', res => {
  // Only the platform's own requests: a missing map tile is the tile server's business.
  if (res.status() >= 400 && res.url().startsWith(ORIGIN) && !/favicon\.ico/.test(res.url())) {
    failedRequests.push(`${res.status()} ${res.request().method()} ${res.url().replace(ORIGIN, '')}`)
  }
})
const terrainCalls = []
page.on('response', async res => {
  if (!res.url().includes('webodm_core.api.flightplan.simulation_terrain')) return
  try { terrainCalls.push({ request: JSON.parse(res.request().postData() || '{}'), answer: (await res.json()).message }) } catch { /* navigated away */ }
})
const shot = name => (SHOTS ? page.screenshot({ path: path.join(SHOTS, name) }) : Promise.resolve())

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

// A rectangle `w` x `h` metres centred `east`, `north` metres from the site.
function rect(w, h, east = 0, north = 0) {
  const mLat = 110574
  const mLng = 111320 * Math.cos(SITE_LAT * Math.PI / 180)
  const cx = SITE_LNG + east / mLng
  const cy = SITE_LAT + north / mLat
  const dx = w / 2 / mLng
  const dy = h / 2 / mLat
  return { type: 'Polygon', coordinates: [[[cx - dx, cy - dy], [cx + dx, cy - dy], [cx + dx, cy + dy], [cx - dx, cy + dy], [cx - dx, cy - dy]]] }
}
const offset = (east, north) => [SITE_LNG + east / (111320 * Math.cos(SITE_LAT * Math.PI / 180)), SITE_LAT + north / 110574]
const SOURCE = OFFLINE ? 'none' : 'auto'
const created = []
async function plan(title, definition) {
  const saved = await api('save_plan', {
    project: PROJECT, title: `${title} ${STAMP}`,
    definition: { type: 'area', drone: 'dji-mavic-3e', camera: 'dji-mavic-3e-wide', elevation_source: 'none', params: {}, ...definition },
  })
  if (!saved?.name) throw new Error(`could not create the plan "${title}": ${JSON.stringify(saved).slice(0, 300)}`)
  created.push(saved.name)
  return saved
}

const root = () => page.evaluate(() => document.querySelector('[data-flight-sim]').simSnapshot())
const scene = () => page.evaluate(() => document.querySelector('[data-sim-3d-host]')?.simSnapshot?.() || null)
const tile = key => page.locator(`[data-sim-tile="${key}"]`).innerText()
const status = () => page.getAttribute('[data-flight-sim]', 'data-sim-status')
async function seek(t) {
  await page.evaluate(v => {
    const el = document.querySelector('[data-sim-scrub]')
    el.value = String(v)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }, t)
  await page.waitForTimeout(150)
}
async function openSimulation(name, { wait3d = true } = {}) {
  await page.goto(`${APP}/project/${P}/plans/${encodeURIComponent(name)}?simulate=1`)
  await page.waitForSelector('[data-sim-status="ready"]', { timeout: 90000 })
  if (wait3d) await page.waitForSelector('[data-sim-3d="ready"]', { timeout: 60000 })
  await page.waitForTimeout(400)
}
// Animation frames per second over `ms`, as the page gets them.
const fps = (ms = 3000) => page.evaluate(ms => new Promise(resolve => {
  let n = 0
  const t0 = performance.now()
  const step = () => { n++; if (performance.now() - t0 < ms) requestAnimationFrame(step); else resolve(n / (ms / 1000)) }
  requestAnimationFrame(step)
}), ms)

try {
  // ---- 1. sign in, make the plans ------------------------------------------------
  await page.goto(`${APP}/login`)
  await page.fill('#username', USER)
  await page.fill('#password', PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL(/\/dashboard/, { timeout: 30000 })
  await page.waitForFunction(() => !!window.csrf_token, null, { timeout: 15000 })
  check('signs in', true)

  const hills = await plan('E2E sim hills', {
    geometry: rect(700, 480), takeoff: offset(-440, -280), elevation_source: SOURCE,
    params: { altitude_mode: OFFLINE ? 'constant' : 'terrain', altitude_m: 100, vlos_m: 2000 },
  })
  const multi = await plan('E2E sim batteries', { geometry: rect(1500, 1000), takeoff: offset(-800, 0), params: { vlos_m: 3000 } })
  const wing = await plan('E2E sim fixed-wing', {
    geometry: rect(900, 600), takeoff: offset(-520, -340), drone: 'ageagle-ebee-x', camera: 'sensefly-soda-3d',
    params: { altitude_m: 120, vlos_m: 3000 },
  })
  const landing = await plan('E2E sim land', { geometry: rect(300, 200), takeoff: offset(-200, -150), params: { finish_action: 'land' } })
  check('creates the plans to simulate', hills.mission.waypoints.length > 4 && multi.mission.flights.length >= 2,
    `${hills.mission.stats.waypoint_count} waypoints with ${hills.mission.terrain ? 'terrain' : 'no terrain'}; ${multi.mission.flights.length} batteries`)

  const planBefore = await api('get_plan', { name: hills.name })
  const exportBefore = await api('export_options', { name: hills.name })

  // ---- 2. from the plan list into the simulation -------------------------------------
  await page.goto(`${APP}/project/${P}/plans`)
  await page.waitForSelector(`[data-plan-simulate="${hills.name}"]`, { timeout: 30000 })
  await page.click(`[data-plan-simulate="${hills.name}"]`)
  await page.waitForURL(/simulate=1/, { timeout: 15000 })
  await page.waitForSelector('[data-sim-status="ready"]', { timeout: 90000 })
  await page.waitForSelector('[data-sim-3d="ready"]', { timeout: 60000 })
  check('Simulate on the plan list opens the plan with its simulation', true)
  await page.waitForTimeout(OFFLINE ? 500 : 5000)                 // imagery
  await shot('01-open.png')

  const dock = await page.locator('[data-sim-dock]').innerText()
  check('says it is an estimate, not telemetry, and verifies nothing about safety',
    /Simulation only/.test(dock) && /not flight telemetry/.test(dock) && /does not verify that the flight is safe/.test(dock)
      && /Estimate, not telemetry/.test(dock) && !/\b(verified|cleared|safe to fly)\b/i.test(dock))
  let s = await root()
  check('starts at time zero, paused, on the ground at the takeoff point',
    s.playback.time === 0 && !s.playback.playing && s.live.phaseKey === 'preflight' && s.live.alt === 0
      && near(s.live.lat, hills.mission.takeoff.lat, 2e-7) && near(s.live.lng, hills.mission.takeoff.lng, 2e-7))
  check('simulates the plan\'s own photos, flights and distance',
    s.summary.photos === hills.mission.stats.photo_count && s.summary.flights === hills.mission.flights.length
      && near(s.summary.distance, hills.mission.stats.total_distance_m, 2),
    `${s.summary.photos} photos, ${Math.round(s.summary.distance)} m vs ${hills.mission.stats.total_distance_m} m planned`)
  check('simulated flight time is close to the plan\'s estimate',
    Math.abs(s.summary.flightTime - hills.mission.stats.total_duration_s) / hills.mission.stats.total_duration_s < 0.08,
    `${Math.round(s.summary.flightTime)} s vs ${hills.mission.stats.total_duration_s} s`)

  if (!OFFLINE) {
    const call = terrainCalls.at(-1)
    const grid = call?.answer?.terrain
    check('loads the elevation grid of the plan\'s own definition', !!grid && grid.rows * grid.cols <= 400 && grid.source?.id,
      grid ? `${grid.rows} x ${grid.cols} from ${grid.source.label}` : 'no grid')
    // The page sends the stored shape, rounded to 1e-7 degree.
    const sent = call?.request?.definition?.geometry?.coordinates?.[0] || []
    const stored = planBefore.definition.geometry.coordinates[0]
    check('asks with the stored definition', sent.length === stored.length
      && sent.every((p, i) => near(p[0], stored[i][0], 1e-6) && near(p[1], stored[i][1], 1e-6))
      && call.request.definition.elevation_source === planBefore.definition.elevation_source)
  } else {
    check('asks for no elevation data for a plan without any', terrainCalls.length === 0)
  }

  // ---- 3. the 3D scene ----------------------------------------------------------------
  let v = await scene()
  check('3D: draws the terrain, the path and the aircraft', v && v.draw.calls > 5 && v.draw.triangles > 100 && v.path.pieces > 10 && v.aircraft.visible,
    `${v.draw.calls} draw calls, ${v.draw.triangles} triangles, ${v.path.pieces} path pieces`)
  if (!OFFLINE) {
    check('3D: terrain is the elevation grid, with relief', !v.terrain.flat && v.terrain.relief > 5, `${v.terrain.rows} x ${v.terrain.cols}, relief ${v.terrain.relief} m`)
    check('3D: map imagery is draped on it', v.imagery.imagery === 'ready' && v.imagery.loaded > 0, `${v.imagery.loaded}/${v.imagery.tiles} tiles at zoom ${v.imagery.zoom}`)
  } else {
    check('3D: without elevation data the ground is flat and says so', v.terrain.flat && await page.locator('[data-sim-3d-flat]').count() === 1)
  }
  check('3D: nothing flown and no photo before the start', v.path.flown === 0 && v.photos === 0)

  // ---- 4. playback ----------------------------------------------------------------------
  await page.selectOption('[data-sim-speed]', '16')
  await page.click('[data-sim-play]')
  await page.waitForFunction(() => Number(document.querySelector('[data-flight-sim]').dataset.simTime) > 12, null, { timeout: 60000 })
  s = await root()
  check('play advances the clock at the chosen speed', s.playback.playing && s.playback.time > 12 && s.playback.speed === 16, `t = ${s.playback.time.toFixed(1)} s`)
  await page.click('[data-sim-play]')
  const held = (await root()).playback.time
  await page.waitForTimeout(700)
  check('pause holds it', (await root()).playback.time === held && !(await root()).playback.playing)
  await page.click('[data-sim-stop]')
  s = await root()
  check('stop returns to the start', s.playback.time === 0 && !s.playback.playing)
  await seek(200)
  await page.click('[data-sim-restart]')
  await page.waitForTimeout(300)
  s = await root()
  check('restart plays again from the start', s.playback.playing && s.playback.time < 60)
  await page.click('[data-sim-play]')

  // Scrub onto the third survey line.
  const lineMid = await page.evaluate(() => {
    // Find a time in the middle of survey line 3 by bisecting on the dashboard's own state.
    const el = document.querySelector('[data-flight-sim]')
    const scrub = document.querySelector('[data-sim-scrub]')
    const max = Number(scrub.max)
    for (let t = 0; t < max; t += max / 400) {
      scrub.value = String(t)
      scrub.dispatchEvent(new Event('input', { bubbles: true }))
      const st = el.simSnapshot().live
      if (st.phaseKey === 'survey' && st.line === 2 && st.speed > 5) return t
    }
    return null
  })
  check('scrubbing reaches any moment of the flight', lineMid !== null, `survey line 3 at t = ${lineMid?.toFixed(1)} s`)
  await page.waitForTimeout(300)
  check('dashboard: phase, speed and camera state for that moment',
    /Survey line 3 of/.test(await tile('phase')) && /m\/s/.test(await tile('speed')) && /camera on/.test(await tile('photos')),
    (await tile('phase')).replace(/\n/g, ' · '))
  s = await root()
  check('dashboard: height above takeoff and above ground', s.live.alt > 20 && (OFFLINE ? s.live.agl === null : s.live.agl > 20),
    `${Math.round(s.live.alt)} m above takeoff, ${s.live.agl === null ? 'ground unknown' : Math.round(s.live.agl) + ' m above ground'}`)
  if (!OFFLINE && hills.mission.terrain?.following) {
    check('terrain following holds the planned height above ground', near(s.live.agl, 100, (hills.mission.terrain.tolerance_m || 5) + 1), `${s.live.agl.toFixed(1)} m`)
  }
  check('dashboard: battery estimate falls with flight time', s.live.battery < 100 && s.live.battery > 50 && /estimate/.test(await tile('battery')))
  check('dashboard: photos taken and area covered so far', s.live.photos > 0 && s.live.coverage > 0 && s.live.coverage < 1,
    `${s.live.photos} photos, ${Math.round(s.live.coverage * 100)} % covered`)

  // ---- 5. 2D and 3D show the same moment ---------------------------------------------------
  v = await scene()
  check('2D map: the aircraft is where the state says', near(s.map.aircraft.lat, s.live.lat, 1e-9) && near(s.map.aircraft.lng, s.live.lng, 1e-9))
  check('3D scene: the aircraft is where the state says',
    near(v.aircraft.position[0], s.live.x, 0.02) && near(v.aircraft.position[1], s.live.alt, 0.02) && near(v.aircraft.position[2], -s.live.y, 0.02),
    `(${v.aircraft.position.join(', ')})`)
  const turned = Number((/rotate\(([-\d.]+)deg\)/.exec(s.map.aircraft.rotation) || [])[1])
  check('2D map: the aircraft is turned to its heading', near(turned, s.live.heading, 0.06), `${turned}°`)
  check('2D map: camera footprint and coverage are drawn', s.map.footprint && s.map.coverage)
  check('3D scene: flown track and photos match the moment', v.path.flown > 0 && v.photos === s.live.photos && v.coverage, `${v.path.flown} pieces, ${v.photos} photos`)
  // The same during playback: sampled in one go, both views are on the frame just drawn.
  await page.selectOption('[data-sim-speed]', '8')
  await page.click('[data-sim-play]')
  await page.waitForTimeout(1500)
  const pair = await page.evaluate(() => {
    const a = document.querySelector('[data-flight-sim]').simSnapshot()
    const b = document.querySelector('[data-sim-3d-host]').simSnapshot()
    return { live: a.live, map: a.map.aircraft, scene: b.aircraft.position }
  })
  await page.click('[data-sim-play]')
  check('while playing, map and scene are on the same frame',
    near(pair.map.lat, pair.live.lat, 1e-9) && near(pair.map.lng, pair.live.lng, 1e-9)
      && near(pair.scene[0], pair.live.x, 0.02) && near(pair.scene[1], pair.live.alt, 0.02) && near(pair.scene[2], -pair.live.y, 0.02))
  await shot('02-survey.png')

  // ---- 6. cameras ---------------------------------------------------------------------------
  await seek(lineMid)
  s = await root()
  const cam = async mode => {
    await page.click(`[data-sim-camera="${mode}"]`)
    await page.waitForTimeout(500)
    await shot(`03-camera-${mode}.png`)
    return scene()
  }
  v = await cam('top')
  check('camera top-down: straight above the aircraft', near(v.camera.position[0], s.live.x, 0.05) && near(v.camera.position[2], -s.live.y, 0.05) && v.camera.position[1] > s.live.alt + 50 && v.aircraft.visible)
  v = await cam('fpv')
  check('camera FPV: at the aircraft, which is not drawn in front of its own lens',
    Math.hypot(v.camera.position[0] - s.live.x, v.camera.position[1] - s.live.alt, v.camera.position[2] + s.live.y) < 1 && !v.aircraft.visible)
  v = await cam('gimbal')
  check('camera view: through the survey camera, with one photo framed',
    Math.hypot(v.camera.position[0] - s.live.x, v.camera.position[1] - s.live.alt, v.camera.position[2] + s.live.y) < 1
      && v.camera.fov > 30 && v.camera.fov < 110 && await page.locator('[data-sim-3d] .border-emerald-400\\/90').count() === 1,
    `field of view ${v.camera.fov}°`)
  v = await cam('follow')
  const followDistance = Math.hypot(v.camera.position[0] - s.live.x, v.camera.position[1] - s.live.alt, v.camera.position[2] + s.live.y)
  check('camera follow: near the aircraft, looking at it', followDistance > 20 && followDistance < 2000 && v.aircraft.visible, `${Math.round(followDistance)} m away`)
  await seek(lineMid + 20)
  const moved = await scene()
  const s2 = await root()
  check('camera follow: moves with the aircraft',
    near(Math.hypot(moved.camera.position[0] - s2.live.x, moved.camera.position[1] - s2.live.alt, moved.camera.position[2] + s2.live.y), followDistance, 1))
  v = await cam('free')
  const freeAt = v.camera.position
  await seek(lineMid)
  check('camera free: stays where the user put it', JSON.stringify((await scene()).camera.position) === JSON.stringify(freeAt))
  // Dragging orbits the free camera.
  const box = await page.locator('[data-sim-3d-host] canvas').boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 30, { steps: 6 })
  await page.mouse.up()
  await page.waitForTimeout(600)
  check('camera free: the mouse orbits the scene', JSON.stringify((await scene()).camera.position) !== JSON.stringify(freeAt))
  await page.click('[data-sim-camera="follow"]')

  // ---- 7. layouts, coverage, profile ------------------------------------------------------------
  await page.click('[data-layout="2d"]')
  await page.waitForTimeout(400)
  check('layout 2D: the map alone', await page.locator('[data-sim-3d]').count() === 0 && await page.locator('[data-plan-map]').isVisible())
  await page.click('[data-layout="3d"]')
  await page.waitForSelector('[data-sim-3d="ready"]', { timeout: 30000 })
  check('layout 3D: the scene alone, the map kept', !(await page.locator('[data-plan-map]').isVisible()) && await page.locator('[data-plan-map]').count() === 1)
  await page.click('[data-layout="split"]')
  await page.waitForSelector('[data-sim-3d="ready"]', { timeout: 30000 })
  await page.waitForTimeout(400)
  s = await root()
  check('layout split: both, still on the same moment', await page.locator('[data-plan-map]').isVisible() && near(s.map.aircraft.lat, s.live.lat, 1e-9)
    && near((await scene()).aircraft.position[0], s.live.x, 0.02))

  await page.selectOption('[data-sim-coverage]', 'overlap')
  await page.waitForTimeout(300)
  check('coverage: photos-per-point view with its legend', /1 photo/.test(await page.locator('[data-sim-overlap-legend]').innerText()) && (await scene()).coverage)
  await shot('04-overlap.png')
  await page.selectOption('[data-sim-coverage]', 'off')
  await page.waitForTimeout(300)
  s = await root()
  check('coverage: off in both views', !s.map.coverage && !(await scene()).coverage)
  await page.selectOption('[data-sim-coverage]', 'covered')

  const profile = await page.locator('[data-sim-profile] svg').boundingBox()
  await page.mouse.click(profile.x + profile.width * 0.75, profile.y + profile.height / 2)
  await page.waitForTimeout(300)
  s = await root()
  check('profile: a click jumps to that distance of the flight', near(s.live.distance / s.summary.distance, 0.75, 0.03), `${Math.round(s.live.distance)} m of ${Math.round(s.summary.distance)} m`)
  const cursor = await page.getAttribute('[data-sim-profile-cursor]', 'data-x')
  check('profile: the cursor follows the simulation', near(Number(cursor) / 420, 0.75, 0.04))
  if (!OFFLINE) {
    check('profile: shows the ground and the lowest clearance', await page.locator('[data-sim-profile-lowest]').count() === 1
      && /ground \d+ m/.test(await page.locator('[data-sim-profile-readout]').innerText()))
  }

  await page.click('[data-sim-details] summary')
  const compare = await page.locator('[data-sim-compare]').innerText()
  check('details: simulation and plan side by side', /Flight time/.test(compare) && /Photos/.test(compare) && /Batteries/.test(compare))
  check('details: coverage figures and assumptions', /Survey area covered/.test(await page.locator('[data-sim-coverage-figures]').innerText())
    && /Ground:/.test(await page.locator('[data-sim-notes]').innerText()))
  await shot('05-details.png')

  // The whole flight ends on the ground with everything photographed.
  await seek(1e9)
  s = await root()
  check('at the end: landed at the takeoff point, every photo taken, the area covered',
    s.live.phaseKey === 'landed' && near(s.live.alt, 0, 0.01) && s.live.photos === hills.mission.stats.photo_count && s.live.coverage > 0.98
      && near(s.live.lat, hills.mission.takeoff.lat, 2e-7),
    `${Math.round(s.live.coverage * 100)} % covered`)

  // ---- 8. editing while simulating --------------------------------------------------------------
  await page.fill('#fp-front', '70')
  await page.locator('#fp-front').blur()
  await page.waitForFunction(n => {
    const snap = document.querySelector('[data-flight-sim]').simSnapshot()
    return snap.status === 'ready' && snap.summary && snap.summary.photos !== n
  }, hills.mission.stats.photo_count, { timeout: 60000 })
  s = await root()
  check('editing the plan rebuilds the simulation from the start', s.playback.time === 0 && s.summary.photos < hills.mission.stats.photo_count,
    `${s.summary.photos} photos at 70 % front overlap`)

  // ---- 9. closing leaves the planner and the plan as they were ------------------------------------
  await page.click('[data-sim-exit]')
  await page.waitForTimeout(500)
  check('exit closes the dock, the 3D view and the map layer',
    await page.locator('[data-sim-dock]').count() === 0 && await page.locator('[data-sim-3d]').count() === 0
      && await page.locator('.flightsim-marker').count() === 0 && await status() === 'off')
  const planAfter = await api('get_plan', { name: hills.name })
  check('the stored plan is untouched: same mission, same modification time',
    planAfter.modified === planBefore.modified && JSON.stringify(planAfter.mission) === JSON.stringify(planBefore.mission)
      && JSON.stringify(planAfter.definition) === JSON.stringify(planBefore.definition))
  const exportAfter = await api('export_options', { name: hills.name })
  check('export options are what they were', JSON.stringify(exportAfter.formats) === JSON.stringify(exportBefore.formats))

  // ---- 10. several batteries ------------------------------------------------------------------------
  await openSimulation(multi.name)
  s = await root()
  const flights = multi.mission.flights.length
  check('multi-battery: one flight per battery on the timeline', await page.locator('[data-sim-flight]').count() === flights && s.summary.flights === flights, `${flights} flights`)
  await page.click('[data-sim-flight="1"]')
  await page.waitForTimeout(250)
  s = await root()
  check('multi-battery: a flight starts with a full battery at the takeoff point',
    s.live.flight === 1 && s.live.phaseKey === 'preflight' && s.live.battery === 100 && near(s.live.lat, multi.mission.takeoff.lat, 2e-7))
  await seek(s.playback.time - 20)
  s = await root()
  check('multi-battery: a battery change on the ground in between', s.live.phaseKey === 'battery' && !s.live.airborne && /Battery change before flight 2/.test(await tile('phase')))
  await seek(s.playback.time - 60)
  s = await root()
  check('multi-battery: the flight before ended within the usable battery', s.live.flight === 0 && s.live.battery < 60 && s.live.battery > 25 && !s.live.belowReserve,
    `${s.live.battery.toFixed(0)} % left`)
  check('multi-battery: per-flight clock on the dashboard', await page.locator('[data-sim-tile="flight"]').count() === 1)
  await shot('06-batteries.png')

  // ---- 11. a fixed-wing ---------------------------------------------------------------------------------
  await openSimulation(wing.name)
  s = await root()
  check('fixed-wing: turns are flown as curves, and the difference from the plan is stated', s.notes.some(n => /curves of \d+ m radius/.test(n)), s.notes.join(' '))
  const banked = await page.evaluate(() => {
    const el = document.querySelector('[data-flight-sim]')
    const scrub = document.querySelector('[data-sim-scrub]')
    const max = Number(scrub.max)
    let most = 0
    let slowest = Infinity
    for (let t = 0; t < max; t += max / 600) {
      scrub.value = String(t)
      scrub.dispatchEvent(new Event('input', { bubbles: true }))
      const st = el.simSnapshot().live
      if (st.phaseKey === 'turn') { most = Math.max(most, Math.abs(st.roll)); slowest = Math.min(slowest, st.speed) }
    }
    return { most, slowest }
  })
  check('fixed-wing: banks through its turns and never stops', banked.most > 8 && banked.most <= 45 && banked.slowest > 5, `up to ${banked.most.toFixed(0)}° of bank, never below ${banked.slowest.toFixed(1)} m/s`)
  await shot('07-fixed-wing.png')

  // ---- 12. landing in place, no elevation data ------------------------------------------------------------
  await openSimulation(landing.name)
  await seek(1e9)
  s = await root()
  const lastWp = landing.mission.waypoints.at(-1)
  check('land in place: ends on the ground at the last waypoint', s.live.phaseKey === 'landed' && near(s.live.lat, lastWp.lat, 2e-7) && near(s.live.lng, lastWp.lng, 2e-7) && near(s.live.alt, 0, 0.01))
  check('no elevation data: height above ground is "not known", the 3D ground flat and labelled',
    /not known/.test(await tile('clearance')) && (await scene()).terrain.flat && await page.locator('[data-sim-3d-flat]').count() === 1)

  // ---- 13. a mission near the planner's limits -------------------------------------------------------------
  const big = await plan('E2E sim large', { geometry: rect(3000, 2400), takeoff: offset(-1600, 0), params: { altitude_m: 40, vlos_m: 6000 } })
  const t0 = Date.now()
  await openSimulation(big.name)
  const opened = Date.now() - t0
  s = await root()
  check('large mission: opens in a few seconds', opened < 30000 && s.summary.photos === big.mission.stats.photo_count,
    `${big.mission.stats.waypoint_count} waypoints, ${big.mission.stats.photo_count} photos, ${big.mission.flights.length} batteries: ready in ${(opened / 1000).toFixed(1)} s`)
  const jump = await page.evaluate(() => {
    const scrub = document.querySelector('[data-sim-scrub]')
    const max = Number(scrub.max)
    const t = performance.now()
    for (let i = 0; i < 200; i++) { scrub.value = String((i * 7919) % max); scrub.dispatchEvent(new Event('input', { bubbles: true })) }
    return (performance.now() - t) / 200
  })
  check('large mission: a jump anywhere in the timeline takes milliseconds', jump < 50, `${jump.toFixed(1)} ms per jump, map and scene updated`)
  await page.selectOption('[data-sim-speed]', '64')
  await page.click('[data-sim-play]')
  const rate = await fps(3000)
  await page.click('[data-sim-play]')
  // Reported rather than tuned: the rate depends on the machine and on what else it is doing. Software
  // WebGL in a headless browser composites slowly whatever is drawn; a GPU shows the real rate. The
  // floor only catches a collapse (per-frame work that grows with the mission).
  check(`large mission: plays at ${rate.toFixed(0)} frames per second at 64x${GPU ? '' : ' (software rendering)'}`, GPU ? rate > 10 : rate > 0.5)
  v = await scene()
  check('large mission: the scene stays a handful of draw calls', v.draw.calls < 60, `${v.draw.calls} draw calls for ${v.path.pieces} path pieces and ${big.mission.stats.photo_count} photos`)
  await shot('08-large.png')

  // ---- 13b. a survey's own surface model as the ground (optional) -------------------------------------------
  if (DSM_TASK && DSM_CENTER.length === 2 && DSM_CENTER.every(Number.isFinite)) {
    const [clat, clng] = DSM_CENTER
    const dLat = 125 / 110574
    const dLng = 150 / (111320 * Math.cos(clat * Math.PI / 180))
    const survey = await plan('E2E sim survey DSM', {
      geometry: { type: 'Polygon', coordinates: [[[clng - dLng, clat - dLat], [clng + dLng, clat - dLat], [clng + dLng, clat + dLat], [clng - dLng, clat + dLat], [clng - dLng, clat - dLat]]] },
      takeoff: [clng - dLng * 0.9, clat - dLat * 0.9], elevation_source: `task:${DSM_TASK}:dsm`,
      params: { altitude_mode: 'terrain', altitude_m: 80, vlos_m: 1500 },
    })
    const tiles = []
    const onTile = res => { if (res.url().includes('webodm_core.api.tiles.serve')) tiles.push(res.status()) }
    page.on('response', onTile)
    await openSimulation(survey.name)
    const grid = terrainCalls.at(-1)?.answer?.terrain
    check('survey DSM: the ground is the project\'s own surface model, within the planner\'s grid budget',
      grid?.source?.kind === 'project-raster' && grid.source.surface === 'surface' && grid.rows * grid.cols <= 10000 && grid.rows * grid.cols > 400,
      grid ? `${grid.rows} x ${grid.cols} nodes, ${grid.spacing_m} m apart` : 'no grid')
    v = await scene()
    check('survey DSM: drawn in 3D node for node', v.terrain.rows === grid?.rows && v.terrain.cols === grid?.cols && !v.terrain.flat, `${v.draw.triangles} triangles`)
    s = await root()
    check('survey DSM: lowest clearance agrees with the plan\'s to within the following tolerance',
      s.summary.lowestClearance !== null && Math.abs(s.summary.lowestClearance - survey.mission.terrain.clearance_min_m) < (survey.mission.terrain.tolerance_m || 5) + 1,
      `${s.summary.lowestClearance?.toFixed(1)} m simulated, ${survey.mission.terrain.clearance_min_m} m planned`)
    // The project's orthophoto, switched on in the map's options, is draped on the 3D ground as well.
    if (await page.locator('label:has-text("Latest orthophoto")').count()) {
      await page.click('button[title="Show map options"]')
      await page.locator('label:has-text("Latest orthophoto") input').check()
      await page.waitForTimeout(5000)
      check('survey orthophoto: its tiles are loaded for both views', tiles.length > 4 && tiles.every(code => code === 200), `${tiles.length} tiles`)
      await shot('08b-survey-dsm.png')
    }
    page.off('response', onTile)
  }

  // ---- 14. phone ------------------------------------------------------------------------------------------------
  await page.setViewportSize({ width: 390, height: 844 })
  await openSimulation(landing.name)
  check('phone: the plan panel makes way, controls and exit are reachable',
    !(await page.locator('aside[aria-label="Flight plan"]').isVisible()) && await page.locator('[data-sim-play]').isVisible() && await page.locator('[data-sim-exit]').isVisible())
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  check('phone: no horizontal overflow', overflow <= 1, `${overflow}px`)
  await shot('09-phone.png')
  await page.click('[data-sim-exit]')
  await page.waitForTimeout(300)
  check('phone: closing brings the plan panel back', await page.locator('aside[aria-label="Flight plan"]').isVisible())
  await page.setViewportSize({ width: 1600, height: 950 })
} catch (e) {
  check('run completed without an exception', false, e.stack || e.message)
  await shot('99-failure.png').catch(() => {})
} finally {
  for (const name of created) await api('delete_plan', { name }).catch(() => {})
}

check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 5).join(' | '))
check('no failed requests to the platform', failedRequests.length === 0, failedRequests.slice(0, 5).join(' | '))
await browser.close()

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
