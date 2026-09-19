import { describe, it, expect, beforeEach } from 'vitest'
import {
  VOLUME_BASE_METHODS,
  DEFAULT_VOLUME_BASE_METHOD,
  loadVolumeBaseMethod,
  saveVolumeBaseMethod,
  VOLUME_BASE_METHOD_STORAGE_KEY,
} from '@/lib/volumeMethods'

describe('VOLUME_BASE_METHODS', () => {
  it('offers WebODM\'s five base methods in its order', () => {
    expect(VOLUME_BASE_METHODS.map(m => m.value)).toEqual([
      'triangulate',
      'plane',
      'average',
      'highest',
      'lowest',
    ])
  })

  it('gives every method a human label', () => {
    for (const m of VOLUME_BASE_METHODS) {
      expect(m.label).toBeTruthy()
    }
  })

  it('defaults to triangulate, matching WebODM', () => {
    expect(DEFAULT_VOLUME_BASE_METHOD).toBe('triangulate')
  })
})

describe('loadVolumeBaseMethod', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('returns the default when nothing is stored', () => {
    expect(loadVolumeBaseMethod()).toBe('triangulate')
  })

  it('returns a previously saved method', () => {
    saveVolumeBaseMethod('plane')
    expect(loadVolumeBaseMethod()).toBe('plane')
  })

  it('ignores a stored value that is not a known method', () => {
    localStorage.setItem(VOLUME_BASE_METHOD_STORAGE_KEY, 'bogus')
    expect(loadVolumeBaseMethod()).toBe('triangulate')
  })

  it('does not persist an unknown method', () => {
    saveVolumeBaseMethod('bogus')
    expect(loadVolumeBaseMethod()).toBe('triangulate')
  })
})
