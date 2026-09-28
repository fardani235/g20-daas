import frappe
from frappe.model.document import Document


class WebODMProject(Document):
    def on_trash(self):
        # Tasks link to their project; delete them first so the project delete
        # does not fail with a link error. Each task in turn deletes its plugin
        # runs, releases compute and drops its stored objects (see WebODMTask).
        for name in frappe.get_all("WebODM Task", filters={"project": self.name}, pluck="name"):
            # Permission was checked on the project; its tasks go with it.
            frappe.delete_doc("WebODM Task", name, ignore_permissions=True)
