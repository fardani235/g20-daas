// Where the 3D view looks from.
//
// Five ways to watch the same flight. Two leave the camera to the user
// (`free`, and `follow`, which carries the user's orbit along with the
// aircraft); three attach it to the aircraft and are computed here from the
// sampled state alone, so they are as deterministic as the flight:
//
// * `top`    - straight down on the aircraft, north up;
// * `fpv`    - from the nose, banking with the aircraft;
// * `gimbal` - through the survey camera: its pitch, its field of view.
//
// Scene axes as in `sceneGeometry.js`: x east, y up, z south.

const RAD = Math.PI / 180

export const CAMERA_MODES = Object.freeze([
  { id: 'follow', label: 'Follow', hint: 'Orbit around the aircraft as it flies' },
  { id: 'free', label: 'Free', hint: 'Move the camera freely around the site' },
  { id: 'top', label: 'Top-down', hint: 'Straight down on the aircraft, north up' },
  { id: 'fpv', label: 'FPV', hint: 'From the nose of the aircraft' },
  { id: 'gimbal', label: 'Camera', hint: 'Through the survey camera, with its field of view' },
])

export const isAttached = mode => mode === 'top' || mode === 'fpv' || mode === 'gimbal'
export const isUserControlled = mode => mode === 'free' || mode === 'follow'

const FPV_TILT_DEG = -10
const FPV_FOV_DEG = 75
const TOP_FOV_DEG = 35

/** Scene position of the aircraft for a sampled state. */
export const aircraftPosition = state => [state.x, state.alt, -state.y]

/** Unit vectors of the aircraft's heading in the scene: forward and right along the ground. */
export function headingAxes(headingDeg) {
  const psi = headingDeg * RAD
  return { forward: [Math.sin(psi), 0, -Math.cos(psi)], right: [Math.cos(psi), 0, Math.sin(psi)] }
}

/**
 * Vertical field of view (degrees) that shows the whole photo in a viewport
 * of `viewAspect` (width / height), and the photo's frame as fractions of
 * the viewport: `{ fov, width, height }`.
 */
export function gimbalFraming(model, viewAspect, margin = 1.08) {
  const aspect = viewAspect > 0 ? viewAspect : 1
  const half = Math.max(model.tanAlong, model.tanAcross / aspect) * margin
  return {
    fov: 2 * Math.atan(half) / RAD,
    width: model.tanAcross / (half * aspect),
    height: model.tanAlong / half,
  }
}

/**
 * Camera pose for an attached mode: `{ position, target, up, fov }` in
 * scene axes. `opts.topHeight` is how far above the aircraft the top-down
 * view sits; `opts.viewAspect` the viewport's width / height.
 */
export function rigPose(mode, state, model, { topHeight = 300, viewAspect = 1.6 } = {}) {
  const p = aircraftPosition(state)
  const { forward, right } = headingAxes(state.heading)

  if (mode === 'top') {
    return { position: [p[0], p[1] + topHeight, p[2]], target: p, up: [0, 0, -1], fov: TOP_FOV_DEG }
  }

  // Look along `pitch` degrees from level, with the image's "up" at right angles to it.
  const look = pitchDeg => {
    const c = Math.cos(pitchDeg * RAD)
    const s = Math.sin(pitchDeg * RAD)
    return {
      dir: [forward[0] * c, s, forward[2] * c],
      up: [-forward[0] * s, c, -forward[2] * s],
    }
  }

  if (mode === 'gimbal') {
    const pitch = model ? model.pitch : -90
    const { dir, up } = look(pitch)
    const position = [p[0], p[1] - 0.2, p[2]]
    return {
      position,
      target: [position[0] + dir[0], position[1] + dir[1], position[2] + dir[2]],
      up,
      fov: model ? gimbalFraming(model, viewAspect).fov : 60,
    }
  }

  // FPV: a little below level, the horizon rolling with the aircraft.
  const { dir, up } = look(FPV_TILT_DEG)
  const roll = (state.roll || 0) * RAD
  const cr = Math.cos(roll)
  const sr = Math.sin(roll)
  const position = [p[0] + forward[0] * 0.4, p[1] + 0.1, p[2] + forward[2] * 0.4]
  return {
    position,
    target: [position[0] + dir[0], position[1] + dir[1], position[2] + dir[2]],
    up: [up[0] * cr + right[0] * sr, up[1] * cr + right[1] * sr, up[2] * cr + right[2] * sr],
    fov: FPV_FOV_DEG,
  }
}

/** Where the follow camera starts relative to the aircraft: behind its first heading, above, at a distance that suits the site. */
export function followOffset(headingDeg, sceneRadius) {
  const { forward } = headingAxes(headingDeg)
  const distance = Math.min(Math.max(sceneRadius * 0.35, 80), 600)
  return [-forward[0] * distance, distance * 0.55, -forward[2] * distance]
}

/**
 * Scale of the aircraft model so it stays visible: life size up close,
 * growing with the camera's distance so it is never smaller than a few
 * pixels across. The readouts, not the model, say how big things are.
 */
export function aircraftScale(distance, { apparent = 0.045, min = 1, max = 900 } = {}) {
  return Math.min(Math.max(distance * apparent, min), max)
}

/** Top-down height that keeps the aircraft's surroundings and its footprint in view. */
export function defaultTopHeight(state, footprintAcross = 0) {
  return Math.min(Math.max(250, (footprintAcross || 0) * 3, (state?.aglAssumed || 0) * 2.5), 4000)
}
