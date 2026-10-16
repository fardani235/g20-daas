// The flight simulator's controller: one simulation, one clock, every view.
//
// Owns what the views share - the simulation built from the mission, the
// clock, the state sampled for the current time and the coverage canvas -
// and hands each frame to whoever subscribed (`onFrame`): the 2D map layer
// and the 3D scene get the *same* state object in the same call, which is
// what keeps them in step. Vue only sees what the panels need: the playback
// position every frame, and a copy of the state a few times a second for
// the dashboard.
//
// Named apart from `useFlightSim`, which is the landing page's animation.

import { markRaw, reactive, ref, shallowRef, toRaw } from 'vue'
import { SimulationError, createClock, createSimulation } from '@/lib/flightsim'
import { getSimulationTerrain } from '@/lib/flightsim/api'

const DASHBOARD_INTERVAL_MS = 100
// While playing, the coverage overlay is repainted at most this often: at
// high speeds photos arrive every frame, and re-uploading the raster to the
// map and to the GPU 60 times a second buys nothing the eye can see. A
// pause, a jump or the end of the flight always paints the exact moment.
const COVERAGE_INTERVAL_MS = 50

/**
 * `loadTerrain`, `clock` (options for `createClock`) and `now` are
 * injectable for tests.
 */
export function useMissionSim({ loadTerrain = getSimulationTerrain, clock: clockOptions = {}, now = () => performance.now() } = {}) {
  const status = ref('idle')                 // 'idle' | 'loading' | 'ready' | 'error'
  const error = ref('')
  const terrainMessage = ref('')             // why there is no elevation data, when there is a reason
  const simulation = shallowRef(null)
  const build = ref(0)                       // counts simulations built: a view keyed on it never outlives its simulation
  const playback = reactive({ time: 0, duration: 0, playing: false, speed: 1, ended: false })
  const state = shallowRef(null)             // dashboard copy of the sampled state
  const coverageMode = ref('covered')        // 'off' | 'covered' | 'overlap'
  const canvas = shallowRef(null)            // the coverage raster, shared by the map and the scene

  const frameListeners = new Set()
  const coverageListeners = new Set()
  const live = {}                            // the state every view draws; reused frame to frame
  let clock = null
  let request = 0
  let lastDashboard = -Infinity
  let lastCoverage = -Infinity
  let ctx = null
  let image = null

  function paintCoverage(force = false, reason = '') {
    const sim = simulation.value
    if (!sim?.coverage || !image || coverageMode.value === 'off') return
    const t = now()
    if (reason === 'tick' && t - lastCoverage < COVERAGE_INTERVAL_MS) return
    if (sim.coverage.paint(image.data, live.photos, coverageMode.value) || force) {
      lastCoverage = t
      ctx.putImageData(image, 0, 0)
      for (const fn of coverageListeners) fn()
    }
  }

  function onClock(s, reason) {
    const sim = simulation.value
    if (!sim) return
    sim.sample(s.time, live)
    paintCoverage(false, reason)
    for (const fn of frameListeners) fn(live, reason)
    playback.time = s.time
    playback.duration = s.duration
    playback.playing = s.playing
    playback.speed = s.speed
    playback.ended = s.ended
    const t = now()
    if (reason !== 'tick' || t - lastDashboard >= DASHBOARD_INTERVAL_MS) {
      state.value = { ...live }
      lastDashboard = t
    }
  }

  function teardown() {
    if (clock) { clock.destroy(); clock = null }
    simulation.value = null
    state.value = null
    canvas.value = null
    ctx = image = null
    Object.assign(playback, { time: 0, duration: 0, playing: false, ended: false })
    lastCoverage = lastDashboard = -Infinity
  }

  /**
   * Build the simulation of `mission` and stop at its start. `definition`
   * and `project` are what the planner sent to compute it: the elevation
   * grid is requested for the same definition. Resolves true when ready.
   */
  async function open(mission, { definition = null, project = null } = {}) {
    // The page holds the mission in a ref; the simulation reads the plain
    // object behind it (thousands of waypoints, read in tight loops) and
    // never writes to it.
    mission = toRaw(mission)
    definition = toRaw(definition)
    const id = ++request
    teardown()
    status.value = 'loading'
    error.value = ''
    terrainMessage.value = ''
    let grid = null
    const source = mission?.elevation_source || definition?.elevation_source || 'auto'
    if (definition && source !== 'none') {
      try {
        const out = await loadTerrain({ definition, project })
        if (id !== request) return false
        grid = out?.terrain || null
        terrainMessage.value = out?.message || ''
      } catch (e) {
        if (id !== request) return false
        terrainMessage.value = e?.message || 'Elevation data could not be loaded'
      }
    }
    let sim
    try {
      sim = createSimulation(mission, grid)
    } catch (e) {
      status.value = 'error'
      error.value = e instanceof SimulationError ? e.message : 'The simulation could not be built from this plan'
      if (!(e instanceof SimulationError)) console.error(e)
      return false
    }
    simulation.value = markRaw(sim)
    build.value++
    if (sim.coverage && typeof document !== 'undefined') {
      const el = document.createElement('canvas')
      el.width = sim.coverage.cols
      el.height = sim.coverage.rows
      ctx = el.getContext ? el.getContext('2d') : null
      image = ctx ? ctx.createImageData(el.width, el.height) : null
      canvas.value = markRaw(el)
    }
    clock = createClock({ duration: sim.duration, speed: playback.speed, ...clockOptions })
    clock.subscribe(onClock)
    status.value = 'ready'
    onClock(clock.state, 'open')
    return true
  }

  function close() {
    request++
    teardown()
    status.value = 'idle'
    error.value = ''
    terrainMessage.value = ''
  }

  /** Subscribe a view to every frame. It is called at once with the current state. Returns the unsubscribe function. */
  function onFrame(fn) {
    frameListeners.add(fn)
    if (simulation.value && clock) fn(live, 'subscribe')
    return () => frameListeners.delete(fn)
  }

  /** Called whenever the coverage canvas was repainted. */
  function onCoverage(fn) {
    coverageListeners.add(fn)
    return () => coverageListeners.delete(fn)
  }

  function setCoverageMode(mode) {
    coverageMode.value = mode
    paintCoverage(true)
    // Views show or hide their overlay on the next frame; give them one now.
    if (clock) for (const fn of frameListeners) fn(live, 'coverage')
  }

  const withClock = fn => (...args) => { if (clock) fn(...args) }

  return {
    status, error, terrainMessage, simulation, build, playback, state, coverageMode, canvas,
    open,
    close,
    onFrame,
    onCoverage,
    setCoverageMode,
    play: withClock(() => clock.play()),
    pause: withClock(() => clock.pause()),
    toggle: withClock(() => clock.toggle()),
    stop: withClock(() => clock.stop()),
    restart: withClock(() => clock.restart()),
    seek: withClock(t => clock.seek(t)),
    setSpeed: withClock(x => clock.setSpeed(x)),
    /** Jump to the first moment `metres` of ground distance have been flown (a click on the profile). */
    seekDistance: withClock(metres => clock.seek(simulation.value.trajectory.timeAtDistance(metres))),
    /** Jump to the start of battery flight `index`. */
    seekFlight: withClock(index => {
      const flight = simulation.value.trajectory.flights[index]
      if (flight) clock.seek(flight.t0)
    }),
    /** Redraw every view with the current state (after a view was mounted or resized). */
    refresh: withClock(() => onClock(clock.state, 'refresh')),
  }
}
