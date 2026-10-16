// Geodesy for the flight simulator.
//
// A mission is planned on the server in a local transverse Mercator frame
// centred on the site (`webodm_core/flightplan/geo.py`) and carries that
// frame's origin as `mission.frame`. The simulator moves the aircraft in the
// same frame - metres east and north of the origin - and converts back to
// WGS84 for the map, so this is a line-for-line port of `geo.LocalFrame`
// (Snyder, *Map Projections: A Working Manual*, eqs. 8-9 ... 8-25). The
// tests check it against values produced by the Python original.

const A = 6378137.0
const F = 1 / 298.257223563
const E2 = F * (2 - F)
const EP2 = E2 / (1 - E2)
const E4 = E2 * E2
const E6 = E4 * E2
const M1 = 1 - E2 / 4 - 3 * E4 / 64 - 5 * E6 / 256
const M2 = 3 * E2 / 8 + 3 * E4 / 32 + 45 * E6 / 1024
const M3 = 15 * E4 / 256 + 45 * E6 / 1024
const M4 = 35 * E6 / 3072
const E1 = (1 - Math.sqrt(1 - E2)) / (1 + Math.sqrt(1 - E2))

const RAD = Math.PI / 180
const DEG = 180 / Math.PI

function meridianArc(phi) {
  return A * (M1 * phi - M2 * Math.sin(2 * phi) + M3 * Math.sin(4 * phi) - M4 * Math.sin(6 * phi))
}

/** Longitude wrapped to [-180, 180). */
export function normalizeLng(lng) {
  return ((((lng + 180) % 360) + 360) % 360) - 180
}

/** Transverse Mercator frame centred on `(lat0, lng0)`; x east, y north, metres. */
export class LocalFrame {
  constructor(lat0, lng0) {
    if (!(lat0 >= -89 && lat0 <= 89)) throw new RangeError('a local frame needs an origin outside 1° of the poles')
    this.lat0 = Number(lat0)
    this.lng0 = normalizeLng(Number(lng0))
    this.m0 = meridianArc(this.lat0 * RAD)
  }

  /** The frame a mission was planned in. */
  static ofMission(mission) {
    const f = mission?.frame
    if (!f || !Number.isFinite(f.lat0) || !Number.isFinite(f.lng0)) throw new TypeError('the mission carries no local frame')
    return new LocalFrame(f.lat0, f.lng0)
  }

  /** `[x, y]` metres of a longitude / latitude. */
  forward(lng, lat) {
    const phi = lat * RAD
    const dlam = normalizeLng(lng - this.lng0) * RAD
    const s = Math.sin(phi)
    const c = Math.cos(phi)
    const t = Math.tan(phi)
    const n = A / Math.sqrt(1 - E2 * s * s)
    const t2 = t * t
    const cc = EP2 * c * c
    const a = dlam * c
    const a2 = a * a
    const x = n * (a + (1 - t2 + cc) * a2 * a / 6
      + (5 - 18 * t2 + t2 * t2 + 72 * cc - 58 * EP2) * a2 * a2 * a / 120)
    const y = meridianArc(phi) - this.m0
      + n * t * (a2 / 2 + (5 - t2 + 9 * cc + 4 * cc * cc) * a2 * a2 / 24
        + (61 - 58 * t2 + t2 * t2 + 600 * cc - 330 * EP2) * a2 * a2 * a2 / 720)
    return [x, y]
  }

  /** `[lng, lat]` of a point `x` metres east and `y` metres north of the origin. */
  inverse(x, y) {
    const mu = (this.m0 + y) / (A * M1)
    const e1 = E1
    const phi1 = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu)
      + (21 * e1 * e1 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
      + (151 * e1 ** 3 / 96) * Math.sin(6 * mu)
      + (1097 * e1 ** 4 / 512) * Math.sin(8 * mu)
    const s = Math.sin(phi1)
    const c = Math.cos(phi1)
    const t = Math.tan(phi1)
    const c1 = EP2 * c * c
    const t1 = t * t
    const w = 1 - E2 * s * s
    const n1 = A / Math.sqrt(w)
    const r1 = A * (1 - E2) / (w ** 1.5)
    const d = x / n1
    const d2 = d * d
    const phi = phi1 - (n1 * t / r1) * (
      d2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * EP2) * d2 * d2 / 24
      + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * EP2 - 3 * c1 * c1) * d2 * d2 * d2 / 720)
    const lam = (d - (1 + 2 * t1 + c1) * d2 * d / 6
      + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * EP2 + 24 * t1 * t1) * d2 * d2 * d / 120) / c
    return [normalizeLng(this.lng0 + lam * DEG), phi * DEG]
  }
}

// -- angles ----------------------------------------------------------------------

/** Compass bearing (0 = north, clockwise, degrees in [0, 360)) of a local-frame vector. */
export function bearingDeg(dx, dy) {
  return ((Math.atan2(dx, dy) * DEG) % 360 + 360) % 360
}

/** Signed smallest rotation from bearing `a` to bearing `b`, in (-180, 180]. */
export function angleDiff(a, b) {
  let d = (b - a) % 360
  if (d > 180) d -= 360
  if (d <= -180) d += 360
  return d
}

/** Bearing a fraction `f` of the way from `a` to `b`, the short way round. */
export function lerpAngle(a, b, f) {
  return (((a + angleDiff(a, b) * f) % 360) + 360) % 360
}

// -- web mercator (the map's and the imagery's projection) ----------------------------

const MAX_LAT = 85.0511287798066

/** Web-Mercator Y in "world" units: 0 at the north edge, 1 at the south edge. */
export function mercatorY(lat) {
  const phi = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * RAD
  return 0.5 - Math.log(Math.tan(Math.PI / 4 + phi / 2)) / (2 * Math.PI)
}

/** Web-Mercator X in world units: 0 at 180° W, 1 at 180° E. */
export function mercatorX(lng) {
  return (lng + 180) / 360
}

/** Latitude of a Web-Mercator world Y. */
export function latOfMercatorY(y) {
  return (2 * Math.atan(Math.exp((0.5 - y) * 2 * Math.PI)) - Math.PI / 2) * DEG
}
