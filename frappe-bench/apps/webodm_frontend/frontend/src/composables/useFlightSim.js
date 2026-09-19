/**
 * useFlightSim — interactive canvas flight simulation for the landing hero.
 *
 * A drone follows a survey waypoint circuit over a top-down mapping scene.
 * The visitor can switch the active layer (optical orthophoto / thermal /
 * point cloud), pause or reset the flight, and click the canvas to inspect a
 * mapped area of interest or reroute the drone to a new waypoint.
 *
 * Pure 2D canvas in normalised world coordinates, so the scene is
 * deterministic, resize-safe, and testable without Vue. The render loop is
 * driven by the host component, which starts and stops it based on visibility
 * and the prefers-reduced-motion preference.
 */

export const LAYERS = [
  { id: 'optical', label: 'Optical ortho' },
  { id: 'thermal', label: 'Thermal' },
  { id: 'pointcloud', label: 'Point cloud' },
]

const TAU = Math.PI * 2
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v)

// ── scene definition (normalised 0..1 world space) ─────────────────────────
const INITIAL_WAYPOINTS = [
  { x: 0.18, y: 0.26, label: 'WP-01 · North block' },
  { x: 0.46, y: 0.20, label: 'WP-02 · Ridge line' },
  { x: 0.74, y: 0.34, label: 'WP-03 · East block' },
  { x: 0.60, y: 0.62, label: 'WP-04 · Stockpile' },
  { x: 0.30, y: 0.72, label: 'WP-05 · Drainage' },
  { x: 0.16, y: 0.52, label: 'WP-06 · Access road' },
]

const AREAS_OF_INTEREST = [
  {
    id: 'aoi-stockpile',
    label: 'Stockpile volume',
    category: 'volumetric',
    confidence: 99.2,
    metric: '1.43 ha · 12,480 m³',
    details: 'Cut/fill volume computed against the DSM, not estimated from imagery.',
    x: 0.3,
    y: 0.6,
    w: 0.16,
    h: 0.13,
  },
  {
    id: 'aoi-footprint',
    label: 'Building footprint',
    category: 'vector',
    confidence: 98.4,
    metric: '2,140 m²',
    details: 'Rooftop outline vectorised from the orthophoto at 2.1 cm/px GSD.',
    x: 0.62,
    y: 0.22,
    w: 0.14,
    h: 0.12,
  },
  {
    id: 'aoi-vegetation',
    label: 'Vegetation encroachment',
    category: 'classification',
    confidence: 96.7,
    metric: '3.1 m clearance',
    details: 'Canopy classified from the point cloud and flagged near the access corridor.',
    x: 0.4,
    y: 0.7,
    w: 0.18,
    h: 0.14,
  },
  {
    id: 'aoi-water',
    label: 'Water body',
    category: 'classification',
    confidence: 97.9,
    metric: '0.62 ha',
    details: 'Surface water segmented from the orthomosaic and elevation breaklines.',
    x: 0.16,
    y: 0.3,
    w: 0.15,
    h: 0.12,
  },
]

const BASE_LAT = -6.2
const BASE_LNG = 106.8
const STATUS_PLAYING = 'SURVEYING'
const STATUS_PAUSED = 'HOLDING'
const DEFAULT_NOTICE = 'Click the map to inspect an area or reroute the drone'

function areaColor(area) {
  if (area.category === 'volumetric') return '#22d3ee'
  if (area.category === 'vector') return '#38bdf8'
  return '#34d399'
}

export function createFlightSim(canvas, options = {}) {
  const onTelemetry = options.onTelemetry || (() => {})
  const reduced = !!options.reduced
  const raf = options.raf || ((fn) => requestAnimationFrame(fn))
  const caf = options.caf || ((id) => cancelAnimationFrame(id))
  const now = options.now || (() => performance.now())

  const ctx =
    canvas && typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null

  const view = { w: 0, h: 0, dpr: 1, pad: 0, iw: 0, ih: 0 }

  let layer = 'optical'
  let playing = true
  let running = false
  let rafId = 0
  let lastFrame = 0
  let elapsed = 0
  let lastPublish = -Infinity
  let notice = DEFAULT_NOTICE

  const drone = {
    x: INITIAL_WAYPOINTS[0].x,
    y: INITIAL_WAYPOINTS[0].y,
    heading: 0,
    rotorAngle: 0,
    scanAngle: 0,
    speed: 0.12, // world units per second
  }

  let waypoints = INITIAL_WAYPOINTS.map((wp) => ({ ...wp }))
  let targetIndex = 1
  let selected = { ...AREAS_OF_INTEREST[0] }

  const telemetry = {
    altitude: 118,
    speed: 8.4,
    battery: 92,
    satellites: 19,
    latitude: BASE_LAT,
    longitude: BASE_LNG,
    heading: 0,
    gsd: 2.1,
    status: STATUS_PLAYING,
  }

  // ── coordinate mapping ───────────────────────────────────────────────────
  function project(x, y) {
    return [view.pad + x * view.iw, view.pad + y * view.ih]
  }

  function unproject(sx, sy) {
    if (!view.iw || !view.ih) return [0.5, 0.5]
    return [(sx - view.pad) / view.iw, (sy - view.pad) / view.ih]
  }

  // ── layout ───────────────────────────────────────────────────────────────
  function layout() {
    const rect =
      canvas && typeof canvas.getBoundingClientRect === 'function'
        ? canvas.getBoundingClientRect()
        : null
    const w = (rect && rect.width) || (canvas && canvas.clientWidth) || 640
    const h = (rect && rect.height) || (canvas && canvas.clientHeight) || 480
    view.w = w
    view.h = h
    view.dpr = Math.min((typeof window !== 'undefined' && window.devicePixelRatio) || 1, 2)
    view.pad = Math.max(18, Math.min(w, h) * 0.06)
    view.iw = Math.max(1, w - view.pad * 2)
    view.ih = Math.max(1, h - view.pad * 2)
    if (canvas) {
      canvas.width = Math.max(1, Math.round(w * view.dpr))
      canvas.height = Math.max(1, Math.round(h * view.dpr))
    }
  }

  // ── physics ──────────────────────────────────────────────────────────────
  function step(dt) {
    if (!playing || reduced || dt <= 0) return
    elapsed += dt

    const target = waypoints[targetIndex]
    if (target) {
      const dx = target.x - drone.x
      const dy = target.y - drone.y
      const dist = Math.hypot(dx, dy)
      const targetHeading = Math.atan2(dy, dx)
      let diff = targetHeading - drone.heading
      while (diff < -Math.PI) diff += TAU
      while (diff > Math.PI) diff -= TAU
      drone.heading += diff * Math.min(1, 9 * dt)

      if (dist > 0.006) {
        const stepLen = Math.min(drone.speed * dt, dist)
        drone.x += Math.cos(drone.heading) * stepLen
        drone.y += Math.sin(drone.heading) * stepLen
      } else {
        drone.x = target.x
        drone.y = target.y
        targetIndex = (targetIndex + 1) % waypoints.length
      }
    }

    drone.rotorAngle = (drone.rotorAngle + 22 * dt) % TAU
    drone.scanAngle = (drone.scanAngle + 1.6 * dt) % TAU

    telemetry.battery = Math.max(9, 92 - elapsed * 0.08)
    telemetry.heading = Math.round(((drone.heading * 180) / Math.PI + 360) % 360)
    telemetry.altitude = 118 + Math.sin(elapsed * 1.1) * 3
    telemetry.speed = 8.4
    telemetry.gsd = layer === 'thermal' ? 4.2 : layer === 'pointcloud' ? 1.4 : 2.1
    telemetry.latitude = Number((BASE_LAT + (0.5 - drone.y) * 0.004).toFixed(5))
    telemetry.longitude = Number((BASE_LNG + (drone.x - 0.5) * 0.004).toFixed(5))
    telemetry.status = STATUS_PLAYING
  }

  function refreshTelemetry() {
    telemetry.speed = playing ? 8.4 : 0
    telemetry.status = playing ? STATUS_PLAYING : STATUS_PAUSED
    telemetry.gsd = layer === 'thermal' ? 4.2 : layer === 'pointcloud' ? 1.4 : 2.1
  }

  function publishTelemetry(force = false) {
    const t = now()
    if (!force && t - lastPublish < 120) return
    lastPublish = t
    onTelemetry({ ...telemetry })
  }

  // ── draw helpers ─────────────────────────────────────────────────────────
  function drawBackground(g) {
    const { w, h, pad, iw, ih } = view

    if (layer === 'thermal') {
      g.fillStyle = '#0b0a1f'
      g.fillRect(0, 0, w, h)
      for (const area of AREAS_OF_INTEREST) {
        const [cx, cy] = project(area.x + area.w / 2, area.y + area.h / 2)
        const r = Math.max(48, Math.min(w, h) * 0.17)
        const grad = g.createRadialGradient(cx, cy, 2, cx, cy, r)
        grad.addColorStop(0, 'rgba(244, 63, 94, 0.5)')
        grad.addColorStop(0.45, 'rgba(217, 70, 239, 0.24)')
        grad.addColorStop(1, 'rgba(11, 10, 31, 0)')
        g.fillStyle = grad
        g.beginPath()
        g.arc(cx, cy, r, 0, TAU)
        g.fill()
      }
      return
    }

    if (layer === 'pointcloud') {
      g.fillStyle = '#020617'
      g.fillRect(0, 0, w, h)
      const stepPx = Math.max(10, Math.min(w, h) / 26)
      for (let x = 0; x <= w; x += stepPx) {
        for (let y = 0; y <= h; y += stepPx) {
          const elev = Math.sin(x * 0.02 + y * 0.018) + Math.cos(y * 0.03 - x * 0.01)
          const size = 1.4 + Math.max(0, elev) * 1.1
          g.fillStyle =
            elev > 0.6
              ? 'rgba(56, 189, 248, 0.85)'
              : elev > -0.4
                ? 'rgba(16, 185, 129, 0.55)'
                : 'rgba(6, 182, 212, 0.35)'
          g.fillRect(x + elev * 2, y + elev * 2, size, size)
        }
      }
      return
    }

    // optical orthophoto
    const grad = g.createLinearGradient(0, 0, w, h)
    grad.addColorStop(0, '#04101f')
    grad.addColorStop(1, '#020617')
    g.fillStyle = grad
    g.fillRect(0, 0, w, h)

    g.strokeStyle = 'rgba(56, 189, 248, 0.1)'
    g.lineWidth = 1
    const gridStep = Math.max(24, iw / 12)
    for (let x = pad; x <= w - pad + 0.5; x += gridStep) {
      g.beginPath()
      g.moveTo(x, pad)
      g.lineTo(x, h - pad)
      g.stroke()
    }
    for (let y = pad; y <= h - pad + 0.5; y += gridStep) {
      g.beginPath()
      g.moveTo(pad, y)
      g.lineTo(w - pad, y)
      g.stroke()
    }

    g.strokeStyle = 'rgba(34, 211, 238, 0.35)'
    g.lineWidth = 1.5
    g.strokeRect(pad, pad, iw, ih)
  }

  function drawAreas(g) {
    for (const area of AREAS_OF_INTEREST) {
      const isSelected = selected && selected.id === area.id
      const color = areaColor(area)
      const [x, y] = project(area.x, area.y)
      const w = area.w * view.iw
      const h = area.h * view.ih

      if (isSelected) {
        g.fillStyle = 'rgba(34, 211, 238, 0.1)'
        g.fillRect(x, y, w, h)
      }

      const corner = Math.min(12, w / 3, h / 3)
      g.strokeStyle = color
      g.lineWidth = isSelected ? 2.5 : 1.5
      g.beginPath()
      // top-left
      g.moveTo(x, y + corner)
      g.lineTo(x, y)
      g.lineTo(x + corner, y)
      // top-right
      g.moveTo(x + w - corner, y)
      g.lineTo(x + w, y)
      g.lineTo(x + w, y + corner)
      // bottom-right
      g.moveTo(x + w, y + h - corner)
      g.lineTo(x + w, y + h)
      g.lineTo(x + w - corner, y + h)
      // bottom-left
      g.moveTo(x + corner, y + h)
      g.lineTo(x, y + h)
      g.lineTo(x, y + h - corner)
      g.stroke()

      g.fillStyle = color
      g.beginPath()
      g.arc(x + w / 2, y + h / 2, 2, 0, TAU)
      g.fill()

      const tagW = Math.max(w, 124)
      g.fillStyle = 'rgba(2, 6, 23, 0.85)'
      g.fillRect(x, y - 18, tagW, 16)
      g.strokeStyle = color
      g.lineWidth = 1
      g.strokeRect(x, y - 18, tagW, 16)
      g.fillStyle = '#e2e8f0'
      g.font = '10px "JetBrains Mono", monospace'
      g.fillText(`${area.label} · ${area.confidence}%`, x + 4, y - 6)
    }
  }

  function drawRoute(g) {
    g.strokeStyle = 'rgba(34, 211, 238, 0.4)'
    g.lineWidth = 1.5
    g.setLineDash([6, 6])
    g.beginPath()
    waypoints.forEach((wp, i) => {
      const [sx, sy] = project(wp.x, wp.y)
      if (i === 0) g.moveTo(sx, sy)
      else g.lineTo(sx, sy)
    })
    if (waypoints.length > 2) g.closePath()
    g.stroke()
    g.setLineDash([])

    waypoints.forEach((wp, i) => {
      const isTarget = i === targetIndex
      const [sx, sy] = project(wp.x, wp.y)
      g.fillStyle = isTarget ? '#22d3ee' : 'rgba(56, 189, 248, 0.55)'
      g.beginPath()
      g.arc(sx, sy, isTarget ? 6 : 4, 0, TAU)
      g.fill()
      if (isTarget) {
        g.strokeStyle = 'rgba(34, 211, 238, 0.6)'
        g.lineWidth = 2
        g.beginPath()
        g.arc(sx, sy, 12, 0, TAU)
        g.stroke()
      }
      g.fillStyle = 'rgba(148, 163, 184, 0.75)'
      g.font = '9px "JetBrains Mono", monospace'
      g.fillText(`WP${i + 1}`, sx + 8, sy - 4)
    })
  }

  function drawDrone(g) {
    const [sx, sy] = project(drone.x, drone.y)
    const sc = Math.max(14, Math.min(view.iw, view.ih) * 0.05)

    const scan = g.createRadialGradient(sx, sy, sc * 0.3, sx, sy, sc * 6)
    if (layer === 'thermal') {
      scan.addColorStop(0, 'rgba(244, 63, 94, 0.32)')
      scan.addColorStop(0.7, 'rgba(245, 158, 11, 0.14)')
      scan.addColorStop(1, 'rgba(0, 0, 0, 0)')
    } else if (layer === 'pointcloud') {
      scan.addColorStop(0, 'rgba(16, 185, 129, 0.32)')
      scan.addColorStop(0.7, 'rgba(6, 182, 212, 0.16)')
      scan.addColorStop(1, 'rgba(0, 0, 0, 0)')
    } else {
      scan.addColorStop(0, 'rgba(34, 211, 238, 0.28)')
      scan.addColorStop(0.75, 'rgba(56, 189, 248, 0.08)')
      scan.addColorStop(1, 'rgba(0, 0, 0, 0)')
    }
    g.fillStyle = scan
    g.beginPath()
    g.arc(sx, sy, sc * 6, 0, TAU)
    g.fill()

    g.strokeStyle = 'rgba(34, 211, 238, 0.55)'
    g.lineWidth = 1.5
    g.beginPath()
    g.moveTo(sx, sy)
    g.arc(sx, sy, sc * 6, drone.scanAngle, drone.scanAngle + 0.6)
    g.closePath()
    g.stroke()

    g.fillStyle = 'rgba(2, 6, 23, 0.5)'
    g.beginPath()
    g.ellipse(sx, sy + sc * 0.5, sc * 0.9, sc * 0.34, 0, 0, TAU)
    g.fill()

    g.strokeStyle = '#334155'
    g.lineWidth = Math.max(2, sc * 0.16)
    g.beginPath()
    g.moveTo(sx - sc, sy - sc)
    g.lineTo(sx + sc, sy + sc)
    g.moveTo(sx + sc, sy - sc)
    g.lineTo(sx - sc, sy + sc)
    g.stroke()

    g.fillStyle = '#0f172a'
    g.strokeStyle = '#22d3ee'
    g.lineWidth = 1.5
    g.beginPath()
    g.moveTo(sx, sy - sc * 0.5)
    g.lineTo(sx + sc * 0.45, sy)
    g.lineTo(sx, sy + sc * 0.5)
    g.lineTo(sx - sc * 0.45, sy)
    g.closePath()
    g.fill()
    g.stroke()

    g.fillStyle = '#38bdf8'
    g.beginPath()
    g.arc(sx, sy, sc * 0.16, 0, TAU)
    g.fill()

    const rotors = [
      [-sc, -sc],
      [sc, -sc],
      [sc, sc],
      [-sc, sc],
    ]
    rotors.forEach(([ox, oy], i) => {
      const rx = sx + ox
      const ry = sy + oy
      g.fillStyle = '#64748b'
      g.beginPath()
      g.arc(rx, ry, sc * 0.16, 0, TAU)
      g.fill()

      g.strokeStyle = 'rgba(56, 189, 248, 0.5)'
      g.lineWidth = 1.5
      g.beginPath()
      g.arc(rx, ry, sc * 0.55, 0, TAU)
      g.stroke()

      const blade = drone.rotorAngle * (i % 2 === 0 ? 1 : -1)
      g.strokeStyle = 'rgba(226, 232, 240, 0.75)'
      g.lineWidth = 2
      g.beginPath()
      g.moveTo(rx - Math.cos(blade) * sc * 0.55, ry - Math.sin(blade) * sc * 0.55)
      g.lineTo(rx + Math.cos(blade) * sc * 0.55, ry + Math.sin(blade) * sc * 0.55)
      g.stroke()
    })
  }

  function render() {
    if (!ctx) return
    const { w, h } = view
    ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    drawBackground(ctx)
    drawAreas(ctx)
    drawRoute(ctx)
    drawDrone(ctx)
  }

  // ── rAF loop ─────────────────────────────────────────────────────────────
  function frame(ts) {
    if (!running) return
    const dt = Math.min(((ts - lastFrame) / 1000) || 0, 0.1)
    lastFrame = ts
    step(dt)
    render()
    publishTelemetry()
    rafId = raf(frame)
  }

  function start() {
    if (running || reduced) return
    running = true
    lastFrame = now()
    rafId = raf(frame)
  }

  function stop() {
    running = false
    if (rafId) {
      caf(rafId)
      rafId = 0
    }
  }

  // ── interactions ─────────────────────────────────────────────────────────
  function hitTest(sx, sy) {
    const [wx, wy] = unproject(sx, sy)
    const pad = 0.02
    for (const area of AREAS_OF_INTEREST) {
      if (
        wx >= area.x - pad &&
        wx <= area.x + area.w + pad &&
        wy >= area.y - pad &&
        wy <= area.y + area.h + pad
      ) {
        return area
      }
    }
    return null
  }

  function refresh() {
    refreshTelemetry()
    render()
    publishTelemetry(true)
  }

  function setLayer(id) {
    if (!LAYERS.some((l) => l.id === id) || id === layer) return layer
    layer = id
    const meta = LAYERS.find((l) => l.id === id)
    notice = `Layer: ${meta.label}`
    refresh()
    return layer
  }

  function togglePlay() {
    playing = !playing
    notice = playing ? 'Flight resumed' : 'Flight paused'
    refresh()
    return playing
  }

  function selectAt(sx, sy) {
    const area = hitTest(sx, sy)
    if (area) {
      selected = area
      notice = `Inspecting ${area.label} · ${area.metric}`
      refresh()
      return { type: 'area', area }
    }

    const [wx, wy] = unproject(sx, sy)
    const point = {
      x: clamp(wx, 0.04, 0.96),
      y: clamp(wy, 0.04, 0.96),
      label: `WP-${String(waypoints.length + 1).padStart(2, '0')} · User waypoint`,
    }
    waypoints = [...waypoints, point]
    targetIndex = waypoints.length - 1
    selected = null
    notice = `Rerouted · ${Math.round(point.x * 100)}, ${Math.round(point.y * 100)}`
    refresh()
    return { type: 'waypoint', point }
  }

  function reset() {
    waypoints = INITIAL_WAYPOINTS.map((wp) => ({ ...wp }))
    targetIndex = 1
    drone.x = INITIAL_WAYPOINTS[0].x
    drone.y = INITIAL_WAYPOINTS[0].y
    drone.heading = 0
    selected = { ...AREAS_OF_INTEREST[0] }
    playing = true
    notice = 'Flight plan reset'
    elapsed = 0
    telemetry.battery = 92
    refresh()
    return getState()
  }

  function getState() {
    return {
      layer,
      playing,
      reduced,
      running,
      notice,
      targetIndex,
      selected: selected ? { ...selected } : null,
      waypoints: waypoints.map((wp) => ({ ...wp })),
      drone: { ...drone },
      telemetry: { ...telemetry },
    }
  }

  function advance(dt) {
    step(dt)
    publishTelemetry(true)
    return getState()
  }

  return {
    init() {
      layout()
      render()
      start()
    },
    resize() {
      layout()
      render()
    },
    start,
    stop,
    destroy() {
      stop()
    },
    render,
    advance,
    setLayer,
    togglePlay,
    selectAt,
    hitTest,
    reset,
    getState,
    get layer() {
      return layer
    },
    get playing() {
      return playing
    },
    get running() {
      return running
    },
    get reduced() {
      return reduced
    },
    get view() {
      return { ...view }
    },
  }
}
