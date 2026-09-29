"""Seed the marketplace reference data: the license registry, browse
categories and the first-party publisher.

Idempotent — every row is upserted by its id, so admins may edit labels and
summaries without them being reverted, but ``permits_redistribution`` is
re-asserted from this table because it gates anonymous downloads and must
not drift. Products and releases are *not* seeded: artifacts are built from
``plugins/*`` (some need model weights fetched at build time) and published
with ``webodm_core.marketplace.publishing``.
"""

import frappe

FIRST_PARTY_PUBLISHER = {
    "publisher_id": "g20-tech",
    "display_name": "G20 Tech",
    "kind": "First-party",
    "status": "Active",
    "description": "Analysis plugins built and maintained by the platform team.",
}

# (license_id, name, url, permits_redistribution, summary)
LICENSES = [
    ("MIT", "MIT License", "https://opensource.org/license/mit", 1,
     "Permissive. Use, modify and redistribute with attribution."),
    ("Apache-2.0", "Apache License 2.0", "https://www.apache.org/licenses/LICENSE-2.0", 1,
     "Permissive with an explicit patent grant. Redistribution allowed with notices kept."),
    ("BSD-3-Clause", "BSD 3-Clause License", "https://opensource.org/license/bsd-3-clause", 1,
     "Permissive. Redistribution allowed with attribution; no endorsement implied."),
    ("GPL-3.0", "GNU General Public License v3.0", "https://www.gnu.org/licenses/gpl-3.0.html", 1,
     "Copyleft. Redistribution allowed; derived works must remain GPL."),
    ("AGPL-3.0", "GNU Affero General Public License v3.0", "https://www.gnu.org/licenses/agpl-3.0.html", 1,
     "Network copyleft. Redistribution allowed; running it as a service triggers source obligations."),
    ("CC-BY-4.0", "Creative Commons Attribution 4.0", "https://creativecommons.org/licenses/by/4.0/", 1,
     "For data and models. Redistribution allowed with attribution."),
    ("CC-BY-NC-4.0", "Creative Commons Attribution-NonCommercial 4.0",
     "https://creativecommons.org/licenses/by-nc/4.0/", 0,
     "Non-commercial use only. Not redistributed anonymously by this platform."),
    ("Etalab-2.0", "Licence Ouverte / Open Licence 2.0",
     "https://www.etalab.gouv.fr/licence-ouverte-open-licence/", 1,
     "French open data licence (e.g. IGN FLAIR models). Redistribution allowed with attribution."),
    ("Proprietary", "Proprietary", None, 0,
     "All rights reserved by the publisher. Available to signed-in organizations under the "
     "publisher's terms; no redistribution."),
]

# (category_id, label, description)
CATEGORIES = [
    ("vegetation", "Vegetation", "Tree crowns, canopy, plant health and land cover."),
    ("terrain", "Terrain", "Elevation, slope, landforms and hydrology."),
    ("detection", "Object detection", "Find and count objects on the orthophoto."),
    ("segmentation", "Segmentation", "Per-pixel classification of the scene."),
    ("3d", "3D", "Meshes, point clouds and reconstruction."),
    ("measurement", "Measurement", "Volumes, areas, distances and change."),
    ("infrastructure", "Infrastructure", "Roads, buildings, utilities and inspection."),
    ("agriculture", "Agriculture", "Crops, fields and yield-related analysis."),
]


def _upsert(doctype: str, name: str, values: dict):
    if frappe.db.exists(doctype, name):
        doc = frappe.get_doc(doctype, name)
        for key, value in values.items():
            doc.set(key, value)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


def execute():
    for license_id, name, url, permits, summary in LICENSES:
        if frappe.db.exists("WebODM License", license_id):
            # Keep admin edits to name/url/summary; re-assert the gate.
            frappe.db.set_value("WebODM License", license_id, "permits_redistribution", permits,
                                update_modified=False)
        else:
            _upsert("WebODM License", license_id, {
                "license_id": license_id, "license_name": name, "url": url,
                "permits_redistribution": permits, "summary": summary,
            })

    for category_id, label, description in CATEGORIES:
        if not frappe.db.exists("WebODM Marketplace Category", category_id):
            _upsert("WebODM Marketplace Category", category_id, {
                "category_id": category_id, "label": label, "description": description,
            })

    if not frappe.db.exists("WebODM Publisher", FIRST_PARTY_PUBLISHER["publisher_id"]):
        _upsert("WebODM Publisher", FIRST_PARTY_PUBLISHER["publisher_id"], FIRST_PARTY_PUBLISHER)

    frappe.db.commit()
