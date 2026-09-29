import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as market from './marketplace.js'

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve({ message: { products: [] } }) }))
  global.window = { csrf_token: 'x' }
})

describe('API calls', () => {
  it('lists products with optional filters as query params', async () => {
    await market.listProducts()
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/method/webodm_core.api.marketplace.list_products',
      expect.objectContaining({ method: 'GET' }),
    )
    await market.listProducts({ q: 'tree crowns', category: '', kind: 'plugin' })
    const url = global.fetch.mock.calls[1][0]
    expect(url).toContain('q=tree%20crowns')
    expect(url).toContain('kind=plugin')
    expect(url).not.toContain('category=')
  })

  it('fetches a product page by id', async () => {
    await market.getProduct('object detection')
    expect(global.fetch.mock.calls[0][0]).toBe(
      '/api/method/webodm_core.api.marketplace.get_product?product=object%20detection',
    )
  })

  it('installs with or without an explicit release and sends the CSRF token', async () => {
    await market.installProduct('p1')
    let [url, opts] = global.fetch.mock.calls[0]
    expect(url).toContain('install_product')
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-Frappe-CSRF-Token']).toBe('x')
    expect(JSON.parse(opts.body)).toEqual({ product: 'p1' })

    await market.installProduct('p1', 'p1-1.2.0')
    ;[url, opts] = global.fetch.mock.calls[1]
    expect(JSON.parse(opts.body)).toEqual({ product: 'p1', release: 'p1-1.2.0' })

    await market.uninstallProduct('p1')
    expect(global.fetch.mock.calls[2][0]).toContain('uninstall_product')
  })

  it('surfaces Frappe error messages', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false,
      json: () => Promise.resolve({ _server_messages: JSON.stringify([JSON.stringify({ message: 'Only organization admins can install marketplace products' })]) }),
    }))
    await expect(market.installProduct('p1')).rejects.toThrow('Only organization admins')
  })
})

const products = [
  {
    product_id: 'object-detection', title: 'Object Detection', summary: 'Find trees and vehicles',
    artifact_kind: 'plugin', publisher: { display_name: 'G20 Tech' },
    categories: [{ category_id: 'detection', label: 'Object detection' }, { category_id: 'vegetation', label: 'Vegetation' }],
  },
  {
    product_id: 'terrain-basemap', title: 'Terrain basemap', summary: 'Shaded relief tiles',
    artifact_kind: 'basemap', publisher: { display_name: 'MapCo' }, categories: [{ category_id: 'terrain', label: 'Terrain' }],
  },
]

describe('filterProducts', () => {
  it('returns everything for empty filters', () => {
    expect(market.filterProducts(products, {})).toHaveLength(2)
    expect(market.filterProducts(undefined, {})).toEqual([])
  })

  it('matches every term across title, summary, publisher and categories', () => {
    expect(market.filterProducts(products, { q: 'trees' }).map(p => p.product_id)).toEqual(['object-detection'])
    expect(market.filterProducts(products, { q: 'mapco relief' }).map(p => p.product_id)).toEqual(['terrain-basemap'])
    expect(market.filterProducts(products, { q: 'Vegetation' })).toHaveLength(1)
    expect(market.filterProducts(products, { q: 'trees relief' })).toHaveLength(0)
  })

  it('filters by category and kind exactly', () => {
    expect(market.filterProducts(products, { category: 'terrain' }).map(p => p.product_id)).toEqual(['terrain-basemap'])
    expect(market.filterProducts(products, { kind: 'plugin' }).map(p => p.product_id)).toEqual(['object-detection'])
    expect(market.filterProducts(products, { kind: 'plugin', category: 'terrain' })).toEqual([])
  })
})

describe('installState', () => {
  const latest = { name: 'p-1.1.0', version: '1.1.0' }
  const base = { latest_release: latest }

  it('distinguishes guests, org-less users and members', () => {
    expect(market.installState(null)).toBe('guest')
    expect(market.installState({ ...base, viewer: { signed_in: false } })).toBe('guest')
    expect(market.installState({ ...base, viewer: { signed_in: true, organization: null } })).toBe('no-org')
    expect(market.installState({ ...base, viewer: { signed_in: true, organization: 'Org', can_install: false } })).toBe('member')
  })

  it('offers install, installed or update to admins from the entitlement', () => {
    const admin = { signed_in: true, organization: 'Org', can_install: true }
    expect(market.installState({ ...base, viewer: { ...admin, entitlement: null } })).toBe('install')
    expect(market.installState({ ...base, viewer: { ...admin, entitlement: { release: 'p-1.1.0', update_available: false } } })).toBe('installed')
    expect(market.installState({ ...base, viewer: { ...admin, entitlement: { release: 'p-1.0.0', update_available: true } } })).toBe('update')
  })

  it('is unavailable without a release unless already installed', () => {
    const admin = { signed_in: true, organization: 'Org', can_install: true }
    expect(market.installState({ latest_release: null, viewer: { ...admin } })).toBe('unavailable')
    expect(market.installState({ latest_release: null, viewer: { ...admin, entitlement: { release: 'x' } } })).toBe('installed')
  })
})

describe('downloadPolicy', () => {
  it('follows the server-provided download_url', () => {
    expect(market.downloadPolicy({ latest_release: { download_url: '/api/x' }, viewer: {} }))
      .toEqual({ canDownload: true, url: '/api/x', reason: null })
  })

  it('tells a guest to sign in when the publisher has not opted in', () => {
    expect(market.downloadPolicy({ latest_release: { download_url: null }, viewer: { signed_in: false } }).reason).toBe('sign-in')
    expect(market.downloadPolicy({ latest_release: { download_url: null }, viewer: { signed_in: true } }).reason).toBe('unavailable')
    expect(market.downloadPolicy({ latest_release: null }).reason).toBe('no-release')
  })
})

describe('formatting helpers', () => {
  it('labels kinds, shortens hashes and builds routes', () => {
    expect(market.kindLabel('plugin')).toBe('Analysis plugin')
    expect(market.kindLabel('mystery')).toBe('mystery')
    expect(market.shortHash('abcdef0123456789abcdef')).toBe('abcdef012345…')
    expect(market.shortHash(null)).toBe('')
    expect(market.productRoute('a b')).toBe('/marketplace/a%20b')
  })

  it('formats Frappe datetimes and tolerates junk', () => {
    expect(market.formatVersionDate('2026-09-29 10:00:00')).toMatch(/2026/)
    expect(market.formatVersionDate('')).toBe('')
    expect(market.formatVersionDate('not a date')).toBe('not a date')
  })
})
