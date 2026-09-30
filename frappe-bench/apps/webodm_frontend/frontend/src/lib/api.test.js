import { describe, it, expect, vi, beforeEach } from 'vitest'
import { postMethod, logout } from './api.js'

const ok = message => ({ ok: true, json: () => Promise.resolve(message === undefined ? {} : { message }) })

describe('postMethod', () => {
  beforeEach(() => {
    window.csrf_token = 'tok'
  })

  it('posts the method with the CSRF header and a JSON body', async () => {
    global.fetch = vi.fn(() => Promise.resolve(ok({ saved: true })))
    const result = await postMethod('some.method', { a: 1 })
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/method/some.method')
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(opts.headers['Content-Type']).toBe('application/json')
    expect(opts.body).toBe('{"a":1}')
    expect(result).toEqual({ saved: true })
  })

  it('omits the CSRF header when no token is cached', async () => {
    window.csrf_token = undefined
    global.fetch = vi.fn(() => Promise.resolve(ok()))
    await postMethod('some.method')
    expect(global.fetch.mock.calls[0][1].headers['X-Frappe-CSRF-Token']).toBeUndefined()
  })

  it('throws the decoded Frappe error on a non-OK response', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        json: () =>
          Promise.resolve({
            _server_messages: JSON.stringify([JSON.stringify({ message: 'Invalid Request' })]),
          }),
      }),
    )
    await expect(postMethod('some.method')).rejects.toThrow('Invalid Request')
  })

  it('resolves the raw body when there is no message key', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ other: 1 }) }))
    expect(await postMethod('some.method')).toEqual({ other: 1 })
  })
})

describe('logout', () => {
  it('posts to the logout method and reads the token lazily', async () => {
    window.csrf_token = undefined
    global.fetch = vi.fn(() => Promise.resolve(ok()))
    window.csrf_token = 'later'
    await logout()
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/method/logout')
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-Frappe-CSRF-Token']).toBe('later')
  })
})
