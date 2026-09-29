import { describe, it, expect, vi, beforeEach } from 'vitest'
import { layoutFor, loggedIn, refreshSession } from './session.js'

describe('layoutFor', () => {
  it('uses AppLayout for routes without a layout flag', () => {
    expect(layoutFor({ requiresAuth: true }, true)).toBe('app')
    expect(layoutFor({}, false)).toBe('app')
    expect(layoutFor(undefined, null)).toBe('app')
  })

  it('renders bare pages for layout: false regardless of session', () => {
    expect(layoutFor({ layout: false }, true)).toBe('bare')
    expect(layoutFor({ layout: false }, false)).toBe('bare')
  })

  it('switches auto routes on the signed-in state, defaulting to bare while unknown', () => {
    expect(layoutFor({ layout: 'auto' }, true)).toBe('app')
    expect(layoutFor({ layout: 'auto' }, false)).toBe('bare')
    // null = not yet checked: never flash the app chrome at a visitor.
    expect(layoutFor({ layout: 'auto' }, null)).toBe('bare')
  })
})

describe('refreshSession', () => {
  beforeEach(() => {
    loggedIn.value = null
  })

  it('is true for a named user and false for Guest', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ message: 'a@b.c' }) }))
    expect(await refreshSession()).toBe(true)
    expect(loggedIn.value).toBe(true)

    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ message: 'Guest' }) }))
    expect(await refreshSession()).toBe(false)
  })

  it('treats errors and non-2xx as signed out', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }))
    expect(await refreshSession()).toBe(false)
    global.fetch = vi.fn(() => Promise.reject(new Error('offline')))
    expect(await refreshSession()).toBe(false)
    expect(loggedIn.value).toBe(false)
  })
})
