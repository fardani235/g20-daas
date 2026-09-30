import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { createApp, nextTick } from 'vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ meta: { layout: false } }),
  useRouter: () => ({ beforeEach: vi.fn(), afterEach: vi.fn() }),
}))

import App from './App.vue'
import { loggedIn } from '@/lib/session.js'

const flush = async () => {
  for (let i = 0; i < 6; i++) await nextTick()
  await new Promise(r => setTimeout(r, 0))
}

let mounted = []
function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(App)
  // The page shell is irrelevant here; render nothing in place of <router-view>.
  app.component('router-view', { setup: () => () => null })
  app.mount(el)
  mounted.push({ app, el })
}

beforeEach(() => {
  loggedIn.value = null
  window.csrf_token = undefined
  if (!window.matchMedia) {
    window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  }
})

afterEach(() => {
  mounted.forEach(({ app, el }) => {
    app.unmount()
    el.remove()
  })
  mounted = []
})

const csrfCalls = calls => calls.filter(u => String(u).includes('csrf.get_token'))

describe('App CSRF bootstrap', () => {
  it('does not request the token for a guest', async () => {
    loggedIn.value = false
    const calls = []
    global.fetch = vi.fn(url => {
      calls.push(url)
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })

    mount()
    await flush()

    expect(csrfCalls(calls)).toHaveLength(0)
  })

  it('requests the token once the session is signed in', async () => {
    loggedIn.value = true
    const calls = []
    global.fetch = vi.fn(url => {
      calls.push(url)
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ message: 'tok' }) })
    })

    mount()
    await flush()

    expect(csrfCalls(calls)).toHaveLength(1)
    expect(window.csrf_token).toBe('tok')
  })
})
