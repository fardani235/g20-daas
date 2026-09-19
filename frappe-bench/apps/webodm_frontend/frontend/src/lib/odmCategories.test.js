import { describe, it, expect } from 'vitest'
import { ODM_CATEGORIES, groupOptions } from './odmCategories'

// Build a fake catalog from a list of option names.
const cat = (...names) => names.map(name => ({ name, type: 'bool' }))

describe('groupOptions', () => {
  it('maps options into their declared category, preserving category order', () => {
    // Pick one real name from two different categories, in reverse declaration
    // order, and assert the output follows ODM_CATEGORIES order, not input order.
    const groups = groupOptions(cat('pc-quality', 'auto-boundary'))
    const names = groups.map(g => g.name)
    expect(names).toEqual(['General', 'Point Cloud'])
    expect(groups[0].options.map(o => o.name)).toEqual(['auto-boundary'])
    expect(groups[1].options.map(o => o.name)).toEqual(['pc-quality'])
  })

  it('puts an unlisted option into the Advanced catch-all', () => {
    const groups = groupOptions(cat('totally-new-flag'))
    expect(groups).toHaveLength(1)
    expect(groups[0].name).toBe('Advanced')
    expect(groups[0].options.map(o => o.name)).toEqual(['totally-new-flag'])
  })

  it('omits a category with no matching catalog entries', () => {
    const groups = groupOptions(cat('auto-boundary'))
    expect(groups.map(g => g.name)).toEqual(['General'])
  })

  it('omits Advanced when every option is mapped', () => {
    const groups = groupOptions(cat('auto-boundary', 'pc-quality'))
    expect(groups.map(g => g.name)).not.toContain('Advanced')
  })

  it('places every catalog option exactly once (no drops, no duplicates)', () => {
    const input = cat('auto-boundary', 'pc-quality', 'mesh-size', 'unknown-x', 'unknown-y')
    const groups = groupOptions(input)
    const out = groups.flatMap(g => g.options.map(o => o.name))
    expect(out.sort()).toEqual(input.map(o => o.name).sort())
    expect(out).toHaveLength(new Set(out).size)
  })

  it('Advanced is always last when present', () => {
    const groups = groupOptions(cat('unknown-x', 'auto-boundary'))
    expect(groups[groups.length - 1].name).toBe('Advanced')
  })

  it('ODM_CATEGORIES has no option assigned to two categories', () => {
    const seen = new Set()
    for (const c of ODM_CATEGORIES) {
      for (const n of c.options) {
        expect(seen.has(n)).toBe(false)
        seen.add(n)
      }
    }
  })
})
