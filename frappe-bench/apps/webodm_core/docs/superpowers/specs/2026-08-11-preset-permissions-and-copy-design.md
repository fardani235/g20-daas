# Preset Permissions & Copy — Design

**Date:** 2026-08-11
**Status:** Approved (design)
**Scope:** `WebODM Preset` permission surfacing + copy action; `whoami` session endpoint
**Repos:** `webodm_core` (backend), `webodm_frontend` (UI)

## Context & Motivation

The reported problem: *"system-scoped presets can be edited by anyone."*

Investigation shows the **backend is already correct**. `api/presets.py` gates
every mutation on `tenancy.is_platform_admin()`:

- `save()` throws `PermissionError` when a non-admin creates (`presets.py:89`) or
  edits (`presets.py:96`) a `system=1` preset.
- `delete()` throws for a non-admin deleting a system preset (`presets.py:123`).
- Non-system presets are org-scoped: edits and deletes require
  `doc.organization == tenancy.get_current_org()` (`presets.py:98`, `:125`).

The actual defect is in the **UI**. `pages/Presets.vue` renders Edit and Delete
buttons on every row unconditionally (`Presets.vue:39-52`), including System
presets. A regular user clicks Edit on a system preset, fills in the dialog,
hits Save — and only then gets an error toast. The affordance lies about what
is permitted.

The root cause is that the frontend has **no notion of the caller's identity or
capabilities**. There is no boot payload, no user store, no session endpoint —
so the page cannot gate anything. This spec closes that gap in a way that keeps
the permission rules on the server.

Secondly, there is no way to duplicate a preset. Copying a curated System preset
into an editable org-scoped one is the common workflow this blocks.

## Decisions (locked)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Non-system presets stay organization-scoped.** No per-user ownership tier. | Matches the existing tenancy model (spec `2026-07-25-multi-tenancy-design.md`); "user-scoped" in the report meant "non-system". |
| 2 | **"Administrator" means `tenancy.is_platform_admin()`** — System Manager *or* Administrator role. Unchanged. | Consistent with org/task/settings enforcement across the app. Locking to the literal `Administrator` user would make system presets unmanageable from named admin accounts and break the seeding patch's assumptions. |
| 3 | **Per-row capability flags** on `list_presets()` drive UI gating. | Server stays the single source of permission truth; the UI cannot drift from it — which is exactly the drift that caused this bug. |
| 4 | **Copy = pre-filled create dialog**, not a new endpoint. | Reuses the existing dialog and option form; posts through `save()` with `name=null`. Users usually copy in order to tweak, so committing on Save (not on click) is the right moment. |
| 5 | **A `whoami` endpoint is added** and used for admin-only UI, alongside (not instead of) the per-row flags. | Identity/context is a page-independent concern; per-resource capability is not. |
| 6 | **Creator display on Projects/Tasks is out of scope.** | Real gap, different feature, different pages — gets its own spec. See Out of Scope. |

### Why per-row flags over a client-side rule

Three options were considered:

1. **Per-row `can_write`/`can_delete` from `list_presets()`** (chosen) — the rule
   is computed by the same helper the mutating endpoints call, so flags and
   enforcement cannot diverge. One round trip, no new endpoint for the gating.
2. **`whoami` + re-implement the rule in JS** — duplicates permission logic on
   the client. That duplication *is* the present bug in another form.
3. **Extend `get_my_organization()` with `is_admin`** — smallest diff, but the
   rule still lives in JS and the endpoint grows a responsibility it doesn't own.

`whoami` is still added (decision 5), but it carries **identity, not
permissions** — the one thing `Presets.vue` reads from it is
`is_platform_admin`, to decide whether the *System preset* toggle exists.

## Section 1 — Backend: capability flags on `list_presets()`

The return shape stays a **flat list of rows** — no envelope change, so
`lib/presets.js` and `lib/presets.test.js` need no structural update. Each row
gains two booleans.

Extract the rule into one module-level helper in `api/presets.py`:

```python
def _can_modify(system, organization, user=None):
    """True when `user` may write or delete a preset with this scope.

    Single source of truth: list_presets() surfaces this as UI hints and
    save()/delete() enforce it. Accepts loose fields so it works for both a
    frappe.get_all row (dict) and a loaded Document.
    """
    if tenancy.is_platform_admin(user):
        return True
    if int(system or 0):
        return False
    return bool(organization) and organization == tenancy.get_current_org(user)
```

`list_presets()` sets the flags in its existing decode loop:

```python
for r in rows:
    r["options"] = _decode_options(r.get("options"))
    writable = _can_modify(r.get("system"), r.get("organization"))
    r["can_write"] = writable
    r["can_delete"] = writable
```

`can_write` and `can_delete` are separate fields despite being identical today.
They are the natural extension point if delete later diverges (e.g. "cannot
delete a preset referenced by a running task"), and consumers reading
`can_delete` will not need to change when it does.

`save()` and `delete()` are refactored to call `_can_modify()` in place of their
inline checks. **Their behaviour is unchanged** — same `PermissionError`, same
messages. The flags are advisory UI hints; enforcement remains server-side and
is not weakened. A client that ignores the flags and POSTs anyway still gets
thrown at.

The `system and not is_platform_admin()` guard on the *create* path
(`presets.py:89`) stays as its own explicit check — it validates a requested
scope rather than an existing row's scope.

## Section 2 — Backend: `whoami`

New module `webodm_core/api/session.py`:

```python
import frappe
from frappe.utils import get_fullname
from webodm_core import tenancy


@frappe.whitelist(allow_guest=False)
def whoami():
    """Identity + tenant context for the calling user.

    Deliberately carries identity, NOT per-resource permissions — capability
    for a given record travels with that record (see presets.list_presets).
    """
    user = frappe.session.user
    row = frappe.db.get_value("WebODM Org Membership", {"user": user},
                              ["organization", "role"], as_dict=True)
    return {
        "user": user,
        "full_name": get_fullname(user),
        "is_platform_admin": tenancy.is_platform_admin(),
        "organization": row.organization if row else None,
        "org_role": row.role if row else None,
    }
```

Read-only, no side effects, callable by any authenticated user about themselves
only (no `user` argument — it always reports the session user, so it cannot be
used to enumerate others).

This overlaps `api/organization.get_my_organization()`, which returns a subset
(`organization`, `role`). That endpoint is **left in place**; migrating its call
sites is unrelated churn. Noted as a follow-up.

## Section 3 — Frontend: `Presets.vue`

**Button gating.** Edit renders only when `p.can_write`; Delete only when
`p.can_delete`. A regular user viewing a System preset sees only Copy. The
misleading affordance is gone.

**Copy.** A Copy button renders on **every** row, for every user — copying reads
the source and writes a new record, so it needs no permission on the source
beyond visibility. `openCopy(p)`:

- loads the ODM option catalog (same as `openCreate`/`openEdit`),
- seeds `values` from the source preset's options,
- sets `draft.preset_name = \`${p.preset_name} (copy)\``,
- sets `draft.system = 0`,
- sets `editing = null` — so `onSave` posts `name: null` and **creates**.

No new backend endpoint. The result is an org preset unless an admin flips the
toggle before saving.

**System toggle.** A "System preset" checkbox renders in the dialog only when
`whoami.is_platform_admin`. Bound to `draft.system`, passed through as the
`system` argument. Initialized from the source on edit, `0` on create and copy.
This closes an existing gap: the UI currently has no way to author a system
preset at all — they exist only via `patches/seed_system_presets.py`.

**Data loading.** `whoami()` is fetched once on mount, in parallel with
`listPresets()`. Admin state is held as a single `ref`.

**Name collisions.** The DocType uses `autoname: field:preset_name`
(`webodm_preset.json:3`), so preset names are unique site-wide. Saving a copy
whose name already exists throws a duplicate error, surfaced by the existing
error toast in `onSave`. `" (copy)"` makes a first collision unlikely; a *second*
copy of the same source will collide and the user renames. This is intentional —
a plain error beats silently auto-incrementing to a name the user did not choose.

**New lib wrapper** in `lib/presets.js`:

```js
export const whoami = () => get('webodm_core.api.session.whoami')
```

(Placed here rather than in a new `lib/session.js` to avoid a third copy of the
`headers`/`unwrap`/`get`/`post` helpers already duplicated between `presets.js`
and `organization.js`. Consolidating those helpers is a known follow-up.)

## Section 4 — Testing

**Backend — `api/test_presets.py`** (extend):

- Admin gets `can_write=True` on a system row.
- Non-admin member gets `can_write=False` on a system row.
- Member gets `can_write=True` on their own org's row.
- Member gets `can_write=False` on another org's row — uses the existing
  two-org fixture in `TestPresetOrgIsolation`.
- **Enforcement still holds:** `save()` on a system preset as a non-admin still
  raises `PermissionError`. This proves the flags did not replace enforcement.

**Backend — `api/test_session.py`** (new):

- `whoami()` as an org member returns that org, role, and
  `is_platform_admin=False`.
- `whoami()` as Administrator returns `is_platform_admin=True`.

**Frontend — `lib/presets.test.js`** (extend): the `whoami` wrapper calls the
right method with GET.

**Frontend — `pages/Presets.test.js`** (new): button visibility *is* the feature,
so it is tested directly.

- System row + non-admin → no Edit, no Delete, Copy present.
- System row + admin → Edit and Delete present.
- Own-org row + non-admin → Edit and Delete present.
- Copy submits with `name: null` and a `" (copy)"` suffixed name.
- System toggle absent for a non-admin, present for an admin.

## Out of Scope

- **Creator/owner display on Projects and Tasks.** Frappe already stamps `owner`,
  `creation`, `modified`, and `modified_by` on every DocType as real columns, and
  `Projects.vue:277` already fetches `fields=["*"]` — so `owner` reaches the
  browser today and is simply never rendered. This is a display gap, not a
  missing-data gap: no schema change or backfill is needed. Surfacing it spans
  multiple pages and raises its own questions (which columns, avatars, task
  detail view), so it gets its own spec.
- **The redundant `owner` DocField on `WebODM Preset`**
  (`webodm_preset.json:24-29`). It shadows Frappe's built-in `owner` column and
  is set manually in `save()` (`presets.py:108`). Removing it is a schema change
  with migration implications, unrelated to this feature.
- **Consolidating `get_my_organization()` into `whoami`**, and de-duplicating the
  fetch helpers shared by `lib/presets.js` and `lib/organization.js`.
- **Per-user (personal) preset scope.** Explicitly rejected — decision 1.
