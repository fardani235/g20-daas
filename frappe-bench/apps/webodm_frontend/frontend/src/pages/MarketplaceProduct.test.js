import { describe, it, expect, vi, afterEach } from 'vitest'
import { createApp, h, nextTick } from 'vue'

const getProduct = vi.fn()
const installProduct = vi.fn(async () => ({ version: '1.1.0' }))
const uninstallProduct = vi.fn(async () => ({ removed: true }))

vi.mock('@/lib/marketplace', async importOriginal => {
  const actual = await importOriginal()
  return {
    ...actual,
    getProduct: (...a) => getProduct(...a),
    installProduct: (...a) => installProduct(...a),
    uninstallProduct: (...a) => uninstallProduct(...a),
  }
})
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { product: 'elevation-mask' }, fullPath: '/marketplace/elevation-mask' }) }))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import MarketplaceProduct from './MarketplaceProduct.vue'
import { loggedIn } from '@/lib/session.js'

// Minimal <router-link> stand-in: the page is mounted without a router.
const RouterLinkStub = {
  props: ['to'],
  setup(props, { slots }) {
    return () => h('a', { href: typeof props.to === 'string' ? props.to : props.to?.path }, slots.default?.())
  },
}

const flush = async () => { for (let i = 0; i < 6; i++) await nextTick(); await new Promise(r => setTimeout(r, 0)) }

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(MarketplaceProduct)
  app.component('router-link', RouterLinkStub)
  app.config.globalProperties.$route = { fullPath: '/marketplace/elevation-mask' }
  app.mount(el)
  mounted.push(app)
  await flush()
  return el
}

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  getProduct.mockReset()
  installProduct.mockClear()
  uninstallProduct.mockClear()
})

const release = (version, extra = {}) => ({
  name: `elevation-mask-${version}`, version, status: 'Published', published_on: '2026-09-29 10:00:00',
  artifact_hash: 'a'.repeat(64), artifact_size: 2048, installable: true, anonymous_download: false,
  download_url: null, release_notes: '', license_notes: '',
  license: { license_id: 'MIT', name: 'MIT License', url: 'https://opensource.org/license/mit', permits_redistribution: true },
  manifest: { id: 'elevation-mask', output_kind: 'raster', inputs: [{ name: 'raster', datasets: ['dsm'] }], parameters: ['threshold'] },
  ...extra,
})

function product(viewer, extra = {}) {
  const latest = release('1.1.0')
  return {
    product_id: 'elevation-mask', title: 'Elevation Mask', summary: 'Mask cells above a threshold',
    artifact_kind: 'plugin', publisher: { display_name: 'G20 Tech', kind: 'First-party' },
    categories: [{ category_id: 'terrain', label: 'Terrain' }], icon: null, allow_anonymous_download: false,
    description: '# Hello\n\nUse **wisely**.', docs: null, media: [], install_count: 3,
    latest_release: latest, releases: [latest, release('1.0.0')],
    viewer, ...extra,
  }
}

const panel = el => el.querySelector('[data-install-panel]')
const buttons = el => [...panel(el).querySelectorAll('button, a')].map(b => b.textContent.trim()).filter(Boolean)

describe('MarketplaceProduct install panel', () => {
  it('invites a signed-out visitor to sign in and hides Download when not allowed', async () => {
    loggedIn.value = false
    getProduct.mockResolvedValue(product({ signed_in: false, organization: null, can_install: false }))
    const el = await mount()
    expect(panel(el).dataset.state).toBe('guest')
    expect(buttons(el)).toContain('Sign in to install')
    expect(buttons(el).join(' ')).not.toMatch(/Download/)
    expect(el.textContent).toContain('does not offer this package for download without an account')
    // Public chrome around the page.
    expect(el.querySelector('nav')).not.toBeNull()
    // Description rendered from markdown, escaped.
    expect(el.querySelector('[data-section="overview"] h1').textContent).toBe('Hello')
    expect(el.querySelector('[data-section="overview"] strong').textContent).toBe('wisely')
  })

  it('shows Download to a signed-out visitor when the publisher allows it', async () => {
    loggedIn.value = false
    const p = product({ signed_in: false, organization: null, can_install: false }, { allow_anonymous_download: true })
    p.latest_release.anonymous_download = true
    p.latest_release.download_url = '/api/method/webodm_core.api.marketplace.download_release?release=elevation-mask-1.1.0'
    getProduct.mockResolvedValue(p)
    const el = await mount()
    const link = panel(el).querySelector('a[href*="download_release"]')
    expect(link).not.toBeNull()
    expect(link.textContent).toContain('Download v1.1.0')
    expect(el.textContent).toContain('the license permits redistribution')
  })

  it('tells members to ask an admin and offers Install to admins', async () => {
    loggedIn.value = true
    getProduct.mockResolvedValue(product({ signed_in: true, organization: 'Acme', can_install: false, entitlement: null }))
    let el = await mount()
    expect(panel(el).dataset.state).toBe('member')
    expect(el.textContent).toContain('Only organization admins can install')
    expect(el.querySelector('nav')).toBeNull() // AppLayout provides chrome when signed in

    mounted.forEach(a => a.unmount()); mounted = []; document.body.innerHTML = ''
    getProduct.mockClear()
    getProduct.mockResolvedValue(product({ signed_in: true, organization: 'Acme', can_install: true, entitlement: null }))
    el = await mount()
    expect(panel(el).dataset.state).toBe('install')
    const btn = [...panel(el).querySelectorAll('button')].find(b => b.textContent.includes('Install into Acme'))
    expect(btn).toBeTruthy()
    btn.click()
    await flush()
    expect(installProduct).toHaveBeenCalledWith('elevation-mask', undefined)
    expect(getProduct).toHaveBeenCalledTimes(2) // reloaded after install
  })

  it('shows installed / update states and installs an explicit older version', async () => {
    loggedIn.value = true
    getProduct.mockResolvedValue(product({
      signed_in: true, organization: 'Acme', can_install: true,
      entitlement: { release: 'elevation-mask-1.0.0', version: '1.0.0', update_available: true },
    }))
    const el = await mount()
    expect(panel(el).dataset.state).toBe('update')
    expect(buttons(el)).toContain('Update to v1.1.0')
    expect(el.querySelector('[data-installed-note]').textContent).toContain('Installed v1.0.0 in Acme')
    // The versions table marks the installed row and offers Install on the others.
    const rows = [...el.querySelectorAll('[data-release-row]')]
    expect(rows[1].textContent).toContain('Installed')
    const rowInstall = rows[0].querySelector('button')
    expect(rowInstall.textContent.trim()).toBe('Install')
    rowInstall.click()
    await flush()
    expect(installProduct).toHaveBeenCalledWith('elevation-mask', 'elevation-mask-1.1.0')
  })

  it('uninstalls after confirmation', async () => {
    loggedIn.value = true
    getProduct.mockResolvedValue(product({
      signed_in: true, organization: 'Acme', can_install: true,
      entitlement: { release: 'elevation-mask-1.1.0', version: '1.1.0', update_available: false },
    }))
    const el = await mount()
    expect(panel(el).dataset.state).toBe('installed')
    const uninstall = [...panel(el).querySelectorAll('button')].find(b => b.textContent.trim() === 'Uninstall')
    uninstall.click()
    await flush()
    // Dialog content portals into document.body.
    const confirm = [...document.body.querySelectorAll('button')].find(b => b.textContent.trim() === 'Uninstall' && !panel(el).contains(b))
    expect(confirm).toBeTruthy()
    confirm.click()
    await flush()
    expect(uninstallProduct).toHaveBeenCalledWith('elevation-mask')
  })

  it('shows the error when the product is not available', async () => {
    loggedIn.value = false
    getProduct.mockRejectedValue(new Error('Unknown product: elevation-mask'))
    const el = await mount()
    expect(el.textContent).toContain('Unknown product: elevation-mask')
    expect(panel(el)).toBeNull()
  })
})
