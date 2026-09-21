import { describe, it, expect } from 'vitest'
import { cn, frappeErrorMessage } from './utils'

describe('cn', () => {
  it('joins plain class strings', () => {
    expect(cn('a', 'b')).toBe('a b')
  })

  it('drops falsy values', () => {
    expect(cn('a', false && 'b', null, undefined, 'c')).toBe('a c')
  })

  it('supports conditional object syntax', () => {
    expect(cn('a', { b: true, c: false })).toBe('a b')
  })

  it('lets a later Tailwind class win over an earlier conflicting one', () => {
    // This is the whole reason tailwind-merge exists: without it the result
    // would be "px-2 px-4" and the winner would depend on CSS source order.
    expect(cn('px-2', 'px-4')).toBe('px-4')
  })

  it('keeps non-conflicting Tailwind classes', () => {
    expect(cn('px-2', 'py-4')).toBe('px-2 py-4')
  })

  it('flattens arrays', () => {
    expect(cn(['a', 'b'], 'c')).toBe('a b c')
  })
})

describe('frappeErrorMessage', () => {
  it('prefers a plain message field', () => {
    expect(frappeErrorMessage({ message: 'Nope' })).toBe('Nope')
  })

  it('decodes the double-encoded _server_messages list from frappe.throw', () => {
    const body = {
      exc_type: 'ValidationError',
      _server_messages: JSON.stringify([
        JSON.stringify({ message: 'Invalid plugin package: package has no plugin.json at its root', title: 'Message' }),
      ]),
    }
    expect(frappeErrorMessage(body))
      .toBe('Invalid plugin package: package has no plugin.json at its root')
  })

  it('strips HTML and joins several server messages', () => {
    const body = {
      _server_messages: JSON.stringify([
        JSON.stringify({ message: '<b>First</b>' }),
        JSON.stringify({ message: 'Second' }),
      ]),
    }
    expect(frappeErrorMessage(body)).toBe('First Second')
  })

  it('falls back to the exception line, minus the exception type', () => {
    expect(frappeErrorMessage({ exception: 'frappe.exceptions.PermissionError: Only organization admins can manage plugins' }))
      .toBe('Only organization admins can manage plugins')
  })

  it('returns the fallback for empty or malformed bodies', () => {
    expect(frappeErrorMessage({})).toBe('Request failed')
    expect(frappeErrorMessage(null, 'Upload failed')).toBe('Upload failed')
    expect(frappeErrorMessage({ _server_messages: 'not json' })).toBe('Request failed')
  })
})
