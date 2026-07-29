import { describe, it, expect } from 'vitest'
import {
  PRIMARY_TABS,
  activePrimaryTab,
  secondaryTabs,
  activeSecondaryTab,
} from './nav'

describe('PRIMARY_TABS', () => {
  it('lists the six app sections in order', () => {
    expect(PRIMARY_TABS.map(t => t.to)).toEqual([
      '/dashboard',
      '/projects',
      '/presets',
      '/invoices',
      '/settings',
      '/plugins',
    ])
  })

  it('gives every tab a label and an icon', () => {
    for (const tab of PRIMARY_TABS) {
      expect(tab.label).toBeTruthy()
      expect(tab.icon).toBeTruthy()
    }
  })
})

describe('activePrimaryTab', () => {
  it('matches a tab exactly', () => {
    expect(activePrimaryTab('/projects')).toBe('/projects')
    expect(activePrimaryTab('/settings')).toBe('/settings')
  })

  it('matches nested paths under a tab', () => {
    expect(activePrimaryTab('/projects/anything')).toBe('/projects')
  })

  it('keeps Projects active on project-detail routes', () => {
    // A project detail page lives at /project/:id (singular), which is not a
    // prefix of /projects — so it needs an explicit rule, or the tab bar would
    // show nothing active while the user is inside a project.
    expect(activePrimaryTab('/project/abc123')).toBe('/projects')
    expect(activePrimaryTab('/project/abc123/task/t1/model')).toBe('/projects')
    expect(activePrimaryTab('/project/abc123/task/t1/console')).toBe('/projects')
  })

  it('does not treat a longer sibling segment as nested', () => {
    // "/presets-archive" must NOT activate "/presets".
    expect(activePrimaryTab('/presets-archive')).toBe(null)
  })

  it('returns null for routes outside the tab bar', () => {
    expect(activePrimaryTab('/')).toBe(null)
    expect(activePrimaryTab('/login')).toBe(null)
    expect(activePrimaryTab('/onboarding')).toBe(null)
    expect(activePrimaryTab('/nope')).toBe(null)
  })

  it('tolerates trailing slashes', () => {
    expect(activePrimaryTab('/projects/')).toBe('/projects')
  })

  it('handles empty and nullish input without throwing', () => {
    expect(activePrimaryTab('')).toBe(null)
    expect(activePrimaryTab(null)).toBe(null)
    expect(activePrimaryTab(undefined)).toBe(null)
  })
})

describe('secondaryTabs', () => {
  it('is empty on non-project routes', () => {
    expect(secondaryTabs('/dashboard')).toEqual([])
    expect(secondaryTabs('/projects')).toEqual([])
    expect(secondaryTabs('/login')).toEqual([])
    expect(secondaryTabs(null)).toEqual([])
  })

  it('offers only Map on a project route with no task selected', () => {
    // 3D Model and Console are task-scoped; without a task id in the path
    // there is nothing to link them to.
    expect(secondaryTabs('/project/p1')).toEqual([
      { to: '/project/p1', label: 'Map' },
    ])
  })

  it('offers Map, 3D Model, and Console when a task is in the path', () => {
    expect(secondaryTabs('/project/p1/task/t9/model')).toEqual([
      { to: '/project/p1', label: 'Map' },
      { to: '/project/p1/task/t9/model', label: '3D Model' },
      { to: '/project/p1/task/t9/console', label: 'Console' },
    ])
  })

  it('builds the same row from the console route', () => {
    expect(secondaryTabs('/project/p1/task/t9/console')).toEqual([
      { to: '/project/p1', label: 'Map' },
      { to: '/project/p1/task/t9/model', label: '3D Model' },
      { to: '/project/p1/task/t9/console', label: 'Console' },
    ])
  })

  it('preserves encoded ids verbatim', () => {
    // Project names are Frappe doc names and can contain encoded characters;
    // round-tripping them through the tab links must not corrupt them.
    const tabs = secondaryTabs('/project/My%20Site/task/t1/model')
    expect(tabs[0].to).toBe('/project/My%20Site')
    expect(tabs[1].to).toBe('/project/My%20Site/task/t1/model')
  })
})

describe('activeSecondaryTab', () => {
  it('marks Map active on the project root', () => {
    expect(activeSecondaryTab('/project/p1')).toBe('/project/p1')
  })

  it('marks the model tab active on the model route', () => {
    expect(activeSecondaryTab('/project/p1/task/t9/model')).toBe(
      '/project/p1/task/t9/model',
    )
  })

  it('marks the console tab active on the console route', () => {
    expect(activeSecondaryTab('/project/p1/task/t9/console')).toBe(
      '/project/p1/task/t9/console',
    )
  })

  it('returns null where there is no secondary row', () => {
    expect(activeSecondaryTab('/dashboard')).toBe(null)
    expect(activeSecondaryTab(null)).toBe(null)
  })
})
