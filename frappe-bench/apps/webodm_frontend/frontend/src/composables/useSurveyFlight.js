/**
 * useSurveyFlight — canvas renderer for the landing-page hero.
 *
 * Plays one loop: a drone flies a serpentine survey grid over an elevation
 * model, each camera footprint flashes and freezes into imagery, a scan line
 * stitches the tiles into a seamless orthophoto, then a measurement is drawn.
 *
 * Pure 2D canvas with a hand-rolled isometric projection — no 3D dependency.
 * Everything is derived from a single normalised loop clock so the scene is
 * deterministic and resize-safe.
 */

// ── loop timeline (fractions of one cycle) ─────────────────────────────────
const SURVEY_START = 0.05
const SURVEY_END = 0.6
const STITCH_END = 0.74
const MEASURE_END = 0.93
const FADE_END = 0.985
const LOOP_SECONDS = 18

const PHASE = { STANDBY: 'STANDBY', SURVEY: 'SURVEY', STITCH: 'STITCH', MEASURE: 'MEASURE' }

// ── terrain definition ─────────────────────────────────────────────────────
const GRID = 26

function terrainH(x, z) {
  const rolling = Math.sin(x * 6.2 + 0.7) * Math.cos(z * 4.8 - 0.3) * 0.032
  const ripple = Math.sin((x + z) * 9.5 + 1.4) * 0.011
  const hill = Math.exp(-(((x - 0.68) ** 2 + (z - 0.34) ** 2) / 0.05)) * 0.082
  const hollow = -Math.exp(-(((x - 0.28) ** 2 + (z - 0.74) ** 2) / 0.055)) * 0.046
  const furrow = Math.sin((x - z) * 13.0) * 0.006
  return rolling + ripple + hill + hollow + furrow + 0.062
}

function hash(n) {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453
  return s - Math.floor(s)
}

// ── small math helpers ─────────────────────────────────────────────────────
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v)
const lerp = (a, b, t) => a + (b - a) * t
const inv = (a, b, v) => clamp((v - a) / (b - a), 0, 1)
const smooth = (t) => t * t * (3 - 2 * t)
/** convert a duration in real seconds to the normalised loop clock */
const sec = (seconds) => seconds / LOOP_SECONDS

// ── palettes (chrome stays slate/blue; imagery owns the only earth tones) ──
function modelColor(h) {
  const t = inv(0.015, 0.19, h)
  return `rgb(${lerp(13, 38, t) | 0},${lerp(21, 51, t) | 0},${lerp(36, 60, t) | 0})`
}

function imageryRGB(x, z, h) {
  if (h < 0.038) return [24, 46, 62]
  const t = inv(0.02, 0.19, h)
  const patch =
    Math.sin(x * 9.1 + 1.3) * Math.cos(z * 7.7 - 0.6) +
    0.5 * Math.sin(x * 21 + z * 17) +
    0.35 * Math.sin(x * 3.1 - z * 4.4)
  return [
    clamp(lerp(30, 76, t) + patch * 7, 0, 255),
    clamp(lerp(44, 80, t) + patch * 8, 0, 255),
    clamp(lerp(36, 54, t) + patch * 4, 0, 255),
  ]
}

export function createSurveyFlight(canvas, options = {}) {
  const onHud = options.onHud || (() => {})
  const reduced = !!options.reduced

  const ctx = canvas.getContext('2d', { alpha: false })
  const view = { w: 0, h: 0, dpr: 1, s: 0, cx: 0, cy: 0 }

  let modelLayer = null
  let path = null // { points, length }
  let photos = [] // { x, z, bright, corners: [[sx,sy]…], cxScreen, cyScreen }
  let raf = 0
  let running = false
  let clock = 0 // seconds, advanced by real time when running
  let lastHudKey = ''

  // ── projection ───────────────────────────────────────────────────────────
  function project(x, y, z) {
    const ax = x - 0.5
    const az = z - 0.5
    return [
      view.cx + (ax - az) * view.s * 0.5,
      view.cy + (ax + az) * view.s * 0.25 - y * view.s * 0.72,
    ]
  }

  // ── serpentine flight path, resampled by arc length ──────────────────────
  function buildPath() {
    const rows = 9
    const raw = []
    for (let r = 0; r < rows; r++) {
      const z = 0.11 + r * (0.78 / (rows - 1))
      if (r % 2 === 0) raw.push([0.1, z], [0.9, z])
      else raw.push([0.9, z], [0.1, z])
    }
    let length = 0
    const segments = []
    for (let i = 0; i < raw.length - 1; i++) {
      const [x0, z0] = raw[i]
      const [x1, z1] = raw[i + 1]
      const d = Math.hypot(x1 - x0, z1 - z0)
      segments.push({ x0, z0, x1, z1, d, at: length })
      length += d
    }
    return { raw, segments, length }
  }

  function pathAt(p) {
    const target = clamp(p, 0, 1) * path.length
    for (const seg of path.segments) {
      if (target <= seg.at + seg.d || seg === path.segments[path.segments.length - 1]) {
        const t = seg.d === 0 ? 0 : clamp((target - seg.at) / seg.d, 0, 1)
        return [lerp(seg.x0, seg.x1, t), lerp(seg.z0, seg.z1, t)]
      }
    }
    return [0.9, 0.89]
  }

  function buildPhotos() {
    const spacing = 0.072
    const total = Math.max(1, Math.round(path.length / spacing))
    const halfX = 0.06
    const halfZ = 0.045
    photos = []
    for (let i = 0; i < total; i++) {
      const [x, z] = pathAt((i + 0.5) / total)
      photos.push({
        x,
        z,
        bright: 0.84 + hash(i * 3.7) * 0.32,
        captureAt: SURVEY_START + ((i + 0.5) / total) * (SURVEY_END - SURVEY_START),
        cornersW: [
          [x - halfX, z - halfZ],
          [x + halfX, z - halfZ],
          [x + halfX, z + halfZ],
          [x - halfX, z + halfZ],
        ],
      })
    }
  }

  // ── offscreen static layers ──────────────────────────────────────────────
  function buildModelLayer() {
    const layer = document.createElement('canvas')
    layer.width = Math.max(1, Math.round(view.w * view.dpr))
    layer.height = Math.max(1, Math.round(view.h * view.dpr))
    const g = layer.getContext('2d')
    g.setTransform(view.dpr, 0, 0, view.dpr, 0, 0)

    g.fillStyle = '#020617'
    g.fillRect(0, 0, view.w, view.h)

    const lift = 0.0015
    const hAt = (i, j) => terrainH(i / GRID, j / GRID) + lift
    const pt = (i, j) => project(i / GRID, hAt(i, j), j / GRID)

    // painter's algorithm: sweep cells along the isometric depth diagonal
    for (let d = 0; d <= 2 * (GRID - 1); d++) {
      for (let i = Math.max(0, d - (GRID - 1)); i <= Math.min(GRID - 1, d); i++) {
        const j = d - i
        const p0 = pt(i, j)
        const p1 = pt(i + 1, j)
        const p2 = pt(i + 1, j + 1)
        const p3 = pt(i, j + 1)
        const h = (hAt(i, j) + hAt(i + 1, j) + hAt(i + 1, j + 1) + hAt(i, j + 1)) / 4
        g.beginPath()
        g.moveTo(p0[0], p0[1])
        g.lineTo(p1[0], p1[1])
        g.lineTo(p2[0], p2[1])
        g.lineTo(p3[0], p3[1])
        g.closePath()
        g.fillStyle = modelColor(h)
        g.fill()
        g.strokeStyle = 'rgba(51, 65, 85, 0.32)'
        g.lineWidth = 0.6
        g.stroke()
      }
    }

    // site boundary
    const b = [
      project(0, terrainH(0, 0) + lift, 0),
      project(1, terrainH(1, 0) + lift, 0),
      project(1, terrainH(1, 1) + lift, 1),
      project(0, terrainH(0, 1) + lift, 1),
    ]
    g.beginPath()
    g.moveTo(b[0][0], b[0][1])
    for (let i = 1; i < 4; i++) g.lineTo(b[i][0], b[i][1])
    g.closePath()
    g.strokeStyle = 'rgba(100, 116, 139, 0.55)'
    g.lineWidth = 1
    g.stroke()

    // corner ticks
    g.strokeStyle = 'rgba(96, 165, 250, 0.7)'
    g.lineWidth = 1.5
    for (const [sx, sy] of b) {
      g.beginPath()
      g.moveTo(sx - 4, sy)
      g.lineTo(sx + 4, sy)
      g.moveTo(sx, sy - 4)
      g.lineTo(sx, sy + 4)
      g.stroke()
    }

    modelLayer = layer
  }

  function layout() {
    const rect = canvas.getBoundingClientRect()
    const w = rect.width || canvas.clientWidth || 640
    const h = rect.height || canvas.clientHeight || 480
    view.w = w
    view.h = h
    view.dpr = Math.min(window.devicePixelRatio || 1, 2)
    view.s = Math.min(w * 0.92, h * 1.4)
    view.cx = w / 2
    view.cy = h * 0.6

    canvas.width = Math.max(1, Math.round(w * view.dpr))
    canvas.height = Math.max(1, Math.round(h * view.dpr))

    buildModelLayer()
    for (const photo of photos) {
      photo.corners = photo.cornersW.map(([x, z]) =>
        project(x, terrainH(x, z) + 0.003, z)
      )
      photo.cxScreen = (photo.corners[0][0] + photo.corners[2][0]) / 2
      photo.cyScreen = (photo.corners[0][1] + photo.corners[2][1]) / 2
    }
  }

  // ── draw helpers ─────────────────────────────────────────────────────────
  function poly(g, corners) {
    g.beginPath()
    g.moveTo(corners[0][0], corners[0][1])
    for (let i = 1; i < corners.length; i++) g.lineTo(corners[i][0], corners[i][1])
    g.closePath()
  }

  function phaseAt(t) {
    if (t < SURVEY_START) return PHASE.STANDBY
    if (t < SURVEY_END) return PHASE.SURVEY
    if (t < STITCH_END) return PHASE.STITCH
    if (t < MEASURE_END) return PHASE.MEASURE
    return PHASE.STANDBY
  }

  function dronePos(t) {
    let p
    if (t < SURVEY_START) p = 0
    else if (t < SURVEY_END) p = inv(SURVEY_START, SURVEY_END, t)
    else p = 1
    const [x, z] = pathAt(p)
    const hover = 0.3 + Math.sin(t * LOOP_SECONDS * 5) * 0.004
    return { x, z, y: terrainH(x, z) + hover }
  }

  function drawTrail(g, t) {
    const flown = t < SURVEY_START ? 0 : t < SURVEY_END ? inv(SURVEY_START, SURVEY_END, t) : 1
    const ground = (p) => {
      const [x, z] = pathAt(p)
      return project(x, terrainH(x, z) + 0.004, z)
    }

    // planned route
    g.save()
    g.setLineDash([4, 5])
    g.strokeStyle = 'rgba(100, 116, 139, 0.45)'
    g.lineWidth = 1
    g.beginPath()
    let first = true
    for (let i = 0; i <= 80; i++) {
      const p = i / 80
      if (p < flown) continue
      const [sx, sy] = ground(p)
      if (first) {
        g.moveTo(sx, sy)
        first = false
      } else g.lineTo(sx, sy)
    }
    g.stroke()
    g.restore()

    // flown track
    if (flown > 0) {
      g.save()
      g.strokeStyle = 'rgba(96, 165, 250, 0.85)'
      g.lineWidth = 1.6
      g.shadowColor = 'rgba(59, 130, 246, 0.7)'
      g.shadowBlur = 6
      g.beginPath()
      const steps = Math.max(2, Math.round(flown * 80))
      for (let i = 0; i <= steps; i++) {
        const [sx, sy] = ground((i / steps) * flown)
        if (i === 0) g.moveTo(sx, sy)
        else g.lineTo(sx, sy)
      }
      g.stroke()
      g.restore()
    }
  }

  function drawTiles(g, t, stitchProgress) {
    for (const photo of photos) {
      if (t < photo.captureAt) continue
      const age = t - photo.captureAt
      const appear = clamp(age / sec(0.35), 0, 1)
      const [r, gr, b] = imageryRGB(photo.x, photo.z, terrainH(photo.x, photo.z))

      // the stitch line normalises per-tile exposure as it sweeps past
      const localStitch = clamp((stitchProgress - photo.z) * 9, 0, 1)
      const bright = lerp(photo.bright, 1, localStitch)
      const seam = 0.34 * (1 - localStitch)

      poly(g, photo.corners)
      g.globalAlpha = appear
      g.fillStyle = `rgb(${clamp(r * bright, 0, 255) | 0},${clamp(gr * bright, 0, 255) | 0},${clamp(b * bright, 0, 255) | 0})`
      g.fill()
      if (seam > 0.01) {
        g.strokeStyle = `rgba(2, 6, 23, ${seam})`
        g.lineWidth = 1
        g.stroke()
      }

      // capture flash
      if (age < sec(0.45)) {
        g.globalAlpha = appear * (1 - age / sec(0.45)) * 0.6
        g.fillStyle = '#fbbf24'
        g.fill()
      }

      // SfM tie points surface as the mosaic resolves
      if (localStitch > 0.05 && localStitch < 1) {
        g.globalAlpha = (1 - Math.abs(localStitch - 0.5) * 2) * 0.9
        g.strokeStyle = 'rgba(147, 197, 253, 0.9)'
        g.lineWidth = 1
        g.beginPath()
        g.moveTo(photo.cxScreen - 3, photo.cyScreen)
        g.lineTo(photo.cxScreen + 3, photo.cyScreen)
        g.moveTo(photo.cxScreen, photo.cyScreen - 3)
        g.lineTo(photo.cxScreen, photo.cyScreen + 3)
        g.stroke()
      }
    }
    g.globalAlpha = 1
  }

  function drawStitchScan(g, t) {
    if (t < SURVEY_END || t >= STITCH_END) return
    const bandZ = inv(SURVEY_END, STITCH_END, t)

    // a straight world-space line at constant z projects to a straight screen line
    const [ax, ay] = project(0, 0.3, bandZ)
    const [bx, by] = project(1, 0.3, bandZ)

    g.save()
    g.globalCompositeOperation = 'lighter'

    // soft sweep highlight
    const sweepGrad = g.createLinearGradient(ax, ay, bx, by)
    sweepGrad.addColorStop(0, 'rgba(59, 130, 246, 0)')
    sweepGrad.addColorStop(0.5, 'rgba(59, 130, 246, 0.18)')
    sweepGrad.addColorStop(1, 'rgba(59, 130, 246, 0)')
    g.strokeStyle = sweepGrad
    g.lineWidth = view.s * 0.045
    g.beginPath()
    g.moveTo(ax, ay)
    g.lineTo(bx, by)
    g.stroke()

    // crisp leading edge
    g.strokeStyle = 'rgba(147, 197, 253, 0.55)'
    g.lineWidth = 1.4
    g.beginPath()
    g.moveTo(ax, ay)
    g.lineTo(bx, by)
    g.stroke()

    g.restore()
  }

  const MEASURE = { x0: 0.3, z0: 0.32, x1: 0.62, z1: 0.6 }

  function measureProgress(t) {
    if (t < STITCH_END) return { draw: 0, fill: 0, area: 0 }
    const p = inv(STITCH_END, MEASURE_END, t)
    return {
      draw: clamp(p / 0.45, 0, 1),
      fill: clamp((p - 0.35) / 0.4, 0, 1),
      area: smooth(clamp((p - 0.4) / 0.5, 0, 1)) * 1.43,
    }
  }

  function measureCorners() {
    const c = [
      [MEASURE.x0, MEASURE.z0],
      [MEASURE.x1, MEASURE.z0],
      [MEASURE.x1, MEASURE.z1],
      [MEASURE.x0, MEASURE.z1],
    ]
    return c.map(([x, z]) => project(x, terrainH(x, z) + 0.006, z))
  }

  function drawMeasure(g, t, fade) {
    const mp = measureProgress(t)
    if (mp.draw <= 0) return
    const corners = measureCorners()
    g.save()
    g.globalAlpha = fade

    // translucent footprint
    if (mp.fill > 0) {
      poly(g, corners)
      g.globalAlpha = fade * mp.fill * 0.22
      g.fillStyle = '#3b82f6'
      g.fill()
      g.globalAlpha = fade
    }

    // dashed perimeter drawn progressively
    const perim = []
    for (let i = 0; i < 4; i++) {
      const a = corners[i]
      const b = corners[(i + 1) % 4]
      perim.push(Math.hypot(b[0] - a[0], b[1] - a[1]))
    }
    const total = perim.reduce((s, v) => s + v, 0)
    let remaining = total * mp.draw
    g.strokeStyle = '#60a5fa'
    g.lineWidth = 1.6
    g.setLineDash([6, 5])
    g.beginPath()
    for (let i = 0; i < 4 && remaining > 0; i++) {
      const a = corners[i]
      const b = corners[(i + 1) % 4]
      const seg = Math.min(perim[i], remaining)
      const u = perim[i] === 0 ? 0 : seg / perim[i]
      g.moveTo(a[0], a[1])
      g.lineTo(lerp(a[0], b[0], u), lerp(a[1], b[1], u))
      remaining -= seg
    }
    g.stroke()
    g.setLineDash([])

    // corner ticks
    g.strokeStyle = '#93c5fd'
    g.lineWidth = 1.5
    for (const [sx, sy] of corners) {
      g.beginPath()
      g.moveTo(sx - 4, sy)
      g.lineTo(sx + 4, sy)
      g.moveTo(sx, sy - 4)
      g.lineTo(sx, sy + 4)
      g.stroke()
    }
    g.restore()
  }

  function drawDrone(g, t, fade) {
    const { x, z, y } = dronePos(t)
    const [sx, sy] = project(x, y, z)
    const sc = view.s * 0.05

    g.save()
    g.globalAlpha = fade

    // ground shadow
    const [shx, shy] = project(x, terrainH(x, z) + 0.002, z)
    g.globalAlpha = fade * 0.3
    g.fillStyle = '#020617'
    g.beginPath()
    g.ellipse(shx, shy, sc * 0.6, sc * 0.24, 0, 0, Math.PI * 2)
    g.fill()
    g.globalAlpha = fade

    // camera frustum down to the live footprint
    const half = 0.06
    const frustum = [
      [x - half, z - 0.045],
      [x + half, z - 0.045],
      [x + half, z + 0.045],
      [x - half, z + 0.045],
    ].map(([fx, fz]) => project(fx, terrainH(fx, fz) + 0.004, fz))
    g.globalAlpha = fade * 0.12
    g.fillStyle = '#60a5fa'
    g.beginPath()
    g.moveTo(sx, sy)
    g.lineTo(frustum[0][0], frustum[0][1])
    g.lineTo(frustum[1][0], frustum[1][1])
    g.lineTo(frustum[2][0], frustum[2][1])
    g.lineTo(frustum[3][0], frustum[3][1])
    g.closePath()
    g.fill()
    g.globalAlpha = fade * 0.3
    g.strokeStyle = '#3b82f6'
    g.lineWidth = 0.8
    poly(g, frustum)
    g.stroke()
    g.globalAlpha = fade

    // arms + rotors
    const spin = t * LOOP_SECONDS * 34
    const arms = [-Math.PI * 0.75, -Math.PI * 0.25, Math.PI * 0.25, Math.PI * 0.75]
    for (const angle of arms) {
      const tx = sx + Math.cos(angle) * sc * 1.05
      const ty = sy + Math.sin(angle) * sc * 0.62
      g.strokeStyle = '#475569'
      g.lineWidth = Math.max(1, sc * 0.11)
      g.beginPath()
      g.moveTo(sx, sy)
      g.lineTo(tx, ty)
      g.stroke()

      g.strokeStyle = 'rgba(148, 163, 184, 0.65)'
      g.lineWidth = 1
      g.beginPath()
      g.ellipse(tx, ty, sc * 0.5, sc * 0.19, 0, 0, Math.PI * 2)
      g.stroke()

      // rotor blade
      const blade = spin * (angle < 0 ? 1 : -1)
      g.strokeStyle = 'rgba(203, 213, 225, 0.75)'
      g.lineWidth = 1.4
      g.beginPath()
      g.moveTo(tx - Math.cos(blade) * sc * 0.5, ty - Math.sin(blade) * sc * 0.19)
      g.lineTo(tx + Math.cos(blade) * sc * 0.5, ty + Math.sin(blade) * sc * 0.19)
      g.stroke()
    }

    // body
    g.beginPath()
    g.moveTo(sx, sy - sc * 0.44)
    g.lineTo(sx + sc * 0.66, sy)
    g.lineTo(sx, sy + sc * 0.44)
    g.lineTo(sx - sc * 0.66, sy)
    g.closePath()
    g.fillStyle = '#0f172a'
    g.fill()
    g.strokeStyle = '#64748b'
    g.lineWidth = 1.2
    g.stroke()

    // gimbal camera
    g.beginPath()
    g.arc(sx, sy + sc * 0.2, sc * 0.19, 0, Math.PI * 2)
    g.fillStyle = '#1e293b'
    g.fill()
    g.strokeStyle = '#60a5fa'
    g.lineWidth = 1.2
    g.stroke()

    // nav lights
    const blink = Math.sin(t * LOOP_SECONDS * 6) > 0
    g.globalAlpha = fade * (blink ? 1 : 0.25)
    g.fillStyle = '#f87171'
    g.beginPath()
    g.arc(sx - sc * 0.66, sy, sc * 0.09, 0, Math.PI * 2)
    g.fill()
    g.fillStyle = '#4ade80'
    g.beginPath()
    g.arc(sx + sc * 0.66, sy, sc * 0.09, 0, Math.PI * 2)
    g.fill()

    g.restore()
  }

  function drawCompass(g) {
    const cx = view.w - 34
    const cy = 34
    const r = 13
    g.save()
    g.globalAlpha = 0.5
    g.strokeStyle = '#334155'
    g.lineWidth = 1
    g.beginPath()
    g.arc(cx, cy, r, 0, Math.PI * 2)
    g.stroke()
    g.fillStyle = '#60a5fa'
    g.beginPath()
    g.moveTo(cx, cy - r + 3)
    g.lineTo(cx + 3.5, cy + 1)
    g.lineTo(cx, cy - 1)
    g.lineTo(cx - 3.5, cy + 1)
    g.closePath()
    g.fill()
    g.fillStyle = '#64748b'
    g.font = '600 9px ui-monospace, SFMono-Regular, Menlo, monospace'
    g.textAlign = 'center'
    g.fillText('N', cx, cy + r + 10)
    g.restore()
  }

  // ── HUD ──────────────────────────────────────────────────────────────────
  function pushHud(t, phase) {
    const flown = inv(SURVEY_START, SURVEY_END, clamp(t, SURVEY_START, SURVEY_END))
    const captured = photos.filter((p) => t >= p.captureAt).length
    const alt = 84 + Math.sin(t * LOOP_SECONDS * 1.1) * 3.4
    const spd = phase === PHASE.SURVEY ? 7.2 : 0
    const mp = measureProgress(t)
    const key = `${phase}|${captured}|${mp.area.toFixed(2)}|${flown.toFixed(2)}`
    if (key === lastHudKey) return
    lastHudKey = key
    onHud({
      phase,
      alt,
      spd,
      gsd: 2.1,
      captured,
      total: photos.length,
      progress: flown,
      area: mp.area,
      measuring: phase === PHASE.MEASURE,
    })
  }

  // ── frame ────────────────────────────────────────────────────────────────
  function drawScene(t) {
    const phase = phaseAt(t)
    const fade = t > FADE_END ? 1 - inv(FADE_END, 1, t) : t < SURVEY_START ? inv(0, SURVEY_START, t) : 1

    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.fillStyle = '#020617'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    if (modelLayer) ctx.drawImage(modelLayer, 0, 0, canvas.width, canvas.height)

    ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0)

    // atmospheric ground glow so the model sits in space
    const glow = ctx.createRadialGradient(view.cx, view.cy, 0, view.cx, view.cy, view.s * 0.7)
    glow.addColorStop(0, 'rgba(30, 58, 138, 0.16)')
    glow.addColorStop(1, 'rgba(2, 6, 23, 0)')
    ctx.fillStyle = glow
    ctx.fillRect(0, 0, view.w, view.h)

    drawTrail(ctx, t)
    const stitchProgress = inv(SURVEY_END, STITCH_END, clamp(t, 0, STITCH_END))
    drawTiles(ctx, t, t >= SURVEY_END ? stitchProgress : 0)
    drawStitchScan(ctx, t)
    drawMeasure(ctx, t, fade)
    drawDrone(ctx, t, fade)
    drawCompass(ctx)

    pushHud(t, phase)
  }

  function renderStatic() {
    // settled orthophoto with the measurement fully drawn and counted
    drawScene(MEASURE_END - 0.005)
  }

  let lastFrameAt = 0

  function frame(now) {
    if (!running) return
    clock += (now - lastFrameAt) / 1000
    lastFrameAt = now
    if (clock > LOOP_SECONDS) clock -= LOOP_SECONDS
    drawScene(clock / LOOP_SECONDS)
    raf = requestAnimationFrame(frame)
  }

  function start() {
    if (running || reduced) return
    running = true
    lastFrameAt = performance.now()
    raf = requestAnimationFrame(frame)
  }

  function stop() {
    running = false
    if (raf) cancelAnimationFrame(raf)
    raf = 0
  }

  return {
    init() {
      path = buildPath()
      buildPhotos()
      layout()
      if (reduced) renderStatic()
      else {
        drawScene(0)
        start()
      }
    },
    resize() {
      layout()
      if (reduced) renderStatic()
    },
    start,
    stop,
    destroy() {
      stop()
      modelLayer = null
    },
    get totalPhotos() {
      return photos.length
    },
  }
}
