"""Structural guard: every tenant-owned DocType must be wired into the
org-scoping permission hooks.

The hooks.py permission maps are hand-maintained. WebODM Org Invitation /
Membership / Organization once fell through them, which let any WebODM User
enumerate other orgs' members and mint an invitation into an arbitrary org.
This test turns "forgot to add the new DocType to hooks.py" into a CI failure.
"""
import frappe
from frappe.tests.utils import FrappeTestCase

# DocTypes that ARE the tenant rather than carrying an `organization` field.
_TENANT_ROOT_DOCTYPES = {"WebODM Organization"}


def _tenant_owned_doctypes():
    owned = set(_TENANT_ROOT_DOCTYPES)
    for dt in frappe.get_all("DocType", filters={"module": "WebODM Core", "istable": 0}, pluck="name"):
        if frappe.get_meta(dt).has_field("organization"):
            owned.add(dt)
    return owned


class TestTenantDoctypeCoverage(FrappeTestCase):
    def test_every_tenant_doctype_has_query_conditions(self):
        hooked = set(frappe.get_hooks("permission_query_conditions", {}).keys())
        missing = _tenant_owned_doctypes() - hooked
        self.assertFalse(
            missing,
            f"Tenant-owned DocTypes missing from hooks.permission_query_conditions: {sorted(missing)}",
        )

    def test_every_tenant_doctype_has_has_permission(self):
        hooked = set(frappe.get_hooks("has_permission", {}).keys())
        missing = _tenant_owned_doctypes() - hooked
        self.assertFalse(
            missing,
            f"Tenant-owned DocTypes missing from hooks.has_permission: {sorted(missing)}",
        )

    def test_hook_targets_resolve(self):
        # A typo in a dotted path would otherwise only surface at request time.
        for hook in ("permission_query_conditions", "has_permission"):
            for dt, paths in frappe.get_hooks(hook, {}).items():
                for path in paths:
                    if path.startswith("webodm_core."):
                        self.assertTrue(callable(frappe.get_attr(path)), f"{hook}[{dt}] -> {path}")

    def test_no_self_service_create_on_org_model(self):
        # Creation of org-model rows is API-only (api/organization.py). Role perms
        # must not hand `create` to regular users, or the has_permission hook's
        # org check is reachable with a caller-chosen `organization`.
        for dt in ("WebODM Organization", "WebODM Org Membership", "WebODM Org Invitation"):
            for perm in frappe.get_meta(dt).permissions:
                if perm.role == "WebODM User":
                    self.assertFalse(perm.create, f"{dt}: WebODM User must not have create")
                    self.assertFalse(perm.write, f"{dt}: WebODM User must not have write")
                    self.assertFalse(perm.delete, f"{dt}: WebODM User must not have delete")
