import { describe, it, expect } from 'vitest'
import { fieldType } from '@/composables/useOdmOptions'

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
