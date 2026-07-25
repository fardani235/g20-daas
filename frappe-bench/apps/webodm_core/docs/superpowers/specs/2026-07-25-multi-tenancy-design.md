# Multi-Tenancy Foundation — Design

**Date:** 2026-07-25
**Status:** Approved (design)
**Scope:** Tenancy / isolation model for Frappe WebODM (subsystem A)

## Context & Motivation

WebODM on Frappe currently isolates data **per-user** via Frappe's built-in
`owner` field. `permissions.py` scopes `WebODM Project` and `WebODM Task` to
their `owner`; custom endpoints call `doc.check_permission()` to close the
`get_doc` IDOR gap (fixed in `a808d61`). `WebODM Settings` is a global **Single**
and processing nodes are a global pool — neither is tenant-aware.

The product goal is a SaaS that grows into a full business system (ERP / CRM /
HRMS / LMS integration). That requires a real **tenant** boundary — an
Organization — not just per-user islands.

### Related subsystems (separate specs, NOT in scope here)
- **B. On-demand ephemeral NodeODM provisioning** — spin up a NodeODM instance
  per task, tear down after. Replaces the shared-node pool; removes processing
  nodes from the "shared config" question entirely.
- **C. Object storage (MinIO/S3)** — move images/orthophotos/tiles off local disk.

These are independent of the tenancy decision and each gets its own
spec → plan → build cycle.

## Decisions (locked)

| # | Decision |
|---|----------|
| 1 | **Hardened hybrid tenancy** — shared Frappe site + row-level `organization` stamp now; `organization` column is the clean cut-seam for a future site-per-tenant migration. |
| 2 | **User belongs to exactly one org** (enforced by a unique constraint on membership). |
| 3 | **Flat membership** with a future-proof `role` field (`Owner`/`Member` today; RBAC seam for later). Isolation is keyed on `organization`, independent of role. |
| 4 | **Per-org Settings** — convert the `WebODM Settings` Single to a normal DocType (one row/org). A separate `WebODM Platform Settings` Single holds operator hard caps. |
| 5 | **Explicit org creation + invitations.** No auto-org on signup. Onboarding gate blocks all features until the user has an org; deny-by-default. |
| 6 | **Wipe existing dev data.** Org-stamping is mandatory from day one — no migration patch, no orphaned-doc edge cases. |
| 7 | **Enforcement via Approach B** — stored `organization` field + a single centralized tenant-context chokepoint (`tenancy.py`). |

### Why Approach B (enforcement)
Considered: (A) keep owner-scoping, derive org at query time; (B) stored
`organization` + centralized guard; (C) Frappe User Permissions machinery.
Chose **B** because it is the only option that makes "hardened hybrid" real:
org is a stored column (fast queries + the literal migration seam), isolation is
enforced at one auditable chokepoint, and deny-by-default is structural rather
than conventional. (A) forfeits the migration seam and keeps leak risk
convention-based; (C) reproduces the `get_doc` custom-endpoint gap and fights the
existing custom-hook architecture.

## Section 1 — Data Model

### New: `WebODM Organization`
| Field | Type | Notes |
|---|---|---|
| `organization_name` | Data | Display name (company) |
| `slug` | Data | Unique, URL/API-safe; auto-generated |
| `owner` | Frappe built-in | Creator = org admin |
| `status` | Select | `Active` / `Suspended` (operator lever) |

### New: `WebODM Org Membership`
| Field | Type | Notes |
|---|---|---|
| `user` | Link → User | **Unique** — enforces the one-org rule at the DB layer |
| `organization` | Link → WebODM Organization | |
| `role` | Select | `Owner` / `Member` today; RBAC seam |

### New: `WebODM Org Invitation`
| Field | Type | Notes |
|---|---|---|
| `email` | Data | Invite by email; may precede signup |
| `organization` | Link → WebODM Organization | |
| `token` | Data | Unique |
| `status` | Select | `Pending` / `Accepted` / `Revoked` |
| `expires_on` | Datetime | |

### New: `WebODM Platform Settings` (Single)
Operator-owned hard caps (e.g. `max_file_size_mb`, `max_projects_per_org`).
Per-org Settings values may not exceed these.

### Modified — add `organization` Link field (`reqd`, `read_only`) to:
- `WebODM Project`
- `WebODM Task`
- `WebODM Preset`
- `WebODM Settings` — **Single → normal DocType**, one row per org
  (`organization` unique). Holds soft preferences (default preset, auto-start)
  that fall back to platform defaults when unset.

`Processing Node` is deliberately untouched (becomes ephemeral in subsystem B).

## Section 2 — Tenant Context Resolution (single chokepoint)

### New module: `webodm_core/tenancy.py`
```python
def get_current_org(user=None) -> str | None:
    """Resolve the actor's organization from Org Membership.
    Returns org name, or None if the user has no membership.
    Memoized on frappe.local for the request lifetime."""

def require_org(user=None) -> str:
    """Same, but raises OrgContextError (deny-by-default) when None."""

def is_org_admin(user=None) -> bool:
    """True if the user's membership role is 'Owner'. RBAC seam."""

def is_platform_admin(user=None) -> bool:
    """System Manager / Administrator — bypasses org-scoping (operator)."""
```

### Resolution rules
- **Normal user** → their one membership's `organization`. No membership →
  `None` → deny-by-default → onboarding gate.
- **Platform admin** → `get_current_org` returns `None` but is *not* denied;
  `is_platform_admin` grants cross-tenant access (support/analytics). Makes the
  current "admin sees all" behavior explicit.
- **Suspended org** → `require_org` treats as no-org (deny) without data deletion.
- **Caching** → resolved org memoized on `frappe.local`; user→org lookup once
  per request.

### Errors
`OrgContextError(frappe.PermissionError)` → API returns **403** with a
machine-readable code `no_organization` so the frontend routes to the onboarding
gate instead of a generic error.

**Invariant:** org context is derived in this module and nowhere else. Proving
no-leak = reviewing one file. It is also the seam a future site-per-tenant
migration reads from.

## Section 3 — Enforcement (three layers, all read from `tenancy.py`)

### Layer 1 — Mandatory stamping (`before_insert`)
```python
def stamp_organization(doc, method):
    doc.organization = require_org()   # deny-by-default; no orphan docs
```
Wired in `hooks.py` `doc_events` for Project, Task, Preset, Settings. Field is
`read_only`; org is derived from **who is acting**, never from the payload →
callers cannot spoof another org.

### Layer 2 — Query & doc permission hooks (org-scoped)
`permissions.py` moves from `owner`-based to `organization`-based:
```python
def get_project_permission_query_conditions(user=None):
    if is_platform_admin(user): return None
    org = get_current_org(user)
    if not org: return "1=0"            # deny-by-default: no rows
    return f"`tabWebODM Project`.`organization` = {escape(org)}"

def has_project_permission(doc, ptype, user=None):
    if is_platform_admin(user): return True
    return doc.organization == get_current_org(user)   # membership, not owner
```
Same shape for Task, Preset, Settings. Flat membership: any org member passes;
`owner` is not checked for isolation (kept only as "who created it"). The `"1=0"`
deny makes no-org / wrong-org requests return nothing, structurally.

### Layer 3 — Custom endpoint guards (`get_doc` gap)
Every custom whitelisted endpoint loading a doc by id calls
`doc.check_permission("read"|"write")` (now runs the org-scoped `has_permission`).
Endpoints doing tenant writes (upload, process) also call `require_org()` up front.

### Org lifecycle
- Creating a `WebODM Organization` auto-creates the creator's `Owner` membership
  (`after_insert`).
- Membership `user` uniqueness rejects a second org at the DB layer.
- `Suspended` org → members lose access (deny) without data deletion.

## Section 4 — API & Onboarding

### New module: `webodm_core/api/organization.py` (`@frappe.whitelist`)
| Endpoint | Purpose | Guard |
|---|---|---|
| `create_organization(name)` | Create org + auto Owner membership; fails if caller already has a membership | authenticated, no existing membership |
| `get_my_organization()` | Returns `{organization, role}` or `{organization: null}` | authenticated |
| `invite_member(email)` | Owner invites (creates pending invitation) | `is_org_admin` |
| `accept_invitation(token)` | Invitee joins → Member membership; fails if already in an org, or token expired/revoked | authenticated, no existing membership |
| `list_members()` | Members of caller's org | org member |
| `remove_member(user)` | Owner removes a member | `is_org_admin` |

Invitations are **by email** so a user who hasn't signed up yet can be invited;
acceptance binds to the account once it exists. Delivery via existing Frappe
email; for dev, the endpoint also returns the token directly (no SMTP needed).

### Onboarding gate (frontend `webodm_frontend`)
```
Login → get_my_organization()
         ├─ has org  → normal app
         └─ no org   → /onboarding (blocks all tenant routes)
                        ├─ Create organization
                        └─ Accept pending invitation (token/link)
```
A route guard redirects tenant routes to `/onboarding` when `organization ==
null`. Backend deny-by-default is independent — bypassing the guard still yields
no data. The gate is UX, not the security boundary.

### Existing endpoints touched
- `api/settings.py`, `api/presets.py` → owner-scope → org-scope (read/write the
  caller's org row).
- `api/task.py` / `api/project.py` uploads → call `require_org()`.

## Section 5 — Testing Strategy (TDD, adversarial)

### Fixtures
Two orgs with members: Org A (`owner_a`, `member_a`), Org B (`owner_b`), plus a
`no_org` user. Use a non-admin `WebODM User` role so scoping actually gates
(Administrator bypasses everything and would hide leaks).

### 1. `test_tenancy.py`
- `get_current_org` correct per user; `None` for `no_org`.
- `require_org` raises `OrgContextError` for `no_org` and a `Suspended` org.
- Membership `user` uniqueness rejects a second org.
- Platform admin bypasses (sees all).

### 2. `test_stamping.py`
- New Project/Task/Preset/Settings auto-stamped with the actor's org.
- Payload setting a *different* `organization` is overridden (spoof-proof).
- Insert as `no_org` → denied, no orphan doc created.

### 3. `test_isolation.py` — core acceptance suite
- List queries: `member_a` sees only Org A docs.
- **IDOR by id:** `owner_b` calling every custom endpoint
  (`get_task_progress`, `get_task_console`, `process_task`, `cancel_task`,
  `upload_images`, tiles info/serve/volume, settings, presets) with an Org A doc
  id → `PermissionError`, for read and write.
- `no_org` calling any tenant endpoint → `OrgContextError` (403 `no_organization`).
- Platform admin reaches both orgs.

### 4. `test_organization_api.py`
- Create org → auto Owner membership; second create by same user fails.
- Invite → accept; accept when already in an org fails; expired/revoked token fails.
- `remove_member` only by Owner; Member forbidden.
- Suspending an org revokes access (deny) without deleting data.

**Acceptance gate:** the isolation suite (#3) is the definition of done — the
foundation is not complete until every cross-tenant access is a hard failure.

## Out of Scope (explicit)
- Ephemeral on-demand NodeODM provisioning (subsystem B).
- Object storage migration to MinIO/S3 (subsystem C).
- Full RBAC (Admin/Member/Viewer, per-project sharing) — `role` field is the seam.
- Billing / quotas enforcement beyond platform hard caps.
- Site-per-tenant migration tooling — `organization` column is the designed seam;
  the migration itself is future work.
