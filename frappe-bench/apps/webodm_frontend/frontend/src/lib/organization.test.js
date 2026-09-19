import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as org from './organization.js'

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve({ message: { organization: 'Acme', role: 'Owner' } }) }))
  global.window = { csrf_token: 'x' }
})

describe('organization lib', () => {
  it('getMyOrganization hits the right method', async () => {
    const res = await org.getMyOrganization()
    expect(res.organization).toBe('Acme')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('webodm_core.api.organization.get_my_organization'),
      expect.objectContaining({ method: 'GET' }))
  })

  it('createOrganization posts the name', async () => {
    await org.createOrganization('New Co')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('webodm_core.api.organization.create_organization'),
      expect.objectContaining({ method: 'POST' }))
  })
})
