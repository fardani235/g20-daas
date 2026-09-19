import frappe


@frappe.whitelist(allow_guest=False)
def update_profile(full_name=None, mobile_no=None):
    """Allow the current user to update their own profile.

    Only ``full_name`` and ``mobile_no`` are accepted. Any other field is
    silently ignored so the endpoint cannot be abused to escalate privileges.
    """
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication required", frappe.AuthenticationError)

    doc = frappe.get_doc("User", user)

    if full_name is not None:
        doc.full_name = full_name
    if mobile_no is not None:
        doc.mobile_no = mobile_no

    doc.save(ignore_permissions=True)
    return doc


@frappe.whitelist(allow_guest=False)
def get_profile():
    """Return the current user's public profile fields."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication required", frappe.AuthenticationError)

    doc = frappe.get_doc("User", user)
    return {
        "name": doc.name,
        "email": doc.email,
        "full_name": doc.full_name,
        "mobile_no": doc.mobile_no,
        "first_name": doc.first_name,
        "last_name": doc.last_name,
    }
