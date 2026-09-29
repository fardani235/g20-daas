"""Marketplace: publishing (immutable releases, license rules, kinds), the
public catalog (guest-visible, Published-only), entitlement + install through
the user-plugin seam, org isolation, and the anonymous-download policy."""

import json
import os
import shutil
import tempfile

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import marketplace as market_api
from webodm_core.api import plugins as plugins_api
from webodm_core.api.test_user_plugins import MANIFEST, _join, _org, _user, _zip
from webodm_core.marketplace import catalog, kinds, publishing
from webodm_core.marketplace import install as install_mod

_PUBLISHER = "test-market-pub"
_PRODUCT = "test-elevation-mask"


def _ensure_license(license_id, permits):
    if not frappe.db.exists("WebODM License", license_id):
        frappe.get_doc({
            "doctype": "WebODM License", "license_id": license_id, "license_name": license_id,
            "permits_redistribution": permits,
        }).insert(ignore_permissions=True)
    else:
        frappe.db.set_value("WebODM License", license_id, "permits_redistribution", permits)
    return license_id


def _ensure_category(category_id, label=None):
    if not frappe.db.exists("WebODM Marketplace Category", category_id):
        frappe.get_doc({"doctype": "WebODM Marketplace Category", "category_id": category_id,
                        "label": label or category_id}).insert(ignore_permissions=True)
    return category_id


def _publisher(publisher_id=_PUBLISHER, **values):
    if frappe.db.exists("WebODM Publisher", publisher_id):
        doc = frappe.get_doc("WebODM Publisher", publisher_id)
        for k, v in values.items():
            doc.set(k, v)
        doc.save(ignore_permissions=True)
        return doc
    return frappe.get_doc({
        "doctype": "WebODM Publisher", "publisher_id": publisher_id, "display_name": "Test Publisher",
        "kind": "Partner", "status": "Active", **values,
    }).insert(ignore_permissions=True)


def _product(product_id=_PRODUCT, **values):
    return publishing.upsert_product(
        product_id, title=values.pop("title", "Elevation Mask"), publisher=values.pop("publisher", _PUBLISHER),
        artifact_kind=values.pop("artifact_kind", "plugin"), status=values.pop("status", "Published"),
        **values,
    )


def _release(product=_PRODUCT, version="1.0.0", license="MIT", manifest=None, **kw):
    path = _zip({**(manifest or MANIFEST), "version": version})
    try:
        return publishing.publish_release(product, path, license=license, **kw)
    finally:
        if os.path.exists(path):
            os.remove(path)


def _cleanup_market():
    frappe.set_user("Administrator")
    frappe.local.webodm_org_cache = {}
    for name in frappe.get_all("WebODM Entitlement", filters={"product": ["like", "test-%"]}, pluck="name"):
        frappe.delete_doc("WebODM Entitlement", name, force=True, ignore_permissions=True)
    # By organization, not by product: a manual upload over a marketplace
    # install clears `product` but the row still belongs to a test org.
    orgs = frappe.get_all("WebODM Organization", filters={"organization_name": ["like", "Market %"]}, pluck="name")
    for name in frappe.get_all("WebODM Plugin", filters={"plugin_type": "User", "organization": ["in", orgs or [""]]},
                               pluck="name"):
        plugins_api._delete_runs({"plugin": name})
        for s in frappe.get_all("WebODM Plugin Setting", filters={"plugin": name}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Setting", s, force=True, ignore_permissions=True)
        plugins_api._delete_attachments("WebODM Plugin", name)
        frappe.delete_doc("WebODM Plugin", name, force=True, ignore_permissions=True)
    for name in frappe.get_all("WebODM Product Release", filters={"product": ["like", "test-%"]}, pluck="name"):
        plugins_api._delete_attachments("WebODM Product Release", name)
        frappe.delete_doc("WebODM Product Release", name, force=True, ignore_permissions=True)
    for name in frappe.get_all("WebODM Product", filters={"name": ["like", "test-%"]}, pluck="name"):
        frappe.delete_doc("WebODM Product", name, force=True, ignore_permissions=True)


class TestKinds(FrappeTestCase):
    def test_registry(self):
        self.assertTrue(kinds.is_installable("plugin"))
        for k in ("preset", "basemap", "model"):
            self.assertIn(k, kinds.KINDS)
            self.assertFalse(kinds.is_installable(k))
        self.assertFalse(kinds.is_installable("nope"))
        with self.assertRaises(kinds.KindError):
            kinds.inspect("nope", "/dev/null")
        with self.assertRaises(kinds.NotInstallable):
            kinds.install("basemap", None, "org", "/dev/null")

    def test_plugin_inspect_wraps_package_errors(self):
        path = _zip(files={"other.py": ""})
        try:
            with self.assertRaises(kinds.KindError) as ctx:
                kinds.inspect("plugin", path)
            self.assertIn("entrypoint", str(ctx.exception))
            self.assertEqual(kinds.inspect("plugin", _zip())["id"], "elevation-mask")
        finally:
            os.remove(path)


class TestPublishing(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_license("MIT", 1)
        _ensure_license("Proprietary", 0)
        _publisher()

    def setUp(self):
        _cleanup_market()
        _product()

    def tearDown(self):
        _cleanup_market()

    def test_publish_release_stores_manifest_hash_and_attaches_artifact(self):
        rel = _release(release_notes="first", license_notes="weights: MIT")
        self.assertEqual(rel.name, f"{_PRODUCT}-1.0.0")
        self.assertEqual(rel.status, "Published")
        self.assertTrue(rel.published_on)
        self.assertEqual(len(rel.artifact_hash), 64)
        self.assertGreater(rel.artifact_size, 0)
        self.assertEqual(json.loads(rel.manifest)["id"], "elevation-mask")
        f = frappe.get_doc("File", {"file_url": rel.artifact})
        self.assertEqual(f.is_private, 1)
        self.assertEqual((f.attached_to_doctype, f.attached_to_name), ("WebODM Product Release", rel.name))

    def test_manifest_version_must_match(self):
        path = _zip({**MANIFEST, "version": "2.0.0"})
        try:
            with self.assertRaises(frappe.ValidationError) as ctx:
                publishing.publish_release(_PRODUCT, path, license="MIT", version="1.0.0")
            self.assertIn("does not match", str(ctx.exception))
        finally:
            os.remove(path)
        self.assertFalse(frappe.db.exists("WebODM Product Release", f"{_PRODUCT}-1.0.0"))

    def test_duplicate_version_and_bad_version_rejected(self):
        _release()
        with self.assertRaises(ValueError):
            _release()
        path = _zip({**MANIFEST, "version": "1.1.0"})
        try:
            with self.assertRaises(frappe.ValidationError):
                publishing.publish_release(_PRODUCT, path, license="MIT", version="latest")
        finally:
            os.remove(path)

    def test_license_is_mandatory(self):
        with self.assertRaises(frappe.ValidationError):
            _release(license=None)

    def test_published_release_is_frozen_but_can_be_yanked(self):
        rel = _release()
        other = _zip({**MANIFEST, "version": "1.0.0", "label": "Other"})
        try:
            from webodm_core.plugins.files import save_private_file_from_path
            f = save_private_file_from_path(other, "other.zip", ignore_permissions=True, move=False)
        finally:
            os.remove(other)
        doc = frappe.get_doc("WebODM Product Release", rel.name)
        doc.artifact = f.file_url
        with self.assertRaises(frappe.ValidationError) as ctx:
            doc.save(ignore_permissions=True)
        self.assertIn("cannot change", str(ctx.exception))

        doc = frappe.get_doc("WebODM Product Release", rel.name)
        doc.license = "Proprietary"
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

        doc = frappe.get_doc("WebODM Product Release", rel.name)
        doc.release_notes = "edited"
        doc.status = "Yanked"
        doc.save(ignore_permissions=True)
        doc.reload()
        self.assertEqual((doc.status, doc.release_notes), ("Yanked", "edited"))
        self.assertEqual(doc.artifact, rel.artifact)

    def test_all_releases_must_ship_the_same_plugin(self):
        _release()
        with self.assertRaises(frappe.ValidationError) as ctx:
            _release(version="1.1.0", manifest={**MANIFEST, "id": "something-else"})
        self.assertIn("same plugin", str(ctx.exception))

    def test_non_installable_kind_cannot_be_published(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            _product("test-basemap", title="Base", artifact_kind="basemap", status="Published")
        self.assertIn("cannot be published yet", str(ctx.exception))
        draft = _product("test-basemap", title="Base", artifact_kind="basemap", status="Draft")
        self.assertEqual(draft.status, "Draft")

    def test_slugs_are_validated(self):
        with self.assertRaises(frappe.ValidationError):
            _product("Bad Id", title="x")
        with self.assertRaises(frappe.ValidationError):
            _publisher("Bad Publisher")

    def test_anonymous_download_requires_redistributable_licenses(self):
        _release(license="Proprietary")
        product = frappe.get_doc("WebODM Product", _PRODUCT)
        product.allow_anonymous_download = 1
        with self.assertRaises(frappe.ValidationError) as ctx:
            product.save(ignore_permissions=True)
        self.assertIn("Proprietary", str(ctx.exception))

        # Yanking the restrictive release clears the way...
        frappe.db.set_value("WebODM Product Release", f"{_PRODUCT}-1.0.0", "status", "Yanked")
        product = frappe.get_doc("WebODM Product", _PRODUCT)
        product.allow_anonymous_download = 1
        product.save(ignore_permissions=True)
        _release(version="1.1.0", license="MIT")

        # ...and with the switch on, a restrictive release cannot be published.
        with self.assertRaises(frappe.ValidationError) as ctx:
            _release(version="1.2.0", license="Proprietary")
        self.assertIn("does not permit redistribution", str(ctx.exception))
        self.assertEqual(
            frappe.get_doc("WebODM Product Release", f"{_PRODUCT}-1.2.0", ).status if
            frappe.db.exists("WebODM Product Release", f"{_PRODUCT}-1.2.0") else "absent", "absent",
        )


class TestCatalogAndInstall(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_license("MIT", 1)
        _ensure_license("Proprietary", 0)
        _ensure_category("terrain", "Terrain")
        _ensure_category("3d", "3D")
        _publisher()
        cls.owner = _user("market_owner@example.com")
        cls.member = _user("market_member@example.com")
        cls.outsider = _user("market_outsider@example.com")
        cls.orgless = _user("market_orgless@example.com")
        cls.org = _org("Market Org")
        cls.org_other = _org("Market Other Org")
        _join(cls.owner, cls.org)
        _join(cls.member, cls.org, role="Member")
        _join(cls.outsider, cls.org_other)
        cls.slug = frappe.db.get_value("WebODM Organization", cls.org, "slug")
        cls.plugin_id = f"{cls.slug}.elevation-mask"

    @classmethod
    def tearDownClass(cls):
        _cleanup_market()
        super().tearDownClass()

    def setUp(self):
        _cleanup_market()
        _product(summary="Mask cells above a threshold", categories=["terrain"])
        self.release = _release()

    def tearDown(self):
        _cleanup_market()

    def _as(self, user):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}

    # --- catalog ---

    def test_guest_lists_published_products_only(self):
        _product("test-draft", title="Draft", status="Draft")
        _publisher("test-suspended-pub", status="Suspended")
        _product("test-suspended", title="Susp", publisher="test-suspended-pub")
        _release("test-suspended")

        self._as("Guest")
        data = market_api.list_products()
        ids = {p["product_id"] for p in data["products"]}
        self.assertIn(_PRODUCT, ids)
        self.assertNotIn("test-draft", ids)
        self.assertNotIn("test-suspended", ids)
        self.assertEqual(data["viewer"], {"signed_in": False, "organization": None, "can_install": False})
        self.assertIn("terrain", {c["category_id"] for c in data["categories"]})

        card = next(p for p in data["products"] if p["product_id"] == _PRODUCT)
        self.assertEqual(card["latest_release"]["version"], "1.0.0")
        self.assertEqual(card["latest_release"]["license"]["license_id"], "MIT")
        self.assertEqual(card["publisher"]["display_name"], "Test Publisher")
        self.assertEqual([c["category_id"] for c in card["categories"]], ["terrain"])
        self.assertNotIn("releases", card)  # card, not page
        for key in ("organization", "plugin", "settings"):
            self.assertNotIn(key, card)

        with self.assertRaises(frappe.DoesNotExistError):
            market_api.get_product("test-draft")
        with self.assertRaises(frappe.DoesNotExistError):
            market_api.get_product("test-suspended")

    def test_search_and_filters(self):
        _product("test-recon", title="3D Reconstruction", summary="Meshes", categories=["3d"])
        _release("test-recon", manifest={**MANIFEST, "id": "recon"})
        self.assertEqual({p["product_id"] for p in catalog.list_products(q="mask")}, {_PRODUCT})
        self.assertEqual({p["product_id"] for p in catalog.list_products(q="test publisher elevation")}, {_PRODUCT})
        self.assertEqual({p["product_id"] for p in catalog.list_products(category="3d")}, {"test-recon"})
        self.assertEqual(catalog.list_products(kind="basemap"), [])
        self.assertEqual(catalog.list_products(q="nothing-like-this"), [])

    def test_product_page_hides_drafts_and_marks_yanked(self):
        _release(version="1.1.0")
        yanked = _release(version="1.2.0")
        frappe.db.set_value("WebODM Product Release", yanked.name, "status", "Yanked")
        path = _zip({**MANIFEST, "version": "2.0.0"})
        try:
            publishing.publish_release(_PRODUCT, path, license="MIT", status="Draft")
        finally:
            os.remove(path)

        self._as("Guest")
        page = market_api.get_product(_PRODUCT)
        versions = [(r["version"], r["status"], r["installable"]) for r in page["releases"]]
        self.assertEqual(versions, [("1.2.0", "Yanked", False), ("1.1.0", "Published", True),
                                    ("1.0.0", "Published", True)])
        self.assertEqual(page["latest_release"]["version"], "1.1.0")
        self.assertEqual(page["releases"][1]["manifest"]["inputs"], MANIFEST["inputs"])
        self.assertEqual(page["releases"][1]["manifest"]["parameters"], ["threshold"])
        self.assertEqual(page["install_count"], 0)

    # --- install ---

    def test_org_admin_installs_through_the_plugin_seam(self):
        self._as(self.owner)
        result = market_api.install_product(_PRODUCT)
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["installed"]["name"], self.plugin_id)
        self.assertTrue(result["installed"]["enabled"])
        self.assertEqual(result["installed"]["source"], "Marketplace")

        row = frappe.get_doc("WebODM Plugin", self.plugin_id)
        self.assertEqual((row.plugin_type, row.organization, row.source), ("User", self.org, "Marketplace"))
        self.assertEqual((row.product, row.release), (_PRODUCT, self.release.name))
        self.assertEqual(row.package_hash, self.release.artifact_hash)
        self.assertNotEqual(row.package, self.release.artifact)  # own copy

        ent = frappe.get_doc("WebODM Entitlement", result["entitlement"])
        self.assertEqual((ent.organization, ent.product, ent.release, ent.plugin, ent.status),
                         (self.org, _PRODUCT, self.release.name, self.plugin_id, "Active"))
        self.assertEqual(ent.installed_by, self.owner)

        page = market_api.get_product(_PRODUCT)
        self.assertTrue(page["viewer"]["can_install"])
        self.assertEqual(page["viewer"]["entitlement"]["version"], "1.0.0")
        self.assertFalse(page["viewer"]["entitlement"]["update_available"])
        self.assertEqual(page["install_count"], 1)
        self.assertIn(self.plugin_id, {p["name"] for p in plugins_api.list_plugins()})

    def test_member_and_orgless_user_cannot_install(self):
        self._as(self.member)
        page = market_api.get_product(_PRODUCT)
        self.assertTrue(page["viewer"]["signed_in"])
        self.assertFalse(page["viewer"]["can_install"])
        with self.assertRaises(frappe.PermissionError):
            market_api.install_product(_PRODUCT)

        self._as(self.orgless)
        self.assertFalse(market_api.get_product(_PRODUCT)["viewer"]["can_install"])
        with self.assertRaises(frappe.PermissionError):
            market_api.install_product(_PRODUCT)

        self._as("Guest")
        with self.assertRaises(frappe.PermissionError):
            market_api.install_product(_PRODUCT)
        self.assertFalse(frappe.db.exists("WebODM Plugin", self.plugin_id))
        self.assertFalse(frappe.db.exists("WebODM Entitlement", {"product": _PRODUCT}))

    def test_installs_are_isolated_per_organization(self):
        self._as(self.owner)
        market_api.install_product(_PRODUCT)
        self._as(self.outsider)
        other = market_api.install_product(_PRODUCT)
        self.assertNotEqual(other["installed"]["name"], self.plugin_id)
        self.assertNotIn(self.plugin_id, {p["name"] for p in plugins_api.list_plugins()})
        self.assertEqual(market_api.get_product(_PRODUCT)["install_count"], 2)
        # Each org sees only its own entitlement, through the API and /api/resource.
        self.assertEqual(set(market_api.my_entitlements()), {_PRODUCT})
        self.assertEqual(frappe.db.count("WebODM Entitlement", {"product": _PRODUCT}), 2)
        self.assertEqual(len(frappe.get_list("WebODM Entitlement", filters={"product": _PRODUCT})), 1)
        self._as(self.member)
        visible = frappe.get_list("WebODM Entitlement", filters={"product": _PRODUCT}, pluck="organization")
        self.assertEqual(visible, [self.org])

    def test_upgrade_keeps_settings_and_moves_the_entitlement(self):
        self._as(self.owner)
        market_api.install_product(_PRODUCT)
        plugins_api.save_plugin_setting(plugin=self.plugin_id, enabled=False, settings={"threshold": 5})

        self._as("Administrator")
        newer = _release(version="1.1.0")
        self._as(self.owner)
        self.assertTrue(market_api.get_product(_PRODUCT)["viewer"]["entitlement"]["update_available"])

        result = market_api.install_product(_PRODUCT)
        self.assertEqual(result["version"], "1.1.0")
        self.assertFalse(result["installed"]["created"])
        self.assertFalse(result["installed"]["enabled"])  # org's choice kept
        self.assertEqual(result["installed"]["settings"], {"threshold": 5})
        row = frappe.get_doc("WebODM Plugin", self.plugin_id)
        self.assertEqual((row.version, row.release), ("1.1.0", newer.name))
        ent = frappe.get_doc("WebODM Entitlement", {"organization": self.org, "product": _PRODUCT})
        self.assertEqual(ent.release, newer.name)
        self.assertEqual(frappe.db.count("WebODM Entitlement", {"product": _PRODUCT}), 1)
        self.assertFalse(market_api.get_product(_PRODUCT)["viewer"]["entitlement"]["update_available"])

        # An explicit older release can still be chosen.
        result = market_api.install_product(_PRODUCT, release=self.release.name)
        self.assertEqual(result["version"], "1.0.0")

    def test_yanked_retired_and_suspended_are_not_installable(self):
        frappe.db.set_value("WebODM Product Release", self.release.name, "status", "Yanked")
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError):
            market_api.install_product(_PRODUCT, release=self.release.name)
        with self.assertRaises(frappe.ValidationError) as ctx:
            market_api.install_product(_PRODUCT)
        self.assertIn("no installable release", str(ctx.exception))

        self._as("Administrator")
        frappe.db.set_value("WebODM Product Release", self.release.name, "status", "Published")
        _publisher(status="Suspended")
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError):
            market_api.install_product(_PRODUCT)
        self._as("Administrator")
        _publisher(status="Active")
        frappe.db.set_value("WebODM Product", _PRODUCT, "status", "Retired")
        self._as(self.owner)
        with self.assertRaises(frappe.DoesNotExistError):
            market_api.install_product(_PRODUCT)
        with self.assertRaises(frappe.DoesNotExistError):
            market_api.get_product(_PRODUCT)

    def test_install_verifies_artifact_integrity(self):
        frappe.db.set_value("WebODM Product Release", self.release.name, "artifact_hash", "0" * 64)
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError) as ctx:
            market_api.install_product(_PRODUCT)
        self.assertIn("integrity", str(ctx.exception))
        self.assertFalse(frappe.db.exists("WebODM Plugin", self.plugin_id))
        spool = frappe.get_site_path("private", "files", "plugin_packages")
        self.assertEqual([f for f in os.listdir(spool) if f.startswith("market-")], [])

    def test_uninstall_from_marketplace_or_plugins_page_closes_the_entitlement(self):
        self._as(self.owner)
        market_api.install_product(_PRODUCT)
        market_api.uninstall_product(_PRODUCT)
        self.assertFalse(frappe.db.exists("WebODM Plugin", self.plugin_id))
        ent = frappe.get_doc("WebODM Entitlement", {"organization": self.org, "product": _PRODUCT})
        self.assertEqual((ent.status, ent.plugin), ("Removed", None))
        self.assertTrue(ent.removed_on)
        self.assertIsNone(market_api.get_product(_PRODUCT)["viewer"]["entitlement"])
        with self.assertRaises(frappe.DoesNotExistError):
            market_api.uninstall_product(_PRODUCT)

        market_api.install_product(_PRODUCT)
        self.assertEqual(frappe.db.count("WebODM Entitlement", {"product": _PRODUCT}), 1)
        plugins_api.remove_plugin(self.plugin_id)
        ent.reload()
        self.assertEqual(ent.status, "Removed")

    def test_manual_upload_over_a_marketplace_install_detaches_it(self):
        self._as(self.owner)
        market_api.install_product(_PRODUCT)
        plugins_api.install_user_plugin(_zip({**MANIFEST, "version": "9.9.9"}), self.org)
        row = frappe.get_doc("WebODM Plugin", self.plugin_id)
        self.assertEqual((row.source, row.product, row.release, row.version), ("Upload", None, None, "9.9.9"))
        ent = frappe.get_doc("WebODM Entitlement", {"organization": self.org, "product": _PRODUCT})
        self.assertEqual(ent.status, "Removed")
        self.assertIsNone(market_api.get_product(_PRODUCT)["viewer"]["entitlement"])

    def test_seam_rejects_inconsistent_provenance(self):
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError):
            plugins_api.install_user_plugin(_zip(), self.org, source="Marketplace")
        with self.assertRaises(frappe.ValidationError):
            plugins_api.install_user_plugin(_zip(), self.org, source="Torrent")

    # --- download policy ---

    def test_download_policy(self):
        self._as("Guest")
        with self.assertRaises(frappe.PermissionError):
            market_api.downloadable_release(self.release.name)
        page = market_api.get_product(_PRODUCT)
        self.assertIsNone(page["latest_release"]["download_url"])
        self.assertFalse(page["latest_release"]["anonymous_download"])

        self._as(self.member)
        self.assertEqual(market_api.downloadable_release(self.release.name).name, self.release.name)
        self.assertIn("download_release?release=", market_api.get_product(_PRODUCT)["latest_release"]["download_url"])

        self._as("Administrator")
        product = frappe.get_doc("WebODM Product", _PRODUCT)
        product.allow_anonymous_download = 1
        product.save(ignore_permissions=True)
        self._as("Guest")
        self.assertEqual(market_api.downloadable_release(self.release.name).name, self.release.name)
        page = market_api.get_product(_PRODUCT)
        self.assertTrue(page["latest_release"]["anonymous_download"])
        self.assertTrue(page["latest_release"]["download_url"])

        frappe.db.set_value("WebODM Product Release", self.release.name, "status", "Yanked")
        with self.assertRaises(frappe.DoesNotExistError):
            market_api.downloadable_release(self.release.name)
        with self.assertRaises(frappe.DoesNotExistError):
            market_api.downloadable_release("no-such-release")
