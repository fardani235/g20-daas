# Marketplace

The marketplace is a global, free catalog of digital products — analysis
plugins today, other artifact kinds later — published by the platform and by
invited partners. Anyone can browse it, signed in or not. An organization
admin installs a product into their organization in one click; from then on
it is an ordinary org-scoped plugin (configured on the Plugins page, run from
a task, executed in the sandbox).

| | Where | Who |
|---|---|---|
| Browse, search, product pages | `/marketplace`, `/marketplace/<product>` (SPA, public) | Everyone |
| Install / update / uninstall | Product page install panel | Organization admins (membership role `Owner`) and platform admins with an organization |
| Publish and curate | Frappe Desk (admin subdomain) or `bench execute` | Platform admins (`System Manager`) |
| Download the artifact | Product page | Signed-in users always; signed-out visitors only when the publisher allows it |

Design record: [`openspec/changes/add-marketplace/design.md`](../../openspec/changes/add-marketplace/design.md).
Spec: [`openspec/specs/marketplace/spec.md`](../../openspec/specs/marketplace/spec.md).
Publishing runbook: [`publishing.md`](publishing.md).

---

## 1. Concepts

```
WebODM Publisher ──< WebODM Product ──< WebODM Product Release ──> WebODM License
   (global)             (global)              (global, immutable)      (registry)
                           │
                           └──< WebODM Entitlement ──> WebODM Plugin  (per organization)
```

- **Publisher** — `First-party` (the platform) or `Partner` (invited). Created
  by platform admins; there is no sign-up. `Suspended` hides every product
  of the publisher and blocks installs; existing installs keep working.
- **Product** — the listing: id (URL slug), title, summary, Markdown
  description and docs, links, icon, media, categories, artifact kind and the
  `allow_anonymous_download` switch. `Draft` → `Published` → `Retired`.
- **Release** — one version of a product: a private artifact with its sha-256
  and size, release notes, and **mandatory license metadata**. For plugins
  the artifact is validated exactly like an uploaded package and its
  normalized manifest is stored. `Draft` → `Published` → `Yanked` (no new
  installs, existing ones untouched). Once published, the artifact, hash,
  manifest and license are frozen — publish a new version instead.
- **License** — a curated registry (`MIT`, `Apache-2.0`, `AGPL-3.0`,
  `CC-BY-NC-4.0`, `Etalab-2.0`, `Proprietary`, …). Each row carries
  `permits_redistribution`, the platform's judgement that gates anonymous
  downloads. Releases may add `license_notes` for terms worth surfacing
  beyond the license itself — model weights especially.
- **Entitlement** — "organization X acquired product Y": which release is
  installed and which `WebODM Plugin` row it produced. Org-scoped like every
  tenant-owned record (permission hooks + session stamping). `Active` or
  `Removed`.
- **Artifact kind** — `plugin` is installable. `preset`, `basemap` and `model`
  are declared in `webodm_core/marketplace/kinds.py` so products can name
  them, but a product of a kind without an installer cannot be published.
  Adding a kind means adding an inspector and an installer to that registry,
  not new tables.

## 2. Roles and isolation

- **Browsing is global.** The public endpoints
  (`webodm_core.api.marketplace.list_products` / `get_product` /
  `download_release`) allow guests and only ever return Published products of
  Active publishers with Published or Yanked releases. Nothing
  organization-specific is included for guests. Signed-in callers get a
  `viewer` block: `signed_in`, `organization`, `can_install`, and for a
  product the organization's `entitlement` (installed version,
  `update_available`).
- **Installing is per organization.** `install_product` requires an
  organization admin — the same gate as uploading a plugin. The result is a
  `WebODM Plugin` row named `<org-slug>.<manifest id>` with
  `source = Marketplace`, visible only to that organization, enabled on first
  install, configured and run exactly like an uploaded plugin, in the same
  sandbox with the same limits and no network.
- **Publishing is platform-admin only.** Role permissions on the catalog
  DocTypes grant `WebODM User` read; writes need `System Manager`. Partners
  hand artifacts to the platform team in this version.

## 3. License policy

Every release links a license; a release without one cannot be saved. The
product page shows the license (name, one-line summary, link to the full
text) and the notes of each version.

`allow_anonymous_download` on a product defaults to off. Turning it on is
refused while any Published release's license has
`permits_redistribution = 0`, and publishing such a release under a product
with the switch on is refused too — the two rules keep the invariant from
both sides. Signed-in users can always download a Published release.

## 4. Install flow

1. Admin clicks *Install* (or *Update to vX*) on the product page.
2. `marketplace.install.install_product` checks product/publisher/release
   state, copies the release artifact into the plugin spool and verifies its
   sha-256 against the release.
3. The kind's installer runs — for plugins
   `api.plugins.install_user_plugin(path, org, source="Marketplace",
   product=…, release=…)`, the single install seam. Re-installing a newer
   release upgrades the row in place and keeps the organization's enablement,
   settings and run history.
4. The entitlement is upserted (`Active`, release, plugin, installed_by/on).

Uninstall (product page or Plugins page *Remove*) goes through
`api.plugins.remove_plugin`, which deletes runs, settings, package and row and
marks the entitlement `Removed`. Manually uploading a package with the same
manifest id over a marketplace install turns the row back into a plain upload
and closes the entitlement, so *Installed* on a product page always means
"the current row is this product".

## 5. API

| Method | Guest | Purpose |
|---|---|---|
| `GET webodm_core.api.marketplace.list_products?q=&category=&kind=` | yes | `{products, categories, viewer}` |
| `GET webodm_core.api.marketplace.get_product?product=` | yes | Full page data incl. `releases`, `download_url` per release, `viewer` |
| `GET webodm_core.api.marketplace.download_release?release=` | policy | Streams the artifact (`Content-Disposition: attachment`, range support) |
| `POST webodm_core.api.marketplace.install_product {product, release?}` | no | Org admin; latest Published release by default |
| `POST webodm_core.api.marketplace.uninstall_product {product}` | no | Org admin |
| `GET webodm_core.api.marketplace.my_entitlements` | no | Caller's organization's active entitlements by product |

## 6. Frontend

- Routes `/marketplace` and `/marketplace/:product` use `meta.layout: 'auto'`:
  inside `AppLayout` (with a *Marketplace* tab) when signed in, wrapped in
  `components/PublicShell.vue` otherwise. `lib/session.js` holds the signed-in
  state the router refreshes before entering these routes.
- Browse page: search + kind filter + category chips, filtered client-side
  (`lib/marketplace.js: filterProducts`).
- Product page: overview (Markdown via `lib/markdownLite.js`, HTML-escaped
  subset), versions table (status, license, size, hash, per-version install /
  download), license card, docs/links, media, plugin details from the
  manifest, and the install panel whose state comes from `installState()`:
  `guest` → *Sign in to install* (+ *Download* only when the publisher allows
  it), `no-org`, `member`, `install`, `installed`, `update`.
- Plugins page: marketplace-installed rows show type *Marketplace*; a
  *Marketplace* button links to the catalog. The landing page links to
  `/marketplace` in its nav, mobile menu and footer.

## 7. Non-goals in this version

Payments and payouts, open self-serve publishing, ratings and reviews, plugin
network access, selling data, download analytics. The Billing page remains
mocked data and is unrelated to the marketplace.
