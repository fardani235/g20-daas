// What the camera sees: field of view and ground footprint.
//
// The planner derives line and photo spacing from a nadir footprint at the
// planned height (`webodm_core/flightplan/photogrammetry.py`). The simulator
// uses the same similar-triangles geometry at the height the aircraft is
// actually simulated at - so the footprint shrinks over rising ground and
// the overlap with it - and tilts it by the plan's gimbal pitch.
//
// The ground under a photo is taken as a horizontal plane at the elevation
// below the aircraft. For a nadir photo over gentle terrain that is the
// planner's own assumption; for an oblique photo over steep ground it is a
// simplification, and the dashboard says coverage is an estimate.

const RAD = Math.PI / 180

/** Simulated seconds for which both views light the footprint up after an exposure. */
export const SHUTTER_FLASH_S = 0.35

// A ray this close to the horizon never reaches the ground in a useful
// distance; its corner is drawn this many flying heights away instead.
const MAX_RANGE_HEIGHTS = 6

/**
 * Camera geometry of a mission: half-angle tangents across and along the
 * flight line, the gimbal pitch and the planner's nominal figures.
 * Returns null when the mission carries no usable camera.
 */
export function cameraModel(mission) {
  const cam = mission?.equipment?.camera
  const plan = mission?.camera || {}
  const w = Number(cam?.sensor_width_mm)
  const h = Number(cam?.sensor_height_mm)
  const f = Number(cam?.focal_length_mm)
  if (!(w > 0) || !(h > 0) || !(f > 0)) return null
  const orientation = plan.orientation === 'portrait' ? 'portrait' : 'landscape'
  const long = Math.max(w, h)
  const short = Math.min(w, h)
  // Landscape puts the long side of the sensor across the flight line.
  const across = orientation === 'portrait' ? short : long
  const along = orientation === 'portrait' ? long : short
  const pitch = Number.isFinite(Number(plan.gimbal_pitch_deg)) ? Number(plan.gimbal_pitch_deg) : -90
  return {
    label: cam.label || cam.name || 'Camera',
    orientation,
    pitch: Math.max(-90, Math.min(0, pitch)),
    tanAcross: across / (2 * f),
    tanAlong: along / (2 * f),
    fovAcrossDeg: 2 * Math.atan(across / (2 * f)) / RAD,
    fovAlongDeg: 2 * Math.atan(along / (2 * f)) / RAD,
    aspect: across / along,
    // Metres per pixel per metre of height above ground (photogrammetry.gsd_m).
    gsdPerMetre: Number(cam.image_width_px) > 0 ? w / (f * Number(cam.image_width_px)) : null,
    photoSpacing: Number(plan.photo_spacing_m) || 0,
    lineSpacing: Number(plan.line_spacing_m) || 0,
    plannedHeight: Number(plan.altitude_m) || 0,
    nadir: pitch <= -89.5,
  }
}

/** Nadir footprint and resolution from `agl` metres above ground: `{ across, along, gsdCm }`. */
export function footprintSize(model, agl) {
  const h = Math.max(0, agl)
  return {
    across: 2 * h * model.tanAcross,
    along: 2 * h * model.tanAlong,
    gsdCm: model.gsdPerMetre === null ? null : model.gsdPerMetre * h * 100,
  }
}

/**
 * Overlap actually achieved at `agl` with the plan's fixed spacings
 * (`photogrammetry.overlap_at`): `{ front, side }` in percent. The footprint
 * scales with the height above ground, the spacing does not, so rising
 * ground eats overlap; a negative value is a gap.
 */
export function overlapAt(model, agl) {
  const size = footprintSize(model, agl)
  const pct = (base, extent) => (extent > 0 ? (1 - base / extent) * 100 : -100)
  return { front: pct(model.photoSpacing, size.along), side: pct(model.lineSpacing, size.across) }
}

/**
 * Ground footprint of a photo taken at `(x, y)`, `agl` metres above the
 * ground, flying on bearing `heading`: the four corners in the local frame
 * as `[x0, y0, ... x3, y3]` - rear-left, rear-right, front-right,
 * front-left, so the ring is convex and consistently wound.
 *
 * `out` (a length-8 array) is filled and returned when given.
 */
export function footprintCorners(model, x, y, agl, heading, out = new Float64Array(8)) {
  const h = Math.max(agl, 0.01)
  const psi = heading * RAD
  const fx = Math.sin(psi)          // forward on the ground
  const fy = Math.cos(psi)
  const rx = fy                     // to the right of it
  const ry = -fx
  const p = model.pitch * RAD
  const cp = Math.cos(p)
  const sp = Math.sin(p)
  // Boresight `b` and the image's up axis `u`, as (forward, up) components.
  // Straight down: b = (0, -1), u = (1, 0) - the top of the image points ahead.
  const bf = cp
  const bu = sp
  const uf = -sp
  const uu = cp
  const reach = h * MAX_RANGE_HEIGHTS
  let k = 0
  for (const [sa, sl] of CORNERS) {
    const along = sl * model.tanAlong
    const across = sa * model.tanAcross
    const df = bf + along * uf       // forward component of the ray
    const du = bu + along * uu       // upward component
    let forward
    let right
    if (du < -1e-6) {
      const t = h / -du
      forward = df * t
      right = across * t
      const d = Math.hypot(forward, right)
      if (d > reach) { forward *= reach / d; right *= reach / d }
    } else {
      // At or above the horizon: as far as the footprint is drawn.
      const d = Math.hypot(df, across) || 1
      forward = df / d * reach
      right = across / d * reach
    }
    out[k++] = x + fx * forward + rx * right
    out[k++] = y + fy * forward + ry * right
  }
  return out
}

// (across sign, along sign): rear-left, rear-right, front-right, front-left.
const CORNERS = [[-1, -1], [1, -1], [1, 1], [-1, 1]]

/** Where the camera's boresight meets the ground, as `[x, y]`; the nadir point for a camera looking down. */
export function boresightGround(model, x, y, agl, heading) {
  if (model.nadir) return [x, y]
  const p = model.pitch * RAD
  const sp = Math.sin(p)
  if (sp > -1e-3) return [x, y]
  const forward = Math.min(Math.max(agl, 0) * Math.cos(p) / -sp, Math.max(agl, 0) * MAX_RANGE_HEIGHTS)
  return [x + Math.sin(heading * RAD) * forward, y + Math.cos(heading * RAD) * forward]
}

/**
 * Height above ground at every exposure, as a `Float32Array`. Where the
 * ground is unknown the plan's own assumption is used: flat ground at the
 * takeoff height. Returns `{ agl, known, min, max }`.
 */
export function photoHeights(trajectory) {
  const { photos, takeoff } = trajectory
  const agl = new Float32Array(photos.count)
  let known = 0
  let min = Infinity
  let max = -Infinity
  for (let i = 0; i < photos.count; i++) {
    const ground = takeoff.ground === null ? null : trajectory.groundAt(photos.x[i], photos.y[i])
    let h = photos.alt[i]
    if (ground !== null) { h = takeoff.ground + photos.alt[i] - ground; known++ }
    agl[i] = h
    if (h < min) min = h
    if (h > max) max = h
  }
  return { agl, known, min: photos.count ? min : null, max: photos.count ? max : null }
}
