# Preset Permissions & Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Presets UI show only the actions a user may actually perform, let admins author system presets, and let anyone copy a preset into an editable org-scoped one.

**Architecture:** The backend already enforces the rules correctly — this work surfaces them. `list_presets()` gains per-row `can_write`/`can_delete` booleans computed by a new `_can_modify()` helper that `save()` and `delete()` also call, so UI hints cannot drift from enforcement. A new `whoami` endpoint supplies identity (used only to gate the admin-only System toggle). Copy reuses the existing create dialog pre-filled, posting through the existing `save()` with `name=null` — no new endpoint.

**Tech Stack:** Frappe (Python 3, MariaDB/Postgres), Vue 3 `<script setup>`, Vitest + jsdom, Tailwind, radix-vue primitives.

## Global Constraints

- **Two separate git repos.** Backend changes live in `apps/webodm_core`; frontend in `apps/webodm_frontend`. Each has its own git root — commit in the repo you changed, never across both.
- **Branch:** `feat/preset-permissions-and-copy` in *both* repos. It already exists in `webodm_core` (holds the spec commit); create it in `webodm_frontend` at Task 4.
- **Backend enforcement must not weaken.** `save()` and `delete()` keep raising `frappe.PermissionError` with their current messages. The new flags are advisory UI hints only.
- **"Admin" means `tenancy.is_platform_admin()`** — the System Manager *or* Administrator role. Never compare against the literal string `"Administrator"`.
- **`list_presets()` returns a flat list.** Do not wrap it in an envelope; `lib/presets.js` and its tests depend on the current shape.
- **Backend test command:** `cd /home/ridwan/workspaces/frappe-webodm/frappe-bench && bench --site webodm.local run-tests --app webodm_core --module <dotted.module.path>`
- **Frontend test command:** `cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend && npx vitest run <path>`
- **No `@vue/test-utils` in this project.** Component tests mount with `createApp` into a detached jsdom node — follow `src/components/OdmOptionsForm.test.js` exactly.
- Spec: `apps/webodm_core/docs/superpowers/specs/2026-08-11-preset-permissions-and-copy-design.md`

---

## File Structure

**`webodm_core` (backend)**

| File | Responsibility |
|---|---|
| `webodm_core/api/presets.py` (modify) | Add `_can_modify()`; use it in `list_presets()`, `save()`, `delete()` |
| `webodm_core/api/session.py` (create) | `whoami()` — identity + tenant context, nothing else |
| `webodm_core/api/test_presets.py` (modify) | Capability-flag coverage + enforcement-still-holds regression |
| `webodm_core/api/test_session.py` (create) | `whoami()` coverage |

**`webodm_frontend` (frontend)**

| File | Responsibility |
|---|---|
| `frontend/src/lib/presets.js` (modify) | Add the `whoami` fetch wrapper |
| `frontend/src/lib/presets.test.js` (modify) | Cover the new wrapper |
| `frontend/src/pages/Presets.vue` (modify) | Button gating, Copy action, admin System toggle |
| `frontend/src/pages/Presets.test.js` (create) | Button visibility + copy payload |

Task order is backend-first because the frontend consumes the flags. Tasks 1–3 are independently shippable; Task 4 depends on 1 and 2 existing.

---

### Task 1: `_can_modify()` helper and capability flags

**Files:**
- Modify: `apps/webodm_core/webodm_core/api/presets.py`
- Test: `apps/webodm_core/webodm_core/api/test_presets.py`

**Interfaces:**
- Consumes: `tenancy.is_platform_admin(user=None)`, `tenancy.get_current_org(user=None)` from `webodm_core/tenancy.py`
- Produces: `_can_modify(system, organization, user=None) -> bool` (module-level in `api/presets.py`); `list_presets()` rows gain `can_write: bool` and `can_delete: bool`

**Background for the implementer:** `list_presets()` returns system presets (visible to everyone) plus the caller's own org's presets. `frappe.get_all` returns `system` as `0`/`1` ints and `organization` as a string or `None`. System presets have `organization = None`. `frappe.local.webodm_org_cache` memoizes org lookups per request — tests reset it in `setUp`/`tearDown`, which is why the existing tests do so.

- [ ] **Step 1: Write the failing tests**

Add to `apps/webodm_core/webodm_core/api/test_presets.py`. Append these four methods inside the existing `class TestPresets` (which already runs as `cls.member`, an Owner of "Preset Test Org"):

```python
    def test_flags_true_for_admin_on_system_preset(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        presets.save(preset_name="Test System Preset", options="[]", system=1)
        frappe.db.commit()
        row = {p["preset_name"]: p for p in presets.list_presets()}["Test System Preset"]
        self.assertTrue(row["can_write"])
        self.assertTrue(row["can_delete"])

    def test_flags_false_for_member_on_system_preset(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        presets.save(preset_name="Test System Preset", options="[]", system=1)
        frappe.db.commit()
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        row = {p["preset_name"]: p for p in presets.list_presets()}["Test System Preset"]
        self.assertFalse(row["can_write"])
        self.assertFalse(row["can_delete"])

    def test_flags_true_for_member_on_own_org_preset(self):
        presets.save(preset_name="Test User Preset", options="[]")
        frappe.db.commit()
        row = {p["preset_name"]: p for p in presets.list_presets()}["Test User Preset"]
        self.assertTrue(row["can_write"])
        self.assertTrue(row["can_delete"])

    def test_flags_do_not_replace_enforcement(self):
        """A false flag is advisory; save() must still raise for a non-admin."""
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        presets.save(preset_name="Test System Preset", options="[]", system=1)
        frappe.db.commit()
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        with patch.object(presets.tenancy, "is_platform_admin", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                presets.save(preset_name="Test System Preset", options="[]",
                             system=1, name="Test System Preset")
```

Then add the cross-org negative case to `class TestPresetOrgIsolation`, which already has `cls.a`/`cls.org_a` and `cls.b`/`cls.org_b` fixtures:

```python
    def test_flags_false_on_another_orgs_preset(self):
        frappe.set_user(self.a)
        frappe.local.webodm_org_cache = {}
        presets.save(preset_name="Iso A Preset", options="[]")
        # B cannot even see A's preset (org query conditions), so assert via the
        # helper directly — this is the rule the flags are derived from.
        frappe.set_user(self.b)
        frappe.local.webodm_org_cache = {}
        self.assertFalse(presets._can_modify(0, self.org_a))
        self.assertTrue(presets._can_modify(0, self.org_b))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench && bench --site webodm.local run-tests --app webodm_core --module webodm_core.api.test_presets
```

Expected: FAIL — `KeyError: 'can_write'` on the flag tests, and `AttributeError: module ... has no attribute '_can_modify'` on the isolation test.

- [ ] **Step 3: Add the `_can_modify` helper**

In `apps/webodm_core/webodm_core/api/presets.py`, insert after `_encode_options()` (just before `list_presets`):

```python
def _can_modify(system, organization, user=None):
    """True when `user` may write or delete a preset with this scope.

    Single source of truth: list_presets() surfaces this as UI hints and
    save()/delete() enforce it, so the buttons a user sees cannot drift from
    what the server actually permits. Takes loose fields rather than a doc so
    it works for both a frappe.get_all row (dict) and a loaded Document.
    """
    if tenancy.is_platform_admin(user):
        return True
    if int(system or 0):
        return False
    return bool(organization) and organization == tenancy.get_current_org(user)
```

- [ ] **Step 4: Set the flags in `list_presets()`**

Replace the existing decode loop:

```python
    for r in rows:
        r["options"] = _decode_options(r.get("options"))
```

with:

```python
    for r in rows:
        r["options"] = _decode_options(r.get("options"))
        writable = _can_modify(r.get("system"), r.get("organization"))
        # Separate fields even though they agree today: delete may later gain
        # its own rule (e.g. a preset in use by a running task).
        r["can_write"] = writable
        r["can_delete"] = writable
```

- [ ] **Step 5: Route `save()` and `delete()` through the helper**

In `save()`, replace these two lines:

```python
        if doc.system and not tenancy.is_platform_admin():
            frappe.throw("Only administrators can edit system presets", frappe.PermissionError)
        if not doc.system and not tenancy.is_platform_admin() and doc.organization != tenancy.get_current_org():
            frappe.throw("You can only edit your organization's presets", frappe.PermissionError)
```

with:

```python
        if not _can_modify(doc.system, doc.organization):
            if doc.system:
                frappe.throw("Only administrators can edit system presets", frappe.PermissionError)
            frappe.throw("You can only edit your organization's presets", frappe.PermissionError)
```

In `delete()`, replace:

```python
    if doc.system and not tenancy.is_platform_admin():
        frappe.throw("Only administrators can delete system presets", frappe.PermissionError)
    if not doc.system and not tenancy.is_platform_admin() and doc.organization != tenancy.get_current_org():
        frappe.throw("You can only delete your organization's presets", frappe.PermissionError)
```

with:

```python
    if not _can_modify(doc.system, doc.organization):
        if doc.system:
            frappe.throw("Only administrators can delete system presets", frappe.PermissionError)
        frappe.throw("You can only delete your organization's presets", frappe.PermissionError)
```

Leave the `system and not tenancy.is_platform_admin()` guard at the top of `save()` untouched — it validates a *requested* scope, not an existing row's scope.

- [ ] **Step 6: Run the full preset module to verify pass and no regression**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench && bench --site webodm.local run-tests --app webodm_core --module webodm_core.api.test_presets
```

Expected: PASS, including the pre-existing `test_non_admin_cannot_create_system_preset` and the org-isolation tests.

- [ ] **Step 7: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/api/presets.py webodm_core/api/test_presets.py
git commit -m "feat(presets): surface per-row can_write/can_delete capability flags

Extract the write/delete rule into _can_modify() and have list_presets(),
save(), and delete() all call it, so the flags the UI gates on cannot drift
from what the server enforces. Flags are advisory; save()/delete() still
raise PermissionError unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `whoami` endpoint

**Files:**
- Create: `apps/webodm_core/webodm_core/api/session.py`
- Test: `apps/webodm_core/webodm_core/api/test_session.py`

**Interfaces:**
- Consumes: `tenancy.is_platform_admin()`
- Produces: whitelisted method `webodm_core.api.session.whoami` returning `{user: str, full_name: str, is_platform_admin: bool, organization: str|None, org_role: str|None}`

**Background:** `WebODM Org Membership` has one row per user (a user belongs to exactly one org) with fields `user`, `organization`, `role` (`"Owner"` or `"Member"`). `frappe.db.get_value(..., as_dict=True)` returns `None` when no row matches — hence the `if row else None` guards. `whoami` takes **no arguments** and always reports the session user, so it cannot enumerate other users.

- [ ] **Step 1: Write the failing test**

Create `apps/webodm_core/webodm_core/api/test_session.py`:

```python
import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import session as session_api


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestWhoami(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.local.webodm_org_cache = {}
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Whoami Org"}).insert(ignore_permissions=True).name
        cls.member = _user("whoami_member@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member,
                        "organization": cls.org, "role": "Member"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_member_gets_org_role_and_no_admin(self):
        frappe.set_user(self.member)
        out = session_api.whoami()
        self.assertEqual(out["user"], self.member)
        self.assertEqual(out["organization"], self.org)
        self.assertEqual(out["org_role"], "Member")
        self.assertFalse(out["is_platform_admin"])

    def test_administrator_is_platform_admin(self):
        frappe.set_user("Administrator")
        out = session_api.whoami()
        self.assertTrue(out["is_platform_admin"])

    def test_orgless_user_gets_nulls(self):
        orgless = _user("whoami_orgless@example.com")
        frappe.set_user(orgless)
        out = session_api.whoami()
        self.assertIsNone(out["organization"])
        self.assertIsNone(out["org_role"])
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench && bench --site webodm.local run-tests --app webodm_core --module webodm_core.api.test_session
```

Expected: FAIL with `ModuleNotFoundError: No module named 'webodm_core.api.session'`.

- [ ] **Step 3: Write the implementation**

Create `apps/webodm_core/webodm_core/api/session.py`:

```python
# webodm_core/api/session.py
"""Session identity for the frontend.

Carries identity and tenant context ONLY. Per-resource capability travels with
the resource itself (see api/presets.list_presets) so permission rules stay on
the server rather than being re-derived in JS.
"""
import frappe
from frappe.utils import get_fullname
from webodm_core import tenancy


@frappe.whitelist(allow_guest=False)
def whoami():
    """Identity + tenant context for the calling user.

    Takes no arguments — always reports the session user, so it cannot be used
    to enumerate other accounts.
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

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench && bench --site webodm.local run-tests --app webodm_core --module webodm_core.api.test_session
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/api/session.py webodm_core/api/test_session.py
git commit -m "feat(api): add whoami session endpoint

Identity and tenant context for the frontend, which currently has no notion
of the caller. Deliberately excludes per-resource permissions -- those travel
with the resource so the rules stay server-side.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Backend regression sweep

**Files:** none changed — this is a gate before the frontend work.

- [ ] **Step 1: Run the whole backend suite**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench && bench --site webodm.local run-tests --app webodm_core
```

Expected: PASS. Pay attention to `test_isolation.py`, `test_stamping.py`, and `test_tenancy.py` — `_can_modify` calls `get_current_org()`, which is request-cached, so a stale cache would surface there.

If anything fails, fix it before starting Task 4. Do not proceed with a red suite.

---

### Task 4: `whoami` frontend wrapper

**Files:**
- Modify: `apps/webodm_frontend/frontend/src/lib/presets.js`
- Test: `apps/webodm_frontend/frontend/src/lib/presets.test.js`

**Interfaces:**
- Consumes: `webodm_core.api.session.whoami` (Task 2)
- Produces: `whoami()` exported from `@/lib/presets`, resolving to the whoami payload

**Background:** `lib/presets.js` holds private `headers`/`unwrap`/`get`/`post` helpers. The wrapper goes here rather than in a new `lib/session.js` to avoid a *third* copy of those helpers (`lib/organization.js` already duplicates them). Consolidating them is a known follow-up, out of scope.

- [ ] **Step 1: Create the frontend branch**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -b feat/preset-permissions-and-copy
```

- [ ] **Step 2: Write the failing test**

Add to the `describe('presets lib', ...)` block in `apps/webodm_frontend/frontend/src/lib/presets.test.js`:

```js
  it('whoami calls the session endpoint with GET', async () => {
    await whoami()
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/method/webodm_core.api.session.whoami',
      expect.objectContaining({ method: 'GET' }),
    )
  })
```

And extend the import at the top of the file:

```js
import { listPresets, savePreset, fetchOptions, whoami } from '@/lib/presets'
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend && npx vitest run src/lib/presets.test.js
```

Expected: FAIL with `whoami is not a function`.

- [ ] **Step 4: Add the wrapper**

In `apps/webodm_frontend/frontend/src/lib/presets.js`, add after the `deletePreset` export:

```js
export const whoami = () => get('webodm_core.api.session.whoami')
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend && npx vitest run src/lib/presets.test.js
```

Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/lib/presets.js frontend/src/lib/presets.test.js
git commit -m "feat(lib): add whoami fetch wrapper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Presets page — gating, copy, and admin toggle

**Files:**
- Modify: `apps/webodm_frontend/frontend/src/pages/Presets.vue`
- Test: `apps/webodm_frontend/frontend/src/pages/Presets.test.js` (create)

**Interfaces:**
- Consumes: `listPresets()` rows with `can_write`/`can_delete` (Task 1); `whoami()` (Task 4); existing `savePreset`, `deletePreset`, `useOdmOptions`, `OdmOptionsForm`
- Produces: no exports — this is the leaf of the feature

**Background for the implementer:**

- The page currently renders Edit and Delete on every row unconditionally (`Presets.vue:39-52`) — that is the bug.
- `draft` is a `reactive({ preset_name: '' })`. It gains a `system` field.
- `editing` is a `ref` holding the source preset when editing, `null` when creating. **Copy sets it to `null`** — that is what makes `onSave` post `name: null` and create a new record.
- The `Dialog` component renders through `DialogPortal`, so **its content mounts into `document.body`, not the returned element**. Page-level assertions about dialog contents must query `document.body`; assertions about the table query the mounted element. This trips people up.
- `useOdmOptions()` fetches the ODM catalog over the network. The test **must** mock `@/lib/presets` (for `listPresets`/`whoami`/`savePreset`) and `@/composables/useOdmOptions`.
- Preset names are unique site-wide (`autoname: field:preset_name`). Copying the same source twice collides and surfaces the existing error toast — intended, do not auto-increment.

- [ ] **Step 1: Write the failing test**

Create `apps/webodm_frontend/frontend/src/pages/Presets.test.js`:

```js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, ref, nextTick } from 'vue'

const rows = ref([])
const whoamiPayload = ref({ is_platform_admin: false })
const savePreset = vi.fn(async () => ({ name: 'X' }))

vi.mock('@/lib/presets', () => ({
  listPresets: () => Promise.resolve(rows.value),
  whoami: () => Promise.resolve(whoamiPayload.value),
  savePreset: (...a) => savePreset(...a),
  deletePreset: vi.fn(async () => ({ ok: true })),
}))

// The real composable fetches the ODM option catalog over the network.
vi.mock('@/composables/useOdmOptions', () => ({
  useOdmOptions: () => ({
    catalog: ref([]),
    loading: ref(false),
    error: ref(''),
    load: async () => {},
    seedEnumDefaults: () => {},
    fieldType: () => 'checkbox',
  }),
  fieldType: () => 'checkbox',
}))

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import Presets from './Presets.vue'

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(Presets)
  app.mount(el)
  mounted.push(app)
  await nextTick(); await nextTick(); await nextTick()
  return el
}

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  savePreset.mockClear()
})

const systemRow = {
  name: 'Sys', preset_name: 'Sys', options: [{ name: 'dsm', value: true }],
  system: 1, organization: null, can_write: false, can_delete: false,
}
const ownRow = {
  name: 'Mine', preset_name: 'Mine', options: [],
  system: 0, organization: 'ORG-A', can_write: true, can_delete: true,
}

function titles(el) {
  return [...el.querySelectorAll('button[title]')].map(b => b.getAttribute('title'))
}

describe('Presets page', () => {
  beforeEach(() => {
    rows.value = []
    whoamiPayload.value = { is_platform_admin: false }
  })

  it('hides Edit and Delete on a system preset for a non-admin, keeps Copy', async () => {
    rows.value = [systemRow]
    const el = await mount()
    const t = titles(el)
    expect(t).toContain('Copy preset')
    expect(t).not.toContain('Edit preset')
    expect(t).not.toContain('Delete preset')
  })

  it('shows Edit and Delete on a system preset for an admin', async () => {
    rows.value = [{ ...systemRow, can_write: true, can_delete: true }]
    whoamiPayload.value = { is_platform_admin: true }
    const el = await mount()
    const t = titles(el)
    expect(t).toContain('Edit preset')
    expect(t).toContain('Delete preset')
  })

  it('shows Edit and Delete on the users own org preset', async () => {
    rows.value = [ownRow]
    const el = await mount()
    const t = titles(el)
    expect(t).toContain('Edit preset')
    expect(t).toContain('Delete preset')
  })

  it('copy submits as a new preset with a (copy) suffixed name', async () => {
    rows.value = [systemRow]
    const el = await mount()
    el.querySelector('button[title="Copy preset"]').click()
    await nextTick(); await nextTick()
    // Dialog content renders through a portal into document.body.
    const save = [...document.body.querySelectorAll('button')]
      .find(b => b.textContent.trim() === 'Save')
    save.click()
    await nextTick()
    expect(savePreset).toHaveBeenCalledWith(expect.objectContaining({
      name: null,
      preset_name: 'Sys (copy)',
      system: 0,
    }))
  })

  it('shows the System toggle only to admins', async () => {
    rows.value = [ownRow]
    const el = await mount()
    el.querySelector('button[title="Edit preset"]').click()
    await nextTick(); await nextTick()
    expect(document.body.querySelector('#preset-system')).toBeNull()

    mounted.forEach(a => a.unmount()); mounted = []
    document.body.innerHTML = ''
    whoamiPayload.value = { is_platform_admin: true }
    const el2 = await mount()
    el2.querySelector('button[title="Edit preset"]').click()
    await nextTick(); await nextTick()
    expect(document.body.querySelector('#preset-system')).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend && npx vitest run src/pages/Presets.test.js
```

Expected: FAIL — the first test fails because Edit/Delete render unconditionally today, and the copy test fails because no Copy button exists.

- [ ] **Step 3: Gate the action buttons and add Copy**

In `apps/webodm_frontend/frontend/src/pages/Presets.vue`, replace the actions cell (currently `Presets.vue:38-53`):

```html
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" class="size-8" title="Copy preset" @click="openCopy(p)">
                <Copy />
                <span class="sr-only">Copy {{ p.preset_name }}</span>
              </Button>
              <Button
                v-if="p.can_write"
                variant="ghost"
                size="icon"
                class="size-8"
                title="Edit preset"
                @click="openEdit(p)"
              >
                <Pencil />
                <span class="sr-only">Edit {{ p.preset_name }}</span>
              </Button>
              <Button
                v-if="p.can_delete"
                variant="ghost"
                size="icon"
                class="size-8 text-muted-foreground hover:text-destructive"
                title="Delete preset"
                @click="onDelete(p)"
              >
                <Trash2 />
                <span class="sr-only">Delete {{ p.preset_name }}</span>
              </Button>
            </td>
```

Update the icon import:

```js
import { Copy, Pencil, Plus, Trash2 } from 'lucide-vue-next'
```

- [ ] **Step 4: Add the admin System toggle to the dialog**

In the dialog body, after the name field's closing `</div>`, add:

```html
        <div v-if="isAdmin" class="flex items-center gap-2">
          <input
            id="preset-system"
            type="checkbox"
            v-model="draft.system"
            class="rounded"
          />
          <Label for="preset-system" class="font-normal">
            System preset (visible to every organization)
          </Label>
        </div>
```

- [ ] **Step 5: Wire up state, whoami, and the copy handler**

In `<script setup>`, add the import and admin state:

```js
import { listPresets, savePreset, deletePreset, whoami } from '@/lib/presets'
```

```js
const isAdmin = ref(false)
```

Add `system` to the draft:

```js
const draft = reactive({ preset_name: '', system: 0 })
```

Replace the bare `refresh()` call with a mount-time load that fetches both in parallel:

```js
async function refresh() {
  try {
    presets.value = await listPresets()
  } catch (e) {
    toast.error(e.message || 'Failed to load presets')
  }
}

async function loadAdmin() {
  try {
    isAdmin.value = !!(await whoami()).is_platform_admin
  } catch {
    isAdmin.value = false  // no admin affordances if identity is unknown
  }
}

refresh()
loadAdmin()
```

Set `draft.system` in the existing handlers — in `openCreate`:

```js
  draft.system = 0
```

in `openEdit`:

```js
  draft.system = p.system ? 1 : 0
```

Add `openCopy` after `openEdit`:

```js
// Copy = open the create dialog pre-filled. editing stays null, so onSave
// posts name:null and creates a new record. Always org-scoped unless an
// admin flips the System toggle before saving.
async function openCopy(p) {
  editing.value = null
  draft.preset_name = `${p.preset_name} (copy)`
  draft.system = 0
  values.value = Object.fromEntries((p.options || []).map(o => [o.name, o.value]))
  showModal.value = true
  await odm.load()
  odm.seedEnumDefaults(values.value)
}
```

Finally, make `onSave` send the draft's scope instead of the source's:

```js
      system: draft.system ? 1 : 0,
```

(replacing `system: editing.value?.system || 0,`)

- [ ] **Step 6: Run the test to verify it passes**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend && npx vitest run src/pages/Presets.test.js
```

Expected: PASS (5 tests).

- [ ] **Step 7: Run the full frontend suite and build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend && npx vitest run && npm run build
```

Expected: all tests PASS (81 total — 75 pre-existing, plus 1 from Task 4 and 5 from this task) and a clean build.

- [ ] **Step 8: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Presets.vue frontend/src/pages/Presets.test.js
git commit -m "fix(presets): show only the actions the user may perform

Edit and Delete rendered on every row including system presets, so a regular
user only discovered the block after filling in the dialog and saving. Gate
both on the server-computed can_write/can_delete flags.

Adds a Copy action (opens the create dialog pre-filled, saves as a new
org preset) and an admin-only System toggle, which closes the gap where
system presets could only be created by the seeding patch.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Manual verification

**Files:** none.

The automated tests cover the rules; this confirms the actual rendered app behaves as intended.

- [ ] **Step 1: Start the stack**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench && bench start
```

If the frontend is served by Vite in dev, also run `cd apps/webodm_frontend/frontend && npm run dev`.

- [ ] **Step 2: Check as a regular user**

Log in as a `WebODM User` who belongs to an org, open the Presets page, and confirm:
- System preset rows show **only** Copy.
- Own-org rows show Copy, Edit, and Delete.
- No "System preset" checkbox in the New Preset dialog.
- Copy on a system preset opens the dialog pre-filled with the source's options and the name `"<name> (copy)"`; saving creates an editable org preset.

- [ ] **Step 3: Check as an admin**

Log in as Administrator (or any System Manager) and confirm:
- System rows show Copy, Edit, and Delete.
- The "System preset" checkbox appears in the dialog and creates a system-scoped preset when ticked.

- [ ] **Step 4: Report results**

Report what you observed, including anything that did not match. Do not mark this task complete on the strength of the unit tests alone.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 `_can_modify` + `can_write`/`can_delete` flags | Task 1 |
| §1 `save()`/`delete()` share the helper, enforcement unchanged | Task 1 (steps 5–6, regression test in step 1) |
| §2 `whoami` endpoint | Task 2 |
| §3 Button gating | Task 5 (steps 3, 6) |
| §3 Copy action, `name: null`, `(copy)` suffix | Task 5 (steps 5–6) |
| §3 Admin-only System toggle | Task 5 (steps 4–5) |
| §3 `whoami` fetched on mount, parallel | Task 5 (step 5) |
| §3 `whoami` lib wrapper | Task 4 |
| §3 Name collisions surface as errors | No code — existing `onSave` catch already toasts; asserted by omission (no auto-increment logic added) |
| §4 Backend flag tests (4 cases) + enforcement regression | Task 1 (step 1) |
| §4 `test_session.py` | Task 2 (step 1) |
| §4 Frontend lib test | Task 4 (step 2) |
| §4 Page tests (5 cases) | Task 5 (step 1) |

No spec requirement is unimplemented. Out-of-scope items (creator display, redundant `owner` DocField, `get_my_organization` consolidation, fetch-helper dedup) are correctly absent.

**Placeholder scan:** No TBDs, no "add error handling", no "similar to Task N". Every code step carries literal code.

**Type consistency:** `_can_modify(system, organization, user=None)` is called with that signature in `list_presets()`, `save()`, `delete()`, and the isolation test. `can_write`/`can_delete` are named identically in the backend, the test assertions, the Vue template, and the test fixtures. `whoami()`'s `is_platform_admin` key matches between `session.py`, `test_session.py`, the mock payload, and `Presets.vue`. `draft.system` is set in `openCreate`, `openEdit`, `openCopy`, read by the toggle and `onSave`.
