// Words and numbers for the simulator's dashboard.
//
// Pure presentation: a sampled state in, labelled rows out. The rule the
// planner follows applies here too - an estimate must read like one, and a
// figure the simulation does not have is shown as missing, never as zero.

import { PHASE, PHASE_INFO } from './trajectory'

/** Colour of each phase on the timeline, by phase key. Survey is the planner's flight-line amber. */
export const PHASE_COLORS = Object.freeze({
  preflight: '#94a3b8',
  takeoff: '#e2e8f0',
  transit_out: '#a78bfa',
  survey: '#f59e0b',
  turn: '#64748b',
  hover: '#38bdf8',
  return: '#a78bfa',
  landing: '#e2e8f0',
  landed: '#94a3b8',
  battery: '#f43f5e',
})

/** What the timeline's colours mean, in the order they are met. */
export const TIMELINE_LEGEND = Object.freeze([
  { key: 'survey', label: 'Survey lines' },
  { key: 'turn', label: 'Turns' },
  { key: 'transit_out', label: 'Transit and return' },
  { key: 'takeoff', label: 'Takeoff and landing' },
  { key: 'battery', label: 'Battery change' },
])

/** `m:ss`, or `h:mm:ss` from an hour on. */
export function formatClock(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

export function formatDistance(metres) {
  const m = Number(metres)
  if (!Number.isFinite(m)) return '—'
  return Math.abs(m) < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(m < 10000 ? 2 : 1)} km`
}

const fixed = (v, digits = 0) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(digits))
const signed = (v, digits = 1) => {
  if (!Number.isFinite(v)) return '—'
  const r = Number(v.toFixed(digits))
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(digits)}`
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
export const compassPoint = deg => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8]

/** "Flight 2 of 3" and "Survey line 5 of 24" for a state. */
export function phaseText(state, trajectory) {
  const flights = trajectory.flights.length
  const info = PHASE_INFO[state.phase] || PHASE_INFO[0]
  let title = info.label
  if (state.phase === PHASE.SURVEY && state.line >= 0) {
    const lines = trajectory.mission.stats?.line_count
    title = `Survey line ${state.line + 1}${lines ? ` of ${lines}` : ''}`
  } else if (state.phase === PHASE.BATTERY) {
    title = `Battery change before flight ${state.flight + 2}`
  }
  return {
    key: info.key,
    title,
    flight: flights > 1 ? `Flight ${state.flight + 1} of ${flights}` : '',
  }
}

/**
 * The dashboard's tiles for a state: `[{ key, label, value, detail, tone }]`.
 * `tone` is `'warn'` where the figure deserves a second look.
 */
export function dashboardTiles(state, simulation) {
  const { trajectory, coverage } = simulation
  const duration = trajectory.duration
  const flight = trajectory.flights[state.flight]
  const phase = phaseText(state, trajectory)
  const reserve = trajectory.battery.reservePercent
  const tiles = [
    {
      key: 'phase', label: 'Now', value: phase.title,
      detail: phase.flight || (state.airborne ? 'In the air' : 'On the ground'),
    },
    {
      key: 'progress', label: 'Progress', value: `${fixed(duration > 0 ? (state.t / duration) * 100 : 0)} %`,
      detail: `${formatClock(state.t)} of ${formatClock(duration)}`,
    },
    {
      key: 'distance', label: 'Distance flown', value: formatDistance(state.distance),
      detail: `of ${formatDistance(trajectory.distance)}`,
    },
    {
      key: 'speed', label: 'Ground speed', value: `${fixed(state.speed, 1)} m/s`,
      detail: Math.abs(state.verticalSpeed) > 0.05 ? `${signed(state.verticalSpeed)} m/s vertical` : 'level',
    },
    {
      key: 'altitude', label: 'Height above takeoff', value: `${fixed(state.alt)} m`,
      detail: state.amsl !== null ? `about ${fixed(state.amsl)} m elevation` : 'elevation not known',
    },
    state.agl !== null
      ? {
        key: 'clearance', label: 'Above ground', value: `${fixed(state.agl)} m`, detail: 'estimate, from the elevation model',
        tone: state.airborne && state.phase !== PHASE.TAKEOFF && state.phase !== PHASE.LANDING
          && state.agl < Number(trajectory.mission.params?.min_clearance_m ?? 0) ? 'warn' : '',
      }
      : { key: 'clearance', label: 'Above ground', value: 'not known', detail: 'no elevation data here' },
    state.battery !== null
      ? {
        key: 'battery', label: 'Battery', value: `${fixed(state.battery)} %`,
        detail: state.belowReserve ? `into the ${fixed(reserve)} % reserve` : `estimate; ${fixed(reserve)} % kept in reserve`,
        tone: state.belowReserve ? 'warn' : '',
        meter: state.battery / 100,
        mark: reserve / 100,
      }
      : { key: 'battery', label: 'Battery', value: 'not known', detail: 'no rated flight time' },
    {
      key: 'photos', label: 'Photos', value: `${state.photos.toLocaleString('en-US')}`,
      detail: `of ${trajectory.photos.count.toLocaleString('en-US')}${state.capturing ? ' · camera on' : ''}`,
    },
    coverage
      ? {
        key: 'coverage', label: 'Area covered', value: `${fixed((state.coverage || 0) * 100)} %`,
        detail: coverage.scoped ? 'estimate, of the survey area' : 'estimate',
      }
      : { key: 'coverage', label: 'Area covered', value: '—', detail: 'no camera data' },
    {
      key: 'attitude', label: 'Heading', value: `${String(Math.round(state.heading) % 360).padStart(3, '0')}° ${compassPoint(state.heading)}`,
      detail: `pitch ${signed(state.pitch, 0)}°, roll ${signed(state.roll, 0)}°`,
    },
  ]
  if (flight && trajectory.flights.length > 1) {
    tiles.splice(2, 0, {
      key: 'flight', label: 'This flight', value: formatClock(state.flightTime),
      detail: `of ${formatClock(flight.duration)}`,
    })
  }
  return tiles
}

/** Simulation totals next to the plan's estimates: `[{ label, simulated, planned, note }]`. */
export function comparisonRows(simulation) {
  const s = simulation.summary
  const rows = [
    { label: 'Flight time', simulated: formatClock(s.flightTime), planned: s.planned.flightTime === null ? '—' : formatClock(s.planned.flightTime) },
    { label: 'Distance', simulated: formatDistance(s.distance), planned: s.planned.distance === null ? '—' : formatDistance(s.planned.distance) },
    { label: 'Photos', simulated: s.photos.toLocaleString('en-US'), planned: s.planned.photos === null ? '—' : Number(s.planned.photos).toLocaleString('en-US') },
    { label: 'Batteries', simulated: String(s.flights), planned: s.planned.flights === null ? '—' : String(s.planned.flights) },
  ]
  if (s.lowestClearance !== null) {
    rows.push({
      label: 'Lowest clearance', simulated: `${fixed(s.lowestClearance)} m`,
      planned: s.plannedLowestClearance === null ? '—' : `${fixed(s.plannedLowestClearance)} m`,
      note: 'The simulation also covers the transit and the turns as flown.',
    })
  }
  return rows
}

/** Coverage and overlap figures: `[{ label, value, detail }]`, empty without a camera. */
export function coverageRows(simulation) {
  const c = simulation.coverage
  if (!c) return []
  const s = c.stats
  const pct = v => (v === null || !Number.isFinite(v) ? '—' : `${Math.round(v)} %`)
  const rows = [
    { label: 'Survey area covered', value: pct(s.coveredPercent), detail: 'in at least one photo' },
    { label: 'In 3 or more photos', value: pct(s.atLeast3), detail: `in 5 or more: ${pct(s.atLeast5)}` },
    { label: 'Photos per point', value: s.meanPhotos ? s.meanPhotos.toFixed(1) : '—', detail: `average; least ${s.minPhotos || 0}` },
    { label: 'Planned overlap', value: `${pct(s.plannedFront)} front`, detail: `${pct(s.plannedSide)} side` },
  ]
  if (s.groundKnown && s.heightMin !== null && Math.abs(s.heightMax - s.heightMin) > 1) {
    rows.push({
      label: 'Over the highest ground', value: `${pct(s.lowestFront)} front`,
      detail: `${pct(s.lowestSide)} side, at ${Math.round(s.heightMin)} m above ground`,
    })
  }
  return rows
}

/**
 * Phase at each of `width` columns of the timeline bar, as phase ids in a
 * `Uint8Array`: what a long mission's thousands of phases reduce to on screen.
 */
export function timelineColumns(trajectory, width) {
  const n = Math.max(1, width | 0)
  const out = new Uint8Array(n)
  const { phases, duration } = trajectory
  let k = 0
  for (let i = 0; i < n; i++) {
    const t = ((i + 0.5) / n) * duration
    while (k < phases.length - 1 && phases[k].t1 <= t) k++
    out[i] = phases[k].phase
  }
  return out
}

/** Where each battery flight starts on the timeline, as fractions of its length. */
export function flightMarks(trajectory) {
  const { duration, flights } = trajectory
  return flights.map(f => ({ index: f.index, t: f.t0, start: duration > 0 ? f.t0 / duration : 0, end: duration > 0 ? f.t1 / duration : 1 }))
}
