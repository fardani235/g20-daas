"""Public view of the catalog: what a visitor (signed-in or not) may see.

Everything here reads with permissions bypassed and filters explicitly to
Published products of Active publishers and Published/Yanked releases, so
Draft/Retired listings and Draft releases never leak through the guest
endpoints. Nothing organization-specific is produced here; ``viewer``
context is merged in by ``api/marketplace.py``.
"""

import frappe

from webodm_core.marketplace import install as install_mod
from webodm_core.marketplace import kinds

_PRODUCT = "WebODM Product"
_RELEASE = "WebODM Product Release"

_PRODUCT_FIELDS = [
    "name", "product_id", "title", "summary", "publisher", "artifact_kind", "status",
    "icon", "allow_anonymous_download", "description", "docs", "docs_url",
    "homepage_url", "support_url", "modified",
]
_RELEASE_FIELDS = [
    "name", "product", "version", "status", "published_on", "artifact_hash",
    "artifact_size", "license", "license_notes", "release_notes", "manifest", "creation",
]


def _active_publishers() -> dict[str, dict]:
    rows = frappe.get_all(
        "WebODM Publisher",
        filters={"status": "Active"},
        fields=["name", "publisher_id", "display_name", "kind", "website", "description"],
    )
    return {r.name: dict(r) for r in rows}


def _licenses() -> dict[str, dict]:
    rows = frappe.get_all(
        "WebODM License",
        fields=["name", "license_id", "license_name", "url", "permits_redistribution", "summary"],
    )
    return {
        r.name: {
            "license_id": r.license_id,
            "name": r.license_name,
            "url": r.url,
            "permits_redistribution": bool(r.permits_redistribution),
            "summary": r.summary,
        }
        for r in rows
    }


def _categories_by_product(product_names: list[str]) -> dict[str, list[dict]]:
    if not product_names:
        return {}
    labels = {
        r.name: r.label
        for r in frappe.get_all("WebODM Marketplace Category", fields=["name", "label"])
    }
    out: dict[str, list[dict]] = {}
    for row in frappe.get_all(
        "WebODM Product Category",
        filters={"parent": ["in", product_names], "parenttype": _PRODUCT},
        fields=["parent", "category"],
        order_by="idx asc",
    ):
        out.setdefault(row.parent, []).append(
            {"category_id": row.category, "label": labels.get(row.category, row.category)}
        )
    return out


def _media_for(product_name: str) -> list[dict]:
    return [
        {"url": r.image, "caption": r.caption}
        for r in frappe.get_all(
            "WebODM Product Media",
            filters={"parent": product_name, "parenttype": _PRODUCT},
            fields=["image", "caption"],
            order_by="idx asc",
        )
    ]


def _manifest_summary(manifest: dict) -> dict:
    """The parts of a plugin manifest a shopper cares about."""
    if not manifest:
        return {}
    return {
        "id": manifest.get("id"),
        "label": manifest.get("label"),
        "output_kind": manifest.get("output_kind"),
        "render_kind": manifest.get("render_kind"),
        "inputs": manifest.get("inputs") or [],
        "parameters": sorted((manifest.get("params_schema") or {}).get("properties", {}).keys()),
        "timeout_seconds": manifest.get("timeout_seconds"),
    }


def serialize_release(row, licenses: dict, *, allow_anonymous_download: bool) -> dict:
    manifest = frappe.parse_json(row.manifest or "{}") or {}
    return {
        "name": row.name,
        "version": row.version,
        "status": row.status,
        "published_on": row.published_on,
        "artifact_hash": row.artifact_hash,
        "artifact_size": row.artifact_size,
        "license": licenses.get(row.license) or {"license_id": row.license, "name": row.license},
        "license_notes": row.license_notes,
        "release_notes": row.release_notes,
        "manifest": _manifest_summary(manifest),
        "installable": row.status == "Published",
        # Guests get the bytes only when the publisher opted in; signed-in
        # users can always download a published release. Rendered per viewer
        # by the API layer, but the policy travels with the release.
        "anonymous_download": bool(allow_anonymous_download) and row.status == "Published",
    }


def public_releases(product_name: str) -> list:
    return frappe.get_all(
        _RELEASE,
        filters={"product": product_name, "status": ["in", ("Published", "Yanked")]},
        fields=_RELEASE_FIELDS,
        order_by="published_on desc, creation desc",
    )


def _latest(releases: list[dict]) -> dict | None:
    for r in releases:
        if r["status"] == "Published":
            return r
    return None


def serialize_product(row, *, publisher: dict, categories: list[dict], releases: list[dict],
                      install_count: int, full: bool) -> dict:
    latest = _latest(releases)
    data = {
        "product_id": row.product_id,
        "title": row.title,
        "summary": row.summary,
        "artifact_kind": row.artifact_kind,
        "artifact_kind_label": kinds.label(row.artifact_kind),
        "installable_kind": kinds.is_installable(row.artifact_kind),
        "publisher": {
            "publisher_id": publisher.get("publisher_id"),
            "display_name": publisher.get("display_name"),
            "kind": publisher.get("kind"),
            "website": publisher.get("website"),
        },
        "categories": categories,
        "icon": row.icon,
        "allow_anonymous_download": bool(row.allow_anonymous_download),
        "latest_release": latest,
        "install_count": install_count,
        "updated_on": row.modified,
    }
    if full:
        data.update({
            "description": row.description,
            "docs": row.docs,
            "docs_url": row.docs_url,
            "homepage_url": row.homepage_url,
            "support_url": row.support_url,
            "publisher": {**data["publisher"], "description": publisher.get("description")},
            "media": _media_for(row.name),
            "releases": releases,
        })
    return data


def _matches(product: dict, q: str) -> bool:
    hay = " ".join(filter(None, [
        product["title"], product["summary"], product["product_id"],
        product["publisher"].get("display_name"),
        *(c["label"] for c in product["categories"]),
    ])).lower()
    return all(term in hay for term in q.lower().split())


def list_products(*, q: str | None = None, category: str | None = None, kind: str | None = None) -> list[dict]:
    """Published products of active publishers, card-sized."""
    publishers = _active_publishers()
    filters = {"status": "Published", "publisher": ["in", list(publishers) or [""]]}
    if kind:
        filters["artifact_kind"] = kind
    rows = frappe.get_all(_PRODUCT, filters=filters, fields=_PRODUCT_FIELDS, order_by="title asc")
    names = [r.name for r in rows]
    categories = _categories_by_product(names)
    licenses = _licenses()
    counts = install_mod.install_counts()

    out = []
    for row in rows:
        cats = categories.get(row.name, [])
        if category and category not in {c["category_id"] for c in cats}:
            continue
        releases = [
            serialize_release(r, licenses, allow_anonymous_download=row.allow_anonymous_download)
            for r in public_releases(row.name)
        ]
        product = serialize_product(
            row, publisher=publishers[row.publisher], categories=cats, releases=releases,
            install_count=counts.get(row.name, 0), full=False,
        )
        if q and not _matches(product, q):
            continue
        out.append(product)
    return out


def get_product(product_id: str) -> dict | None:
    """Full product page data, or None when not publicly visible."""
    rows = frappe.get_all(
        _PRODUCT, filters={"name": product_id, "status": "Published"}, fields=_PRODUCT_FIELDS,
    )
    if not rows:
        return None
    row = rows[0]
    publishers = _active_publishers()
    if row.publisher not in publishers:
        return None
    licenses = _licenses()
    releases = [
        serialize_release(r, licenses, allow_anonymous_download=row.allow_anonymous_download)
        for r in public_releases(row.name)
    ]
    return serialize_product(
        row,
        publisher=publishers[row.publisher],
        categories=_categories_by_product([row.name]).get(row.name, []),
        releases=releases,
        install_count=install_mod.install_counts().get(row.name, 0),
        full=True,
    )


def list_categories() -> list[dict]:
    return [
        {"category_id": r.name, "label": r.label, "description": r.description}
        for r in frappe.get_all(
            "WebODM Marketplace Category", fields=["name", "label", "description"], order_by="label asc",
        )
    ]
