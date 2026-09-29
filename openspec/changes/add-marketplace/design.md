# Design: Marketplace

## Context

See `proposal.md` for motivation. Current state that shapes this design:

- `WebODM Plugin` is both catalog and installation: `System` rows are synced
  from the geospatial service, `User` rows are one organization's uploaded
  package, named `<org-slug>.<manifest id>`. Visibility is binary
  (`permissions.get_plugin_permission_query_conditions`,
  `api/plugins._visible_plugin_row`): System = everyone, User = owning org.
- `api/plugins.install_user_plugin(package_path, org)` is the one install
  seam: validates the zip (`plugins/package.inspect_package`), upserts the
  row, stores the package privately, enables it on first install and keeps
  settings/runs on upgrade. It **consumes** the path. `remove_plugin` is the
  symmetric removal seam.
- Tenancy: org = `WebODM Org Membership` (one per user); org admin =
  membership `role == "Owner"`; platform admin = `System Manager` /
  `Administrator`. Every DocType with an `organization` field must be in both
  permission hook maps (`api/test_tenant_doctype_coverage.py`) and is stamped
  from the session by `tenancy_hooks.stamp_organization`.
- `WebODM Preset` `system=1` rows are the existing precedent for
  "platform-global row readable by every tenant, writable only by platform
  admins".
- Frontend routes are either `requiresAuth` (AppLayout) or `layout: false`
  (bare page with its own chrome). Identity comes from
  `webodm_core.api.session.whoami`; per-resource capability travels with the
  resource. `Invoices.vue` (Billing) is mocked data — left alone, no payments.
- A whitelisted method may return a werkzeug `Response`
  (`frappe.handler.execute_cmd`), which is how a large artifact can be
  streamed instead of read into memory.

## Goals / Non-Goals

**Goals:**

- Global, discoverable listings that signed-out visitors can browse.
- Publisher identity for first-party and invited partners.
- Versioned, immutable releases with mandatory license metadata.
- One-click install into the caller's organization through the existing
  install seam; installs/config/runs stay org-scoped.
- Artifact kinds extensible without a rewrite.
- Publisher-controlled anonymous download, gated by the license.

**Non-Goals:**

- Payments, entitlement expiry, seats, payouts.
- Self-serve publisher onboarding or a publisher UI in the SPA.
- Ratings/reviews, download analytics.
- Any change to the sandbox trust boundary (no network for plugins).
- Installing presets/basemaps/models (kinds are declared, installers are not).

## Decisions

### D1: A listing layer separate from the installed row

**Decision:** New global DocTypes — `WebODM Publisher`, `WebODM Product`,
`WebODM Product Release`, `WebODM License`, `WebODM Marketplace Category`
(+ child tables `WebODM Product Category`, `WebODM Product Media`). None has
an `organization` field. The per-organization `WebODM Plugin` row remains
the installed product and gains three read-only provenance fields: `source`
(`Upload` | `Marketplace`), `product`, `release`. A new org-scoped
`WebODM Entitlement` (organization, product → release, plugin, status) is
the join.

**Rationale:** Reusing `WebODM Plugin` for listings would have needed a third
`plugin_type` and touched every visibility check (four call sites plus the
controller's `validate`, which nulls `package` for non-User rows). Keeping the
catalog in its own tables leaves the plugin isolation rules untouched and
lets non-plugin kinds exist without a plugin row.

**Alternatives:** `plugin_type = "Marketplace"` on `WebODM Plugin` with
shared rows across orgs — rejected: settings/runs/removal are keyed on the
plugin row and would leak across tenants (`remove_plugin` deletes every
setting for a row).

### D2: Publishers are curated in Desk; no self-serve

**Decision:** `WebODM Publisher` has `kind` (`First-party` | `Partner`),
`status` (`Active` | `Suspended`), contact/website/description. Platform
admins create publishers, products and releases in Frappe Desk (admin
subdomain) or with `bench execute
webodm_core.marketplace.publishing.publish_release`. Partners hand artifacts
to the platform team. A suspended publisher's products disappear from the
catalog and cannot be installed.

**Rationale:** The brief rules out open sign-up and self-serve onboarding, and
"platform admins publish and curate". Desk already exists for admin CRUD,
so a second publishing UI would be duplicated effort for a v1 with a handful
of partners.

**Alternatives:** a publisher-members table with `has_permission` hooks so
partners edit their own products — deferred; the schema doesn't preclude it.

### D3: Releases are immutable once published

**Decision:** `WebODM Product Release` is named `<product>-<version>`.
`version` is immutable after insert. On save the controller resolves the
`artifact` Attach (must be a **private** File that exists on disk), computes
`artifact_hash` (sha-256) and `artifact_size`, and runs the kind's inspector
(`plugin` → `package.inspect_package`, stores the normalized `manifest`,
requires `manifest.version == version` and the same `manifest.id` as the
product's other releases). Once `status` has been `Published`, `artifact`,
`artifact_hash`, `manifest` and `license` cannot change; only `status`
(`Published` ↔ `Yanked`), `release_notes` and `license_notes` can. Yanked
releases stay visible on the product page (marked), are not installable,
and existing installs keep working.

**Rationale:** An immutable hash per version is what lets an organization
trust that "1.2.0" is the same bytes everywhere and what the install path
verifies against the copy it makes.

### D4: License registry with a redistribution flag

**Decision:** `WebODM License` (`license_id` such as `MIT`, `Apache-2.0`,
`AGPL-3.0`, `CC-BY-NC-4.0`, `Etalab-2.0`, `Proprietary`) carries `name`,
`url`, `permits_redistribution` and a one-line summary. Every release links
one (`reqd`) and may add `license_notes` (e.g. "model weights: DeepForest,
MIT"). `WebODM Product.allow_anonymous_download` defaults to off and its
`validate` throws when any Published release's license has
`permits_redistribution = 0`; the release controller throws symmetrically
when publishing a non-redistributable release under a product that has the
flag on.

**Rationale:** Putting the redistribution judgement on the curated license
row (not a free checkbox per release) means a publisher cannot flip it by
accident, and the platform surfaces terms honestly — the product page shows
the license of each version and the notes.

### D5: Install goes through `install_user_plugin`; entitlement is the record

**Decision:** `api/marketplace.install_product(product, release=None)`:
org-admin gate (`_require_org_admin`, same as upload) → product Published &
publisher Active → release Published (default: latest) → kind installable →
copy the artifact into the plugin spool dir, verify sha-256 against
`artifact_hash` → `plugins_api.install_user_plugin(path, org,
source="Marketplace", product=..., release=...)` → upsert
`WebODM Entitlement` (Active, release, plugin, installed_by/on). Upgrading =
installing a newer release (the seam upgrades in place and keeps
settings/runs). `uninstall_product` → `plugins_api.remove_plugin` →
entitlement `Removed`. Removing the plugin from the Plugins page, or manually
re-uploading a package over the same id, also detaches the entitlement
(`marketplace.install.detach_plugin`), so "Installed" on the product page
means "the current row came from this product".

**Rationale:** The brief asks for one install seam. `install_user_plugin`
already produces the right row name, storage, enablement and permissions.
The artifact is *copied* because the seam moves its input and the release
artifact must stay canonical.

**Alternatives:** sharing one package File across orgs — rejected; per-org
copies keep `remove_plugin`'s attachment deletion correct and cost one zip
per install.

### D6: Artifact kinds are a registry

**Decision:** `marketplace/kinds.py` exposes `KINDS = {"plugin", "preset",
"basemap", "model"}`, `inspect(kind, path) -> dict` (plugin → manifest;
others → `{}`), `is_installable(kind)` and `install(kind, release, org)`.
`WebODM Product.validate` refuses to set `status = Published` for a kind
without an installer ("cannot be published yet"), so the catalog never shows
something that cannot be acquired.

### D7: Public reads through guest-allowed endpoints only

**Decision:** `list_products` / `get_product` / `download_release` are
`allow_guest=True` and query with `ignore_permissions=True`, filtering to
`status = Published`, publisher `Active`, and Published/Yanked releases.
DocType role permissions grant `WebODM User` read (so Desk/`/api/resource`
list views work for signed-in users) and nothing to Guest. `get_product`
adds a `viewer` block for signed-in callers: `signed_in`, `organization`,
`can_install` (org admin or platform admin *with* an org), `entitlement`
(installed release/version/plugin, `update_available`). Download for a Guest
requires `allow_anonymous_download`; for signed-in users any Published
release. The response is `werkzeug.utils.send_file(..., as_attachment=True,
conditional=True)` so a 256 MB zip streams with range support.

### D8: Frontend `layout: 'auto'`

**Decision:** New route meta value `layout: 'auto'`. `lib/session.js` holds a
`loggedIn` ref refreshed by the router guard (for `requiresAuth` and `auto`
routes) before entering; `App.vue` renders `AppLayout` when `auto` and
signed in, otherwise the bare page, which wraps itself in
`components/PublicShell.vue` (brand, Marketplace/About/Sign in links,
footer). No org check on marketplace routes — signed-in members without an
org can browse; the install panel explains why they cannot install.

### D9: Search is client-side over the fetched catalog

**Decision:** The API accepts `q`, `category`, `kind` (Python-side filtering;
the catalog is small), but the browse page fetches once and filters locally
with pure helpers in `lib/marketplace.js` (`filterProducts`), keeping typing
snappy and unit-testable.

### D10: Markdown without a dependency

**Decision:** `lib/markdownLite.js` renders a safe subset (headings, lists,
bold/italic/code, fenced code, http(s)/mailto/relative links) after escaping
HTML. Product `description` and `docs` are Markdown Editor fields in Desk.

## Risks

- [Desk attach timing] Desk uploads the Attach for an unsaved doc against
  the temporary `new-<doctype>-…` name; `Document.insert` then runs
  `relink_mismatched_files`, which moves that File to the real name (verified
  over HTTP with `upload_file` + `savedocs`). At `validate` time the File is
  therefore not yet attached to the release → the controller resolves the
  artifact by `file_url` and requires `is_private = 1`; only platform admins
  can write releases, so the "attach field points at someone else's file"
  concern from `files.file_doc_for_url` does not apply here. Downloads and
  installs still go through `file_doc_for_url` (attachment required).
- [Large artifacts in memory] avoided via `send_file` (D7) and streaming
  copy + hash on install.
- [Guest endpoints] no writes happen on guest paths; `install_count` is
  computed from entitlements on read.
- [Coverage guard] `WebODM Entitlement` is added to both hook maps and to
  `doc_events` for stamping; the new global doctypes have no `organization`
  field so the guard ignores them by design.
