import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

import { signOut } from './signout.js'
import { loggedIn } from './session.js'
import { toast } from './toast'

const ok = body => ({ ok: true, json: () => Promise.resolve(body === undefined ? {} : body) })

describe('signOut', () => {
  beforeEach(() => {
    loggedIn.value = null
    window.csrf_token = 'tok'
    toast.error.mockClear()
  })

  it('posts a CSRF-protected logout, clears cached state and re-reads the session', async () => {
    global.fetch = vi.fn(url =>
      url === '/api/method/logout'
        ? Promise.resolve(ok())
        : Promise.resolve(ok({ message: 'Guest' })),
    )
    loggedIn.value = true

    expect(await signOut()).toBe(true)

    const call = global.fetch.mock.calls.find(c => c[0] === '/api/method/logout')
    expect(call[1].method).toBe('POST')
    expect(call[1].headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(window.csrf_token).toBeNull()
    expect(loggedIn.value).toBe(false)
  })

  it('reports a refused sign-out and leaves cached state untouched', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        json: () =>
          Promise.resolve({
            _server_messages: JSON.stringify([JSON.stringify({ message: 'Invalid Request' })]),
          }),
      }),
    )
    loggedIn.value = true

    expect(await signOut()).toBe(false)
    expect(toast.error).toHaveBeenCalledWith('Invalid Request')
    expect(loggedIn.value).toBe(true)
    expect(window.csrf_token).toBe('tok')
  })
})
