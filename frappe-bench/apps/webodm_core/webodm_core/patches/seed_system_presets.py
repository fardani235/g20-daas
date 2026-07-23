"""Seed the well-known WebODM system processing presets.

Mirrors the default presets WebODM ships (see its ``app/boot.py``
``add_default_presets``): the same names and ODM option name/value pairs, so
users get a familiar starting set. Idempotent — upserts by name, so running it
again (or after editing the values below) refreshes the stored options without
creating duplicates.

Options are stored through ``presets._encode_options`` (a double-encoded JSON
string scalar) exactly like the API's ``save()``, so ``list_presets`` decodes
them correctly and the value stays delete-safe on PostgreSQL (a top-level array
left in a JSON column reloads as a list and breaks Frappe's delete snapshot).
"""

import frappe

from webodm_core.api.presets import _encode_options

_DOCTYPE = "WebODM Preset"

# name -> options list ([{name, value}]). Copied verbatim from WebODM's
# add_default_presets(); order preserved.
_SYSTEM_PRESETS = {
    "Default": [
        {"name": "auto-boundary", "value": True},
        {"name": "dsm", "value": True},
    ],
    "High Resolution": [
        {"name": "auto-boundary", "value": True},
        {"name": "dsm", "value": True},
        {"name": "dem-resolution", "value": "1.0"},
        {"name": "orthophoto-resolution", "value": "1.0"},
    ],
    "Fast Orthophoto": [
        {"name": "auto-boundary", "value": True},
        {"name": "fast-orthophoto", "value": True},
    ],
    "Field": [
        {"name": "sfm-algorithm", "value": "planar"},
        {"name": "fast-orthophoto", "value": True},
        {"name": "matcher-neighbors", "value": 4},
    ],
    "DSM + DTM": [
        {"name": "auto-boundary", "value": True},
        {"name": "dsm", "value": True},
        {"name": "dtm", "value": True},
    ],
    "Forest": [
        {"name": "auto-boundary", "value": True},
        {"name": "min-num-features", "value": "18000"},
        {"name": "use-3dmesh", "value": True},
        {"name": "feature-quality", "value": "medium"},
    ],
    "Buildings": [
        {"name": "auto-boundary", "value": True},
        {"name": "mesh-size", "value": "300000"},
        {"name": "feature-quality", "value": "high"},
        {"name": "pc-quality", "value": "high"},
    ],
    "3D Model": [
        {"name": "auto-boundary", "value": True},
        {"name": "mesh-octree-depth", "value": "12"},
        {"name": "use-3dmesh", "value": True},
        {"name": "pc-quality", "value": "high"},
        {"name": "mesh-size", "value": "300000"},
    ],
    "Volume Analysis": [
        {"name": "auto-boundary", "value": True},
        {"name": "dsm", "value": True},
        {"name": "dem-resolution", "value": "2"},
        {"name": "pc-quality", "value": "high"},
    ],
    "Multispectral": [
        {"name": "auto-boundary", "value": True},
        {"name": "radiometric-calibration", "value": "camera"},
    ],
}


def execute():
    for preset_name, options in _SYSTEM_PRESETS.items():
        encoded = _encode_options(options)
        if frappe.db.exists(_DOCTYPE, preset_name):
            doc = frappe.get_doc(_DOCTYPE, preset_name)
            doc.options = encoded
            doc.system = 1
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({
                "doctype": _DOCTYPE,
                "preset_name": preset_name,
                "owner": "Administrator",
                "system": 1,
                "options": encoded,
            }).insert(ignore_permissions=True)
    frappe.db.commit()
