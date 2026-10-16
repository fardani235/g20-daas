import { describe, it, expect, afterEach } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import fixture from '@/lib/__fixtures__/flightplan.json'
import PlanFindings from './PlanFindings.vue'

let mounted = []
async function mount(props, listeners = {}) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp({ render: () => h(PlanFindings, { ...props, ...listeners }) })
  app.mount(el)
  mounted.push(app)
  await nextTick()
  return el
}

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
})

const { mission } = fixture

describe('PlanFindings', () => {
  it('lists findings worst first, each with how it was arrived at', async () => {
    const issues = [
      { code: 'TAKEOFF_ASSUMED', severity: 'info', category: 'geometry', basis: 'note', message: 'No takeoff point is set.' },
      { code: 'AGL_LIMIT', severity: 'warning', category: 'regulation', basis: 'advisory', message: 'Over the 120 m limit.' },
      { code: 'SPEED_ABOVE_MAX', severity: 'error', category: 'capability', basis: 'checked', message: 'Too fast.' },
    ]
    const el = await mount({ issues })
    const rows = [...el.querySelectorAll('[data-finding]')]
    expect(rows.map(r => r.dataset.finding)).toEqual(['SPEED_ABOVE_MAX', 'AGL_LIMIT', 'TAKEOFF_ASSUMED'])
    expect(rows.map(r => r.dataset.severity)).toEqual(['error', 'warning', 'info'])
    expect(rows[0].textContent).toContain('Checked against profile')
    expect(rows[1].textContent).toContain('Advisory')
    // A note is guidance, not a check: no badge claiming it was checked.
    expect(rows[2].textContent.trim()).toBe('Note: No takeoff point is set.')
    // Severity is announced to screen readers, not only shown by colour.
    expect(rows[0].querySelector('.sr-only').textContent).toContain('Blocks export')
  })

  it('labels terrain findings as estimates and offers to show them on the map', async () => {
    const at = [107.61, -6.82]
    const issues = [{ code: 'CLEARANCE_BELOW_MIN', severity: 'error', category: 'terrain', basis: 'estimated',
      message: 'Within 12 m of the terrain.', detail: { clearance_m: 12, at } }]
    const located = []
    const el = await mount({ issues }, { onLocate: p => located.push(p) })
    const row = el.querySelector('[data-finding="CLEARANCE_BELOW_MIN"]')
    expect(row.textContent).toContain('Estimate')
    row.querySelector('button').click()
    expect(located).toEqual([at])
  })

  it('says, per topic, what was checked, estimated and not looked at', async () => {
    const el = await mount({ issues: mission.issues, assurance: mission.assurance, notice: mission.notice })
    const topics = Object.fromEntries([...el.querySelectorAll('[data-assurance-topic]')]
      .map(n => [n.dataset.assuranceTopic, n]))
    expect(Object.keys(topics).sort()).toEqual(['airspace', 'battery', 'capability', 'obstacles', 'regulation', 'terrain', 'weather'])
    expect(topics.capability.dataset.status).toBe('checked')
    expect(topics.terrain.dataset.status).toBe('estimated')
    expect(topics.terrain.textContent).toContain('Estimate')
    expect(topics.airspace.dataset.status).toBe('not_checked')
    expect(topics.airspace.textContent).toContain('Not checked')
    expect(topics.obstacles.textContent).toContain('Not checked')
    expect(topics.weather.textContent).toContain('Not checked')
    expect(topics.regulation.textContent).toContain('Advisory')
    expect(el.querySelector('[role=note]').textContent).toContain('not verified flight-safety information')
  })

  it('never calls anything verified', async () => {
    const el = await mount({ issues: mission.issues, assurance: mission.assurance })
    const badges = [...el.querySelectorAll('[data-assurance] dt span, [data-finding] span')].map(n => n.textContent.trim())
    expect(badges.some(t => /verified|safe/i.test(t))).toBe(false)
  })

  it('compact mode shows the findings only', async () => {
    const el = await mount({ issues: mission.issues, assurance: mission.assurance, notice: mission.notice, compact: true })
    expect(el.querySelectorAll('[data-finding]').length).toBe(mission.issues.length)
    expect(el.querySelector('[data-assurance]')).toBeNull()
    expect(el.querySelector('[role=note]')).toBeNull()
  })

  it('says so when there is nothing to report', async () => {
    const el = await mount({ issues: [] })
    expect(el.textContent).toContain('No findings for this plan.')
  })
})
