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
