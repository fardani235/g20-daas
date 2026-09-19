import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import HeroFlightSim from '@/components/HeroFlightSim.vue'

// Minimal observer doubles: jsdom has neither, and the component only observes,
// disconnects, and reacts to a callback.
class MockObserver {
  static instances = []
  constructor(cb) {
    this.cb = cb
    this.disconnected = false
    MockObserver.instances.push(this)
  }
  observe() {}
  disconnect() {
    this.disconnected = true
  }
  trigger(payload) {
    this.cb([payload])
  }
}

let apps = []
let raf
let caf
let motionMatches

function mountHero() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp({ render: () => h(HeroFlightSim) })
  app.mount(el)
  apps.push({ app, el })
  return el
}

beforeEach(() => {
  apps = []
  MockObserver.instances = []
  motionMatches = false

  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  raf = vi.fn(() => 1)
  caf = vi.fn()
  vi.stubGlobal('requestAnimationFrame', raf)
  vi.stubGlobal('cancelAnimationFrame', caf)
  vi.stubGlobal('IntersectionObserver', MockObserver)
  vi.stubGlobal('ResizeObserver', MockObserver)

  const media = {
    media: '(prefers-reduced-motion: reduce)',
    get matches() {
      return motionMatches
    },
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    onchange: null,
    dispatchEvent: vi.fn(),
  }
  const mm = vi.fn(() => media)
  vi.stubGlobal('matchMedia', mm)
  Object.defineProperty(window, 'matchMedia', { value: mm, configurable: true, writable: true })
})

afterEach(() => {
  for (const { app } of apps) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('HeroFlightSim', () => {
  it('mounts with a labeled canvas, telemetry, and a selected-area panel', async () => {
    const el = mountHero()
    await nextTick()

    const canvas = el.querySelector('canvas')
    expect(canvas).toBeTruthy()
    expect(canvas.getAttribute('role')).toBe('img')
    expect(canvas.getAttribute('aria-label')).toMatch(/drone/i)

    const text = el.textContent
    expect(text).toContain('Flight sim')
    expect(text).toContain('ALT:')
    expect(text).toContain('SURVEYING')
    expect(text).toContain('Stockpile volume')
  })

  it('toggles pause/resume, layer, and reset from native buttons', async () => {
    const el = mountHero()
    await nextTick()

    const pause = el.querySelector('[aria-label="Pause flight simulation"]')
    expect(pause.tagName).toBe('BUTTON')
    pause.click()
    await nextTick()
    expect(el.querySelector('[aria-label="Resume flight simulation"]')).toBeTruthy()
    expect(el.textContent).toContain('HOLDING')

    const thermal = [...el.querySelectorAll('button')].find((b) =>
      b.textContent.includes('Thermal')
    )
    thermal.click()
    await nextTick()
    expect(thermal.getAttribute('aria-pressed')).toBe('true')

    const reset = el.querySelector('[aria-label="Reset flight plan"]')
    reset.click()
    await nextTick()
    expect(el.querySelector('[aria-label="Pause flight simulation"]')).toBeTruthy()
    expect(el.textContent).toContain('SURVEYING')
  })

  it('stops the animation loop when offscreen and resumes when visible', async () => {
    const el = mountHero()
    await nextTick()

    const intersection = MockObserver.instances.find((o) => o.cb.length === 1)
    expect(intersection).toBeTruthy()
    expect(raf).toHaveBeenCalled()

    const rafCallsBefore = raf.mock.calls.length
    intersection.trigger({ isIntersecting: false })
    await nextTick()
    expect(caf).toHaveBeenCalled()

    intersection.trigger({ isIntersecting: true })
    await nextTick()
    expect(raf.mock.calls.length).toBeGreaterThan(rafCallsBefore)
  })

  it('does not autoplay when reduced motion is requested', async () => {
    motionMatches = true
    const el = mountHero()
    await nextTick()
    expect(raf).not.toHaveBeenCalled()
    // content still rendered
    expect(el.textContent).toContain('Stockpile volume')
  })
})
