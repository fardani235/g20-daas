import frappe


def _is_admin(user):
    return "System Manager" in frappe.get_roles(user) or "Administrator" in frappe.get_roles(user)


def get_project_permission_query_conditions(user=None):
    if not user:
        user = frappe.session.user
    if _is_admin(user):
        return None
    return f"`tabWebODM Project`.`owner` = {frappe.db.escape(user)}"


def get_task_permission_query_conditions(user=None):
    if not user:
        user = frappe.session.user
    if _is_admin(user):
        return None
    return f"`tabWebODM Task`.`owner` = {frappe.db.escape(user)}"


def has_project_permission(doc, ptype, user=None):
    if not user:
        user = frappe.session.user
    if _is_admin(user):
        return True
    owner = doc.owner if hasattr(doc, "owner") else frappe.db.get_value("WebODM Project", doc, "owner")
    return owner == user


def has_task_permission(doc, ptype, user=None):
    if not user:
        user = frappe.session.user
    if _is_admin(user):
        return True
    owner = doc.owner if hasattr(doc, "owner") else frappe.db.get_value("WebODM Task", doc, "owner")
    return owner == user
