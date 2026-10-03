# Proposal: Marketplace

## Why

Plugins are becoming install-only — object detection just left the geospatial
service and now ships as a user plugin package — but the only way for an
organization to get a package today is to obtain the zip out of band and
upload it on the Plugins page. There is no shared catalog, no publisher
identity and nothing that records which organization acquired what. Anyone
evaluating the platform has no way to see what analysis is available before
signing in. A global, free marketplace closes that gap and gives later
product kinds (processing presets, basemaps, models) a home without a second
distribution mechanism.

## What Changes

- A **global listing layer** — `WebODM Publisher`, `WebODM Product`,
  `WebODM Product Release`, `WebODM License`, `WebODM Marketplace Category` —
  kept separate from the per-organization `WebODM Plugin` row, which stays
  the *installed* product.
- **Publishers** are first-party or invited partners, created and curated by
  platform admins in Desk. No sign-up, no self-serve onboarding.
- **Releases** carry an immutable artifact (sha-256, size), a normalized
  manifest, release notes and mandatory license metadata (a link into a
  curated license registry plus free-text notes, e.g. model-weight terms).
- **Artifact kinds** are a small registry (`plugin` today; `preset`,
  `basemap`, `model` declared but not installable) so new kinds add an
  inspector + installer rather than new tables.
- **Entitlement + install**: `WebODM Entitlement` records that an organization
  acquired a product (which release, which installed row). Installing a
  plugin product copies the release artifact into the existing
  `install_user_plugin` seam — the same code path as a manual upload — so the
  installed row is `<org-slug>.<manifest id>`, org-scoped, sandboxed, and
  configurable exactly like today. Uninstall reuses `remove_plugin`.
- **Anonymous download** is a per-product, publisher-controlled switch that
  defaults to off and can only be turned on when every published release's
  license permits redistribution.
- **Frontend**: `/marketplace` (browse + search + filters) and
  `/marketplace/:product` (description, versions, license, docs, media,
  install panel) work signed-out (public chrome) and signed-in (app chrome,
  "Marketplace" tab). Signed-out visitors get a Download button only when the
  publisher allows it; otherwise a sign-in prompt. Landing page links to the
  marketplace.
- **Non-goals:** payments and payouts, open self-serve publishing, ratings and
  reviews, plugin network access, selling data, a publisher UI in the SPA
  (Desk + a `bench execute` helper cover publishing in this version).

## Capabilities

### New Capabilities

- `marketplace` — catalog, publishers, releases, licenses, entitlement and
  install, anonymous download, public browsing.

### Modified Capabilities

- `user-plugins` — the install entrypoint records provenance (`source`,
  `product`, `release`) and removal detaches the entitlement; otherwise
  unchanged.
- `landing-page` — a Marketplace link in the navigation and footer.

## Impact

- **`webodm_core`**: 8 new DocTypes (`webodm_core/webodm_core/doctype/`),
  `webodm_core/marketplace/` package (kinds, catalog, install, publishing),
  `api/marketplace.py`, three new read-only fields on `WebODM Plugin`,
  `install_user_plugin(..., source, product, release)`, permission hooks +
  stamping for `WebODM Entitlement`, seed patch for licenses / categories /
  the first-party publisher.
- **`webodm_frontend`**: `lib/marketplace.js`, `lib/session.js`,
  `lib/markdownLite.js`, pages `Marketplace.vue` / `MarketplaceProduct.vue`,
  `components/PublicShell.vue`, `layout: 'auto'` routing, nav tab, Landing
  links, Plugins page provenance label.
- **Docs**: `docs/marketplace/` (concepts, publishing runbook, install flow),
  `openspec/specs/marketplace/spec.md`, AGENTS.md phase entry.
