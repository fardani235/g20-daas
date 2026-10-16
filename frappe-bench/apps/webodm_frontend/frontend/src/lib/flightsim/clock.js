// The simulation clock: one time for every view.
//
// The 2D map, the 3D scene, the dashboard and the profile chart do not keep
// time of their own. They subscribe here and are handed the same simulation
// time in the same call, so they cannot drift apart. The clock only decides
// *what time it is*; what the aircraft is doing at that time is the
// trajectory's business and depends on nothing else.
//
// Time sources are injected (`now`, `raf`, `caf`) so tests can drive it.

export const SPEEDS = Object.freeze([0.5, 1, 2, 4, 8, 16, 32, 64])

// A background tab stops animation frames; coming back must not leap the
// flight forward by however long the tab was hidden.
const MAX_FRAME_SECONDS = 0.25

/**
 * Create a clock for a timeline of `duration` seconds.
 *
 * Controls: `play`, `pause`, `toggle`, `stop` (back to the start, paused),
 * `restart` (back to the start, playing), `seek(t)`, `setSpeed(x)`,
 * `setDuration(d)`. `subscribe(fn)` calls `fn(clock.state, reason)` after
 * every change and returns an unsubscribe function. `destroy()` stops
 * everything.
 */
export function createClock({
  duration = 0,
  speed = 1,
  now = () => performance.now(),
  raf = cb => requestAnimationFrame(cb),
  caf = id => cancelAnimationFrame(id),
} = {}) {
  const state = { time: 0, duration: Math.max(0, duration), speed, playing: false, ended: false }
  const listeners = new Set()
  let frame = null
  let last = 0
  let dead = false

  const emit = reason => { for (const fn of [...listeners]) fn(state, reason) }

  function tick() {
    frame = null
    if (!state.playing || dead) return
    const t = now()
    const dt = Math.min(Math.max((t - last) / 1000, 0), MAX_FRAME_SECONDS)
    last = t
    state.time += dt * state.speed
    if (state.time >= state.duration) {
      state.time = state.duration
      state.playing = false
      state.ended = true
      emit('end')
      return
    }
    emit('tick')
    frame = raf(tick)
  }

  function cancel() {
    if (frame !== null) { caf(frame); frame = null }
  }

  const api = {
    state,
    play() {
      if (dead || state.playing || !(state.duration > 0)) return
      // Play at the end starts over, as a media player does.
      if (state.time >= state.duration) state.time = 0
      state.playing = true
      state.ended = false
      last = now()
      emit('play')
      frame = raf(tick)
    },
    pause() {
      if (!state.playing) return
      state.playing = false
      cancel()
      emit('pause')
    },
    toggle() {
      if (state.playing) api.pause()
      else api.play()
    },
    stop() {
      state.playing = false
      state.ended = false
      state.time = 0
      cancel()
      emit('stop')
    },
    restart() {
      api.stop()
      api.play()
    },
    seek(t) {
      const next = Math.min(Math.max(Number(t) || 0, 0), state.duration)
      state.time = next
      state.ended = next >= state.duration && state.duration > 0
      if (state.ended && state.playing) { state.playing = false; cancel() }
      emit('seek')
    },
    setSpeed(x) {
      const next = Number(x)
      if (!(next > 0) || next === state.speed) return
      state.speed = next
      emit('speed')
    },
    setDuration(d) {
      state.duration = Math.max(0, Number(d) || 0)
      if (state.time > state.duration) state.time = state.duration
      if (!(state.duration > 0)) { state.playing = false; cancel() }
      emit('duration')
    },
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    destroy() {
      dead = true
      state.playing = false
      cancel()
      listeners.clear()
    },
  }
  return api
}
