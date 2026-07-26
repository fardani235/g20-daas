# webodm_core/api/organization.py
import frappe
from frappe.utils import now_datetime, get_datetime
from webodm_core import tenancy


@frappe.whitelist(allow_guest=False)
def create_organization(name):
    user = frappe.session.user
    if frappe.db.exists("WebODM Org Membership", {"user": user}):
        frappe.throw("You already belong to an organization", frappe.ValidationError)
    org = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}).insert(ignore_permissions=True)
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org.name, "role": "Owner"}).insert(ignore_permissions=True)
    frappe.local.webodm_org_cache = {}
    return {"organization": org.name, "role": "Owner"}


@frappe.whitelist(allow_guest=False)
def get_my_organization():
    user = frappe.session.user
    row = frappe.db.get_value("WebODM Org Membership", {"user": user},
                              ["organization", "role"], as_dict=True)
    if not row:
        return {"organization": None, "role": None}
    return {"organization": row.organization, "role": row.role}


@frappe.whitelist(allow_guest=False)
def list_members():
    org = tenancy.require_org()
    return frappe.get_all("WebODM Org Membership", filters={"organization": org},
                          fields=["user", "role"])


@frappe.whitelist(allow_guest=False)
def remove_member(user):
    org = tenancy.require_org()
    if not tenancy.is_org_admin():
        frappe.throw("Only organization admins can remove members", frappe.PermissionError)
    name = frappe.db.get_value("WebODM Org Membership", {"user": user, "organization": org})
    if not name:
        frappe.throw("Not a member of your organization")
    owners = frappe.db.count("WebODM Org Membership", {"organization": org, "role": "Owner"})
    role = frappe.db.get_value("WebODM Org Membership", name, "role")
    if role == "Owner" and owners <= 1:
        frappe.throw("Cannot remove the last owner")
    frappe.delete_doc("WebODM Org Membership", name, ignore_permissions=True)
    frappe.local.webodm_org_cache = {}
    return {"removed": user}


@frappe.whitelist(allow_guest=False)
def invite_member(email):
    org = tenancy.require_org()
    if not tenancy.is_org_admin():
        frappe.throw("Only organization admins can invite members", frappe.PermissionError)
    inv = frappe.get_doc({"doctype": "WebODM Org Invitation",
                          "email": email, "organization": org, "status": "Pending"}).insert(ignore_permissions=True)
    # TODO(subsystem: notifications) send email with the accept link.
    return {"token": inv.token, "email": email}


@frappe.whitelist(allow_guest=False)
def accept_invitation(token):
    user = frappe.session.user
    if frappe.db.exists("WebODM Org Membership", {"user": user}):
        frappe.throw("You already belong to an organization", frappe.ValidationError)
    name = frappe.db.get_value("WebODM Org Invitation", {"token": token})
    if not name:
        frappe.throw("Invalid invitation", frappe.ValidationError)
    inv = frappe.get_doc("WebODM Org Invitation", name)
    if inv.status != "Pending":
        frappe.throw("Invitation is no longer valid", frappe.ValidationError)
    if inv.expires_on and get_datetime(inv.expires_on) < now_datetime():
        frappe.throw("Invitation has expired", frappe.ValidationError)
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": inv.organization, "role": "Member"}).insert(ignore_permissions=True)
    inv.status = "Accepted"
    inv.save(ignore_permissions=True)
    frappe.local.webodm_org_cache = {}
    return {"organization": inv.organization, "role": "Member"}
