import { describe, it, expect, vi, afterEach } from 'vitest'
import { fieldType, useOdmOptions } from '@/composables/useOdmOptions'
import * as presetsLib from '@/lib/presets'

describe('fieldType', () => {
  it('bool -> checkbox', () => {
    expect(fieldType({ name: 'dsm', type: 'bool' })).toBe('checkbox')
  })
  it('enum (domain array) -> select', () => {
    expect(fieldType({ name: 'feature-quality', type: 'enum', domain: ['low', 'high'] })).toBe('select')
  })
  it('int/float -> number', () => {
    expect(fieldType({ name: 'min-num-features', type: 'int' })).toBe('number')
    expect(fieldType({ name: 'gps-accuracy', type: 'float' })).toBe('number')
  })
  it('string/other -> text', () => {
    expect(fieldType({ name: 'name', type: 'string' })).toBe('text')
    expect(fieldType({ name: 'x', type: 'mystery' })).toBe('text')
  })
})

describe('seedEnumDefaults', () => {
  afterEach(() => { vi.restoreAllMocks() })

  async function loadedOdm(catalog) {
    vi.spyOn(presetsLib, 'fetchOptions').mockResolvedValue(catalog)
    const odm = useOdmOptions()
    await odm.load()
    return odm
  }

  it('fills an untouched enum with its default value', async () => {
    const odm = await loadedOdm([{ name: 'feature-quality', type: 'enum', domain: ['low', 'high'], value: 'high' }])
    const values = {}
    odm.seedEnumDefaults(values)
    expect(values['feature-quality']).toBe('high')
  })

  it('falls back to the first domain entry when there is no default', async () => {
    const odm = await loadedOdm([{ name: 'pc-quality', type: 'enum', domain: ['medium', 'ultra'] }])
    const values = {}
    odm.seedEnumDefaults(values)
    expect(values['pc-quality']).toBe('medium')
  })

  it('never overrides an already-set value (preset/user wins)', async () => {
    const odm = await loadedOdm([{ name: 'feature-quality', type: 'enum', domain: ['low', 'high'], value: 'high' }])
    const values = { 'feature-quality': 'low' }
    odm.seedEnumDefaults(values)
    expect(values['feature-quality']).toBe('low')
  })

  it('ignores non-select options', async () => {
    const odm = await loadedOdm([{ name: 'dsm', type: 'bool' }, { name: 'min-num-features', type: 'int' }])
    const values = {}
    odm.seedEnumDefaults(values)
    expect(values).toEqual({})
  })
})
