import frappe

from webodm_core.tenancy import get_current_org, is_platform_admin


def _org_query_conditions(doctype, user):
    if is_platform_admin(user):
        return None
    org = get_current_org(user)
    if not org:
        return "1=0"  # deny-by-default: no org -> no rows
    return f"`tab{doctype}`.`organization` = {frappe.db.escape(org)}"


def _org_has_permission(doc, user):
    if is_platform_admin(user):
        return True
    org = get_current_org(user)
    if not org:
        return False
    doc_org = doc.organization if hasattr(doc, "organization") else None
    return doc_org == org


def _is_create(ptype):
    # Frappe runs check_permission("create") BEFORE the before_insert stamping
    # hook, so a new doc's `organization` is still unset (or a spoofed payload
    # value) at this point -- an org-match check here would wrongly deny a valid
    # member and wrongly reflect a spoofed org. Creation is instead governed by
    # role perms plus the stamping hook's require_org(), which denies orgless
    # users (OrgContextError) and overwrites any spoofed org. So never deny here.
    return ptype == "create"


def get_project_permission_query_conditions(user=None):
    return _org_query_conditions("WebODM Project", user or frappe.session.user)


def get_task_permission_query_conditions(user=None):
    return _org_query_conditions("WebODM Task", user or frappe.session.user)


def get_preset_permission_query_conditions(user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return None
    org = get_current_org(user)
    # System presets (organization IS NULL, system=1) are visible to every org.
    system_clause = "`tabWebODM Preset`.`system` = 1"
    if not org:
        return system_clause  # no org: only the shared system presets
    return f"({system_clause} OR `tabWebODM Preset`.`organization` = {frappe.db.escape(org)})"


def has_project_permission(doc, ptype, user=None):
    if _is_create(ptype):
        return True
    return _org_has_permission(doc, user or frappe.session.user)


def has_task_permission(doc, ptype, user=None):
    if _is_create(ptype):
        return True
    return _org_has_permission(doc, user or frappe.session.user)


def has_preset_permission(doc, ptype, user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return True
    if _is_create(ptype):
        return True
    # A system preset is readable by anyone; writes still fall to org scoping.
    if getattr(doc, "system", 0) and int(doc.system) == 1 and ptype == "read":
        return True
    return _org_has_permission(doc, user)
