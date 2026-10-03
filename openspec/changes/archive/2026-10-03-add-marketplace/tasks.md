# Tasks: Marketplace

## 1. Data model: listing layer

- [x] 1.1 Add DocTypes `WebODM License`, `WebODM Marketplace Category`,
  `WebODM Publisher`, `WebODM Product` (+ child `WebODM Product Category`,
  `WebODM Product Media`), `WebODM Product Release`, `WebODM Entitlement`,
  and verify `bench migrate` creates them on the throwaway site.
- [x] 1.2 Add `source` / `product` / `release` to `WebODM Plugin` and keep
  `validate` nulling them for System rows, and verify with the existing
  user-plugin tests.
- [x] 1.3 Wire `WebODM Entitlement` into both permission hook maps and the
  stamping `doc_events`, and verify `test_tenant_doctype_coverage` passes.

## 2. Controllers and kinds

- [x] 2.1 `marketplace/kinds.py` registry (inspect / is_installable / install),
  and verify a `basemap` product cannot be Published.
- [x] 2.2 `WebODM Product Release.validate`: artifact resolution, hash/size,
  manifest inspection, version/id consistency, immutability after publish,
  license-vs-anonymous-download rule, and verify each with tests.
- [x] 2.3 `WebODM Product.validate`: slug, publisher state, kind
  installability, anonymous-download license rule, and verify with tests.

## 3. Catalog, install, publishing, API

- [x] 3.1 `marketplace/catalog.py` serializers + listing/search, and verify
  guests see Published-only data and no org data.
- [x] 3.2 `marketplace/install.py`: install (copy + sha-256 verify →
  `install_user_plugin`), entitlement upsert, uninstall, `detach_plugin`, and
  verify install/upgrade/uninstall/member-denied/other-org isolation.
- [x] 3.3 `marketplace/publishing.py::publish_release` for `bench execute`,
  and verify it round-trips a zip into a Published release.
- [x] 3.4 `api/marketplace.py`: `list_products`, `get_product`,
  `install_product`, `uninstall_product`, `download_release` (streamed;
  guest gate), and verify the guest/anonymous-download matrix.
- [x] 3.5 Hook `remove_plugin` and manual re-upload into `detach_plugin`, and
  verify the entitlement flips to Removed.
- [x] 3.6 Seed patch (licenses, categories, first-party publisher) listed in
  `patches.txt` and called from `install.seed_defaults`, and verify
  idempotency.

## 4. Frontend

- [x] 4.1 `lib/session.js`, `layout: 'auto'` in router + `App.vue`, and verify
  with a unit test of the layout decision helper.
- [x] 4.2 `lib/marketplace.js` (API calls + pure helpers: `filterProducts`,
  `installState`, `canInstall`, `downloadPolicy`), and verify with vitest.
- [x] 4.3 `lib/markdownLite.js` safe renderer, and verify escaping + syntax
  with vitest.
- [x] 4.4 `components/PublicShell.vue`, pages `Marketplace.vue` and
  `MarketplaceProduct.vue`, routes, nav tab (Store icon) and `nav.test.js`
  update, and verify with a mounted page test for the install panel states.
- [x] 4.5 Landing page Marketplace links (desktop nav, mobile, footer) and
  Plugins page "Marketplace" provenance label + link, and verify `npm test`
  and `npm run build`.

## 5. Docs

- [x] 5.1 `docs/marketplace/README.md` (concepts, roles, license policy,
  install flow), `docs/marketplace/publishing.md` (Desk + bench execute
  runbook), and verify links resolve.
- [x] 5.2 `openspec/specs/marketplace/spec.md`, AGENTS.md Phase 18, SPEC.md
  §6 refresh, TODO.md, and verify the change is self-consistent.
