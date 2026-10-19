"""The usage API, and organization isolation of usage through every route.

One organization's usage must never be visible to another. "Every route"
is taken literally here: the usage API, the document API, Frappe's generic
client methods, the Desk list and link-search backends, and real HTTP
requests through the WSGI application with a member's session cookie.

Fixtures are committed (the HTTP requests run on their own connection) and
removed again, committed, in ``tearDownClass``.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import patch
from urllib.parse import quote

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_test_client

from webodm_core import tenancy
from webodm_core.api import usage as usage_api
from webodm_core.metering import ledger, sweep
from webodm_core.metering import storage as storage_meter
from webodm_core.metering.testing import (
    as_user,
    at,
    make_instance,
    make_org,
    make_run,
    make_user,
    site_local,
    wipe,
)
from webodm_core.testing import make_dataset

DOCTYPE = ledger.DOCTYPE
OCTOBER = dict(start="2026-10-01", end="2026-11-01")


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org_a = make_org("Usage API Org A")
        cls.org_b = make_org("Usage API Org B")
        cls.owner_a = make_user("usage_api_owner_a@example.com", cls.org_a, "Owner")
        cls.member_a = make_user("usage_api_member_a@example.com", cls.org_a, "Member")
        cls.owner_b = make_user("usage_api_owner_b@example.com", cls.org_b, "Owner")
        cls.outsider = make_user("usage_api_outsider@example.com")
        wipe(cls.org_a, cls.org_b)

        def seed(org, user, seconds, size, plugin):
            make_instance(org, ready_at=site_local("2026-10-05T08:00:00Z"),
                          requested_at=site_local("2026-10-05T07:55:00Z"),
                          dispatched_at=site_local("2026-10-05T08:00:30Z"),
                          released_at=site_local("2026-10-05T08:00:00Z") + frappe.utils.datetime.timedelta(seconds=seconds),
                          terminated_at=None, status="Terminating")
            make_run(user, plugin=plugin, started_at=site_local("2026-10-06T08:00:00Z"),
                     completed_at=site_local("2026-10-06T08:01:00Z"))
            as_user(user)
            make_dataset(f"{org} images", images=[("IMG_0001.JPG", b"\xff" * size)])
            frappe.set_user("Administrator")
            with at("2026-10-07T00:05:00Z"):
                storage_meter.sample(org)

        seed(cls.org_a, cls.owner_a, 1111, 3000, "acme.plugin-of-a")
        seed(cls.org_b, cls.owner_b, 7777, 9000, "secret.plugin-of-b")
        with at("2026-10-08T00:00:00Z"):
            sweep.run()
        cls.names_a = set(frappe.get_all(DOCTYPE, filters={"organization": cls.org_a}, pluck="name"))
        cls.names_b = set(frappe.get_all(DOCTYPE, filters={"organization": cls.org_b}, pluck="name"))
        assert len(cls.names_a) == 3 and len(cls.names_b) == 3
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(cls.org_a, cls.org_b)
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.db.rollback()  # fixtures are committed; nothing a test does should outlive it
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    # What must never reach organization A, in any form.
    @property
    def secrets_of_b(self):
        return [self.org_b, "secret.plugin-of-b", "7777", "9000", *self.names_b]

    def assertNothingOfB(self, payload):
        text = payload if isinstance(payload, str) else frappe.as_json(payload)
        for secret in self.secrets_of_b:
            self.assertNotIn(secret, text)


class TestUsageApi(_Base):
    def test_a_member_gets_their_own_organizations_usage(self):
        for user in (self.owner_a, self.member_a):
            as_user(user)
            with at("2026-11-02T00:00:00Z"):
                answer = usage_api.summary(**OCTOBER)
            self.assertEqual(answer["organization"], self.org_a)
            self.assertEqual(answer["meters"]["compute"]["quantity"], 1111)
            self.assertEqual(answer["meters"]["analysis_run"]["quantity"], 1)
            self.assertEqual(answer["meters"]["storage"]["quantity"], 3000)
            self.assertNothingOfB(answer)

    def test_records_and_one_record(self):
        as_user(self.member_a)
        page = usage_api.records(**OCTOBER, with_evidence=1)
        self.assertEqual({r["name"] for r in page["records"]}, self.names_a)
        self.assertNothingOfB(page)
        one = usage_api.record(name=next(iter(self.names_a)))
        self.assertEqual(one["organization"], self.org_a)
        self.assertTrue(one["evidence"]["inputs"])
        compute = usage_api.records(**OCTOBER, meter="compute", limit="1", offset="0")
        self.assertEqual((compute["total"], len(compute["records"])), (1, 1))

    def test_naming_another_organization_is_refused_the_same_way_whether_it_exists_or_not(self):
        as_user(self.owner_a)
        messages = []
        for target in (self.org_b, "An Organization That Does Not Exist"):
            for call in (usage_api.summary, usage_api.records):
                with self.assertRaises(frappe.PermissionError) as caught:
                    call(organization=target, **OCTOBER)
                messages.append(str(caught.exception))
        self.assertEqual(len(set(messages)), 1)
        self.assertNothingOfB(messages[0])
        # Naming your own is fine.
        self.assertEqual(usage_api.summary(organization=self.org_a, **OCTOBER)["organization"], self.org_a)

    def test_another_organizations_record_reads_as_not_found(self):
        as_user(self.owner_a)
        messages = []
        for name in (next(iter(self.names_b)), "no-such-usage-record", None, ""):
            with self.assertRaises(frappe.DoesNotExistError) as caught:
                usage_api.record(name=name)
            messages.append(str(caught.exception))
        self.assertEqual(len(set(messages)), 1)

    def test_a_user_without_an_organization_is_refused_everything(self):
        as_user(self.outsider)
        for call, kwargs in ((usage_api.summary, OCTOBER), (usage_api.records, OCTOBER),
                             (usage_api.record, {"name": next(iter(self.names_b))}),
                             (usage_api.record, {"name": "no-such-usage-record"})):
            with self.assertRaises(tenancy.OrgContextError):
                call(**kwargs)

    def test_a_suspended_organizations_members_are_refused(self):
        frappe.db.set_value("WebODM Organization", self.org_a, "status", "Suspended")
        try:
            as_user(self.owner_a)
            with self.assertRaises(tenancy.OrgContextError):
                usage_api.summary(**OCTOBER)
        finally:
            frappe.set_user("Administrator")
            frappe.db.set_value("WebODM Organization", self.org_a, "status", "Active")

    def test_a_platform_admin_names_the_organization(self):
        frappe.set_user("Administrator")
        self.assertEqual(usage_api.summary(organization=self.org_b, **OCTOBER)["meters"]["compute"]["quantity"], 7777)
        self.assertEqual(usage_api.record(name=next(iter(self.names_b)))["organization"], self.org_b)
        with self.assertRaises(frappe.DoesNotExistError):
            usage_api.summary(organization="An Organization That Does Not Exist", **OCTOBER)
        with self.assertRaises(frappe.ValidationError):
            usage_api.summary(**OCTOBER)  # an admin has no organization of their own to default to

    def test_a_malformed_or_unbounded_question_is_refused_not_guessed(self):
        from webodm_core.metering import report

        as_user(self.member_a)
        for kwargs in ({"start": "2026-10-01 00:00:00", "end": "2026-11-01"}, {"start": None, "end": None},
                       {"start": "0001-01-01", "end": "9999-12-31"}, {"start": "2026-11-01", "end": "2026-10-01"},
                       {**OCTOBER, "as_of": "2999-01-01"}):
            with self.assertRaises(report.InvalidPeriod):
                usage_api.summary(**kwargs)

    def test_guests_are_not_allowed(self):
        for name in ("summary", "records", "record"):
            method = getattr(usage_api, name)
            self.assertIn(method, frappe.whitelisted)
            self.assertNotIn(method, frappe.guest_methods)

    def test_the_api_has_no_way_to_write(self):
        import inspect

        public = [name for name, member in inspect.getmembers(usage_api, inspect.isfunction)
                  if member in frappe.whitelisted]
        self.assertEqual(sorted(public), ["record", "records", "summary"])


class TestDocumentRoutes(_Base):
    """Frappe's own doors to a DocType, as a member of organization A."""

    def test_list_queries_return_only_own_rows(self):
        as_user(self.member_a)
        rows = frappe.get_list(DOCTYPE, fields=["name", "organization", "quantity", "dimension", "evidence"],
                               limit_page_length=0)
        self.assertEqual({r.name for r in rows}, self.names_a)
        self.assertNothingOfB(rows)
        self.assertEqual(frappe.get_list(DOCTYPE, filters={"organization": self.org_b}, limit_page_length=0), [])
        self.assertEqual(frappe.get_list(DOCTYPE, filters={"name": ["in", list(self.names_b)]},
                                         limit_page_length=0), [])
        # A filter on someone else's values is still confined to own rows.
        self.assertEqual(frappe.get_list(DOCTYPE, filters={"quantity": 7777}, limit_page_length=0), [])
        # An OR that would match everyone's rows is still ANDed with "own organization".
        widened = frappe.get_list(DOCTYPE, or_filters=[["organization", "=", self.org_b], ["dimension", "like", "%"]],
                                  pluck="organization", limit_page_length=0)
        self.assertEqual(set(widened), {self.org_a})

    def test_aggregates_cannot_see_across(self):
        as_user(self.member_a)
        from frappe.client import get_count

        self.assertEqual(get_count(DOCTYPE), 3)
        self.assertEqual(get_count(DOCTYPE, filters={"organization": self.org_b}), 0)
        totals = frappe.get_list(DOCTYPE, fields=["organization", {"SUM": "quantity", "as": "total"}],
                                 group_by="organization", order_by="organization asc", limit_page_length=0)
        self.assertEqual([(t.organization, int(t.total)) for t in totals], [(self.org_a, 1111 + 1 + 3000)])

    def test_reading_a_document_of_another_organization_is_denied(self):
        as_user(self.member_a)
        theirs = next(iter(self.names_b))
        self.assertFalse(frappe.has_permission(DOCTYPE, "read", theirs))
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc(DOCTYPE, theirs).check_permission("read")
        mine = next(iter(self.names_a))
        self.assertTrue(frappe.has_permission(DOCTYPE, "read", mine))

    def test_generic_client_methods(self):
        from frappe import client

        as_user(self.member_a)
        theirs = next(iter(self.names_b))
        self.assertEqual({r["name"] for r in client.get_list(DOCTYPE, limit_page_length=0)}, self.names_a)
        with self.assertRaises(frappe.PermissionError):
            client.get(DOCTYPE, theirs)
        for attempt in (lambda: client.get_value(DOCTYPE, "evidence", {"name": theirs}),
                        lambda: client.get_value(DOCTYPE, ["quantity", "organization"], {"organization": self.org_b})):
            try:
                self.assertFalse(attempt())
            except frappe.PermissionError:
                pass

    def test_nobody_can_write_through_the_client_either(self):
        from frappe import client

        mine = next(iter(self.names_a))
        for user in (self.member_a, self.owner_a):
            as_user(user)
            with self.assertRaises(frappe.PermissionError):
                client.set_value(DOCTYPE, mine, "quantity", 0)
            with self.assertRaises(frappe.PermissionError):
                client.delete(DOCTYPE, mine)
            with self.assertRaises(frappe.PermissionError):
                client.insert({"doctype": DOCTYPE, "organization": self.org_a, "meter": "storage", "quantity": 1,
                               "idempotency_key": "forged"})
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value(DOCTYPE, mine, "record_hash"),
                         ledger.get(mine)["record_hash"])
        self.assertTrue(frappe.db.exists(DOCTYPE, mine))

    def test_desk_list_and_link_search_backends(self):
        from frappe.desk import reportview, search

        as_user(self.member_a)
        frappe.local.form_dict = frappe._dict(doctype=DOCTYPE, fields=json.dumps(["name", "organization", "dimension"]),
                                              page_length=500, start=0)
        result = reportview.get()
        self.assertNothingOfB(result)
        self.assertIn(self.org_a, frappe.as_json(result))
        for text in ("", self.org_b[:9], "secret"):
            found = search.search_link(DOCTYPE, text)
            self.assertNothingOfB(found)
        frappe.local.form_dict = frappe._dict()

    def test_an_outsider_sees_nothing(self):
        as_user(self.outsider)
        self.assertEqual(frappe.get_list(DOCTYPE, limit_page_length=0), [])
        self.assertFalse(frappe.has_permission(DOCTYPE, "read", next(iter(self.names_a))))

    def test_a_platform_admin_sees_every_organization(self):
        frappe.set_user("Administrator")
        names = set(frappe.get_list(DOCTYPE, filters={"organization": ["in", [self.org_a, self.org_b]]},
                                    pluck="name", limit_page_length=0))
        self.assertEqual(names, self.names_a | self.names_b)


class TestOverHttp(_Base):
    """The same questions asked the way a browser or a script would ask them."""

    def _sid(self, user):
        from frappe.auth import CookieManager, LoginManager
        from frappe.utils import set_request

        set_request(path="/")
        frappe.local.cookie_manager = CookieManager()
        frappe.local.login_manager = LoginManager()
        frappe.local.login_manager.login_as(user)
        sid = frappe.session.sid
        frappe.db.commit()
        frappe.set_user("Administrator")
        return sid

    def _get(self, path, user=None):
        site = frappe.local.site
        headers = {"Cookie": f"sid={self._sid(user)}"} if user else {}
        box = {}

        def run():
            with patch("frappe.app.get_site_name", return_value=site):
                box["resp"] = get_test_client(use_cookies=False).get(path, headers=headers)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        return box["resp"]

    def test_usage_api_over_http(self):
        ok = self._get("/api/method/webodm_core.api.usage.summary?start=2026-10-01&end=2026-11-01", self.member_a)
        self.assertEqual(ok.status_code, 200, ok.data[:300])
        body = json.loads(ok.data)["message"]
        self.assertEqual((body["organization"], body["meters"]["compute"]["quantity"]), (self.org_a, 1111))
        self.assertNothingOfB(ok.data.decode())

        spoof = self._get("/api/method/webodm_core.api.usage.summary?start=2026-10-01&end=2026-11-01"
                          f"&organization={quote(self.org_b)}", self.member_a)
        self.assertEqual(spoof.status_code, 403)
        self.assertNotIn("7777", spoof.data.decode())

        theirs = next(iter(self.names_b))
        hidden = self._get(f"/api/method/webodm_core.api.usage.record?name={theirs}", self.member_a)
        missing = self._get("/api/method/webodm_core.api.usage.record?name=no-such-usage-record", self.member_a)
        self.assertEqual((hidden.status_code, missing.status_code), (404, 404))
        self.assertNotIn("secret.plugin-of-b", hidden.data.decode())

    def test_rest_resource_over_http(self):
        listing = self._get(f"/api/resource/{quote(DOCTYPE)}?limit_page_length=500"
                            '&fields=["name","organization","quantity","dimension","evidence"]', self.member_a)
        self.assertEqual(listing.status_code, 200, listing.data[:300])
        self.assertEqual({row["name"] for row in json.loads(listing.data)["data"]}, self.names_a)
        self.assertNothingOfB(listing.data.decode())

        filtered = self._get(f"/api/resource/{quote(DOCTYPE)}?limit_page_length=500"
                             f'&filters=[["organization","=","{self.org_b}"]]', self.member_a)
        self.assertEqual(json.loads(filtered.data)["data"], [])

        theirs = next(iter(self.names_b))
        direct = self._get(f"/api/resource/{quote(DOCTYPE)}/{theirs}", self.member_a)
        self.assertEqual(direct.status_code, 403)
        self.assertNotIn("secret.plugin-of-b", direct.data.decode())

        mine = next(iter(self.names_a))
        self.assertEqual(self._get(f"/api/resource/{quote(DOCTYPE)}/{mine}", self.member_a).status_code, 200)

    def test_signed_out_and_orgless_callers(self):
        path = "/api/method/webodm_core.api.usage.summary?start=2026-10-01&end=2026-11-01"
        self.assertIn(self._get(path).status_code, (401, 403))
        self.assertEqual(self._get(path, self.outsider).status_code, 403)
        self.assertIn(self._get(f"/api/resource/{quote(DOCTYPE)}").status_code, (401, 403))
        orgless = self._get(f"/api/resource/{quote(DOCTYPE)}?limit_page_length=500", self.outsider)
        if orgless.status_code == 200:
            self.assertEqual(json.loads(orgless.data)["data"], [])
        else:
            self.assertEqual(orgless.status_code, 403)
