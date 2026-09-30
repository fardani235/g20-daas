import { describe, it, expect, vi, beforeEach } from 'vitest'

// Keep the real layoutFor (used to show the shell decision); stub only the
// session read the guard depends on.
vi.mock('./session', async importOriginal => {
  const actual = await importOriginal()
  return { ...actual, refreshSession: vi.fn() }
})
vi.mock('./organization', () => ({ getMyOrganization: vi.fn() }))

import { resolveNavigation } from './routeGuard.js'
import { layoutFor, refreshSession } from './session.js'
import { getMyOrganization } from './organization.js'

const protectedRoute = (name, fullPath) => ({ name, fullPath, meta: { requiresAuth: true } })

describe('resolveNavigation', () => {
  beforeEach(() => {
    refreshSession.mockReset()
    getMyOrganization.mockReset()
  })

  it('redirects a signed-out visitor to sign-in and preserves the destination', async () => {
    refreshSession.mockResolvedValue(false)
    expect(await resolveNavigation(protectedRoute('Dashboard', '/dashboard'))).toEqual({
      name: 'Login',
      query: { redirect: '/dashboard' },
    })
    expect(await resolveNavigation(protectedRoute('Projects', '/projects'))).toEqual({
      name: 'Login',
      query: { redirect: '/projects' },
    })
    expect(getMyOrganization).not.toHaveBeenCalled()
  })

  it('keeps a signed-out visitor out of the app shell on a protected route', async () => {
    // After sign-out the session is guest; the attempted route would otherwise
    // render inside AppLayout, and the redirect is what prevents that.
    refreshSession.mockResolvedValue(false)
    const redirect = await resolveNavigation(protectedRoute('Dashboard', '/dashboard'))
    expect(redirect.name).toBe('Login')
    expect(layoutFor({ requiresAuth: true }, false)).toBe('app')
    expect(layoutFor({ layout: false }, false)).toBe('bare')
  })

  it('allows a signed-in member who belongs to an organization', async () => {
    refreshSession.mockResolvedValue(true)
    getMyOrganization.mockResolvedValue({ organization: 'Acme' })
    expect(await resolveNavigation(protectedRoute('Dashboard', '/dashboard'))).toBeNull()
  })

  it('sends a signed-in user without an organization to onboarding', async () => {
    refreshSession.mockResolvedValue(true)
    getMyOrganization.mockResolvedValue({ organization: null })
    expect(await resolveNavigation(protectedRoute('Dashboard', '/dashboard'))).toEqual({
      name: 'Onboarding',
    })
  })

  it('sends a signed-in user to onboarding when the organization lookup fails', async () => {
    refreshSession.mockResolvedValue(true)
    getMyOrganization.mockRejectedValue(new Error('offline'))
    expect(await resolveNavigation(protectedRoute('Dashboard', '/dashboard'))).toEqual({
      name: 'Onboarding',
    })
  })

  it('does not gate the onboarding route itself on an organization', async () => {
    refreshSession.mockResolvedValue(true)
    const onboarding = { name: 'Onboarding', fullPath: '/onboarding', meta: { requiresAuth: true } }
    expect(await resolveNavigation(onboarding)).toBeNull()
    expect(getMyOrganization).not.toHaveBeenCalled()
  })

  it('resolves the session for an auto-layout route without gating it', async () => {
    refreshSession.mockResolvedValue(false)
    const marketplace = { name: 'Marketplace', fullPath: '/marketplace', meta: { layout: 'auto' } }
    expect(await resolveNavigation(marketplace)).toBeNull()
    expect(refreshSession).toHaveBeenCalledTimes(1)
  })

  it('allows a public route without reading the session', async () => {
    const landing = { name: 'Landing', fullPath: '/', meta: { layout: false } }
    expect(await resolveNavigation(landing)).toBeNull()
    expect(refreshSession).not.toHaveBeenCalled()
  })
})
