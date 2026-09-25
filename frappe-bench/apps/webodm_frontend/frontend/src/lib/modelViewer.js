// Pure helpers for the 3D model viewer (pages/ModelView.vue via
// composables/useModelViewer.js). Everything here is plain math and data
// mapping with no three.js scene or DOM dependency, so it is unit-tested in
// jsdom without WebGL. Vectors are `{x, y, z}` plain objects; the composable
// copies them into THREE.Vector3 at the boundary.

import { MOUSE, TOUCH } from 'three'

// ---------------------------------------------------------------------------
// Interaction modes: which action the primary mouse button / one finger does.
// The remaining buttons keep their conventional roles so a user who knows
// right-drag = pan is never surprised.
// ---------------------------------------------------------------------------

export const MODES = ['rotate', 'pan', 'zoom']

export const MODE_LABELS = {
  rotate: 'Rotate',
  pan: 'Pan',
  zoom: 'Zoom',
}

// Short hint shown under the canvas; changes with the active mode.
export const MODE_HINTS = {
  rotate: 'Drag to rotate · Right-drag to pan · Scroll to zoom · Double-click to focus',
  pan: 'Drag to pan · Right-drag to rotate · Scroll to zoom · Double-click to focus',
  zoom: 'Drag up/down to zoom · Right-drag to pan · Scroll to zoom · Double-click to focus',
}

// Touch devices get their own wording (no right button or scroll wheel).
export const TOUCH_HINTS = {
  rotate: 'One finger to rotate · Two fingers to zoom and pan',
  pan: 'One finger to pan · Two fingers to zoom and rotate',
  zoom: 'Pinch to zoom · One finger to rotate',
}

export function hintFor(mode, coarsePointer = false) {
  const table = coarsePointer ? TOUCH_HINTS : MODE_HINTS
  return table[mode] || table.rotate
}

export function mouseButtonsFor(mode) {
  switch (mode) {
    case 'pan':
      return { LEFT: MOUSE.PAN, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.ROTATE }
    case 'zoom':
      return { LEFT: MOUSE.DOLLY, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN }
    case 'rotate':
    default:
      return { LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN }
  }
}

export function touchesFor(mode) {
  switch (mode) {
    case 'pan':
      return { ONE: TOUCH.PAN, TWO: TOUCH.DOLLY_ROTATE }
    case 'zoom':
    case 'rotate':
    default:
      return { ONE: TOUCH.ROTATE, TWO: TOUCH.DOLLY_PAN }
  }
}

// ---------------------------------------------------------------------------
// Camera framing. The scene is Y-up after the model is re-oriented, so "top"
// looks down -Y and the ground plane is XZ.
// ---------------------------------------------------------------------------

export const VIEW_PRESETS = ['iso', 'top', 'north', 'east']

export const VIEW_LABELS = {
  iso: 'Isometric',
  top: 'Top-down',
  north: 'Facing north',
  east: 'Facing east',
}

const FIT_PADDING = 1.15

/**
 * Distance from the bounding-box center at which the whole model fits the
 * viewport, plus the depth range and orbit limits derived from it.
 *
 * `size` is the bounding-box size (Y-up), `aspect` the viewport aspect ratio,
 * `fovDeg` the vertical field of view. The fit uses the bounding sphere so the
 * result is orientation-independent: any preset framed at this distance shows
 * the whole model.
 */
export function framingFor(size, aspect, fovDeg = 50) {
  const sx = Math.abs(size?.x || 0), sy = Math.abs(size?.y || 0), sz = Math.abs(size?.z || 0)
  const radius = Math.max(Math.sqrt(sx * sx + sy * sy + sz * sz) / 2, 1e-3)
  const vFov = (fovDeg * Math.PI) / 180
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * Math.max(aspect || 1, 1e-3))
  const fov = Math.min(vFov, hFov)
  const distance = (radius / Math.sin(fov / 2)) * FIT_PADDING
  return {
    radius,
    distance,
    near: Math.max(distance / 1000, 1e-4),
    far: distance * 100,
    minDistance: radius * 0.02,
    maxDistance: distance * 8,
  }
}

// Unit direction from the model centre to the camera for each preset. The iso
// view is the default "hero" angle: from the south-west, elevated. Tiny Z
// offset on "top" keeps the up-vector well defined for OrbitControls. "north"
// sits south of the model (Z+ after the Z-up -> Y-up rotation) and looks
// north; "east" sits west and looks east.
const PRESET_DIRECTIONS = {
  iso: normalize({ x: 0.58, y: 0.5, z: 0.64 }),
  top: normalize({ x: 0, y: 1, z: 1e-3 }),
  north: normalize({ x: 0, y: 0.35, z: 0.94 }),
  east: normalize({ x: -0.94, y: 0.35, z: 0 }),
}

function normalize(v) {
  const n = Math.hypot(v.x, v.y, v.z) || 1
  return { x: v.x / n, y: v.y / n, z: v.z / n }
}

/** Camera position for a named preset, orbiting `center` at `distance`. */
export function presetPosition(preset, center, distance) {
  const c = { x: center?.x || 0, y: center?.y || 0, z: center?.z || 0 }
  const d = PRESET_DIRECTIONS[preset] || PRESET_DIRECTIONS.iso
  return { x: c.x + d.x * distance, y: c.y + d.y * distance, z: c.z + d.z * distance }
}

/** Multiplicative dolly with the orbit limits respected. `factor < 1` zooms in. */
export function dollyDistance(current, factor, minDistance, maxDistance) {
  const next = current * factor
  return Math.min(Math.max(next, minDistance), maxDistance)
}

/**
 * Screen-space pan step as a fraction of the camera distance, so keyboard
 * panning covers the same on-screen distance regardless of model scale.
 * `dx`/`dy` are in [-1, 1] units (right/up positive).
 */
export function panStep(distance, dx, dy, fraction = 0.08) {
  return { right: dx * distance * fraction, up: dy * distance * fraction }
}

// ---------------------------------------------------------------------------
// Model orientation and placement.
// ---------------------------------------------------------------------------

// ODM writes its textured GLB (odm_textured_model_geo.glb) in the projected
// CRS: X east, Y north, Z up. glTF/three.js are Y-up, so the model is rotated
// -90° about X to stand the right way up. The rotation is applied to a wrapper
// group, never to vertex data.
export const Z_UP_TO_Y_UP_X_ROTATION = -Math.PI / 2

// Above this magnitude (metres) bounding-box centres are treated as projected
// coordinates (UTM eastings/northings are 10^5-10^6). Float32 GPU math on
// such offsets jitters, so the offset is baked into the vertex data once, on
// the CPU in float64. Models with a CESIUM_RTC centre (what ODM emits) are
// already local and take the cheap group-offset path.
export const LARGE_COORD_THRESHOLD = 1e4

export function needsVertexRecenter(center) {
  return ['x', 'y', 'z'].some(k => Math.abs(center?.[k] || 0) > LARGE_COORD_THRESHOLD)
}

/** Grid extent: a "nice" number at least 1.5x the model footprint. */
export function gridSizeFor(footprint) {
  const target = Math.max(footprint || 0, 1) * 1.5
  const mag = Math.pow(10, Math.floor(Math.log10(target)))
  for (const m of [1, 2, 5, 10]) {
    if (m * mag >= target) return m * mag
  }
  return 10 * mag
}

// Very dense meshes are rendered at a lower device pixel ratio so orbiting
// stays fluid on laptops; the cap is relaxed as the mesh gets lighter.
export function pixelRatioFor(devicePixelRatio, triangles) {
  const dpr = Math.max(devicePixelRatio || 1, 1)
  if (triangles > 4_000_000) return Math.min(dpr, 1)
  if (triangles > 1_500_000) return Math.min(dpr, 1.5)
  return Math.min(dpr, 2)
}

// ---------------------------------------------------------------------------
// Package handling for the model.zip fallback (glTF + external buffers).
// ---------------------------------------------------------------------------

/** Prefer a .glb, then a .gltf, among zip member names; null when neither. */
export function pickModelEntry(names) {
  const list = (names || []).filter(n => n && !n.endsWith('/'))
  const glb = list.find(n => n.toLowerCase().endsWith('.glb'))
  if (glb) return glb
  return list.find(n => n.toLowerCase().endsWith('.gltf')) || null
}

/** Resolve a glTF-relative URI against the zip member map (handles ./ and %20). */
export function resolveZipUri(uri, files, baseDir = '') {
  if (!uri) return null
  let key = uri.replace(/^\.\//, '')
  try { key = decodeURIComponent(key) } catch {}
  return files[baseDir + key] || files[key] || null
}

// ---------------------------------------------------------------------------
// Keyboard map. Returns an action name or null; the composable performs it.
// ---------------------------------------------------------------------------

export function keyAction(event) {
  if (!event || event.ctrlKey || event.metaKey || event.altKey) return null
  const tag = event.target?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || event.target?.isContentEditable) return null
  switch (event.key) {
    case 'ArrowLeft': case 'a': case 'A': return 'panLeft'
    case 'ArrowRight': case 'd': case 'D': return 'panRight'
    case 'ArrowUp': case 'w': case 'W': return 'panUp'
    case 'ArrowDown': case 's': case 'S': return 'panDown'
    case '+': case '=': return 'zoomIn'
    case '-': case '_': return 'zoomOut'
    case 'r': case 'R': case 'Home': return 'reset'
    case '1': return 'viewIso'
    case '2': return 'viewTop'
    case '3': return 'viewNorth'
    case '4': return 'viewEast'
    case 'g': case 'G': return 'toggleGrid'
    case 'f': case 'F': return 'toggleFullscreen'
    case '?': case 'h': case 'H': return 'toggleHelp'
    case 'Escape': return 'escape'
    default: return null
  }
}

// ---------------------------------------------------------------------------
// Page state helpers.
// ---------------------------------------------------------------------------

export const PROCESSING_STATUSES = ['Pending', 'Queued', 'Provisioning', 'Running']

/**
 * What to show when a task has no model to display. Returns null when the
 * task does have a model (the viewer should load it).
 */
// Which GLB the viewer shows. A `?run=<name>` query selects a plugin run's
// model output (e.g. the 3D Reconstruction plugin) instead of the task's own
// ODM model; both are private file URLs the viewer fetches the same way.
export function modelSourceFor(task, runs, runName) {
  if (runName) {
    const run = (runs || []).find(r => r.name === runName)
    if (run && run.output_kind === 'model' && run.status === 'Completed' && run.output_file) {
      return { kind: 'run', url: run.output_file, run }
    }
    return { kind: 'run-missing', url: null, run: run || null }
  }
  return task?.model ? { kind: 'task', url: task.model, run: null } : { kind: 'none', url: null, run: null }
}

// Entries for the model switcher: the project's tasks with an ODM model plus
// this task's completed reconstruction runs. Values are `task:<name>` /
// `run:<name>` so one Select can route to either.
export function modelChoices(datasets, runs, taskName, labelFor = id => id) {
  const out = (datasets || []).map(d => ({
    value: `task:${d.name}`, label: d.title || d.name, kind: 'task',
  }))
  for (const run of runs || []) {
    if (run.output_kind !== 'model' || run.status !== 'Completed' || !run.output_file) continue
    if (taskName && run.task && run.task !== taskName) continue
    const wf = run.output_metadata?.workflow
    out.push({ value: `run:${run.name}`, kind: 'run', label: `${labelFor(run.plugin)}${wf ? ` (${wf})` : ''}` })
  }
  return out
}

export function emptyStateFor(task, source = null) {
  if (!task) {
    return { kind: 'missing', title: 'Task not found', detail: 'This task does not exist or you do not have access to it.' }
  }
  if (source?.kind === 'run-missing') {
    const status = source.run?.status
    return {
      kind: 'none',
      title: 'Reconstruction not available',
      detail: status && status !== 'Completed'
        ? `This 3D reconstruction run is ${status.toLowerCase()}. Its model appears here once it completes.`
        : 'This 3D reconstruction run no longer has a model (it may have been replaced by a newer run).',
    }
  }
  if (source ? source.url : task.model) return null
  if (PROCESSING_STATUSES.includes(task.status)) {
    return {
      kind: 'processing',
      title: 'Processing in progress',
      detail: 'The 3D model appears here automatically when processing completes.',
    }
  }
  if (task.status === 'Failed' || task.status === 'Cancelled') {
    return {
      kind: 'failed',
      title: `Task ${task.status.toLowerCase()}`,
      detail: 'No 3D model was produced. Check the console for details, then restart processing.',
    }
  }
  return {
    kind: 'none',
    title: 'No 3D model for this task',
    detail: 'Processing finished without a textured model. Re-run without the "skip-3dmodel" option to generate one.',
  }
}

export function formatBytes(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n < 0) return ''
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}

export function formatCount(n) {
  const v = Number(n) || 0
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(v >= 1e5 ? 0 : 1)}k`
  return String(v)
}

/** Download progress as 0-100, or null when the total is unknown. */
export function progressPercent(loaded, total) {
  if (!total || total <= 0) return null
  return Math.max(0, Math.min(100, Math.round((loaded / total) * 100)))
}
