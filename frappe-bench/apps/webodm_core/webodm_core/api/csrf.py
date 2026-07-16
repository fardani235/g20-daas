import frappe
from frappe.sessions import get_csrf_token


@frappe.whitelist(allow_guest=False)
def get_token():
    return get_csrf_token()
