from frappe.model.document import Document


class WebODMLicense(Document):
    """A license in the marketplace registry. ``permits_redistribution`` is
    the platform's curated judgement and gates whether a product may offer
    anonymous downloads of artifacts released under it."""
