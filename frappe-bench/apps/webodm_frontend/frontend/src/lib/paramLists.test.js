import { describe, it, expect } from 'vitest'
import { parseList, formatList } from './paramLists.js'

describe('paramLists', () => {
  it('parses comma and newline separated lists', () => {
    expect(parseList('car, person')).toEqual(['car', 'person'])
    expect(parseList('car\nperson\nbus')).toEqual(['car', 'person', 'bus'])
    expect(parseList(' car ,\n person , ')).toEqual(['car', 'person'])
  })

  it('returns an empty list for blank input', () => {
    expect(parseList('')).toEqual([])
    expect(parseList('  \n , ')).toEqual([])
    expect(parseList(null)).toEqual([])
  })

  it('formats arrays as one item per line', () => {
    expect(formatList(['a', 'b'])).toBe('a\nb')
    expect(formatList([])).toBe('')
    expect(formatList(undefined)).toBe('')
  })
})
