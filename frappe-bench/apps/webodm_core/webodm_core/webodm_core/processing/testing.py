"""Test helpers for the processing package. Not imported by production code."""

from __future__ import annotations

from contextlib import contextmanager

import frappe


@contextmanager
def patch_local(name: str, value):
    """Temporarily set ``frappe.local.<name>`` and restore it afterwards.

    ``unittest.mock.patch.object(frappe.local, name, value, create=True)`` cannot
    be used for this. ``frappe.local`` is a contextvar-backed ``Local`` with
    ``__slots__ = ()`` and no ``__dict__``, so mock cannot tell the attribute was
    already set: it records the value as non-local and **deletes the attribute**
    on exit instead of restoring it. That silently breaks every later test in the
    same runner batch that reads ``frappe.local.<name>`` — e.g. a plain
    ``unittest.TestCase`` reading ``frappe.local.site``, which (unlike a
    ``FrappeTestCase``) never re-runs ``frappe.init`` to put it back.
    """
    missing = object()
    original = getattr(frappe.local, name, missing)
    setattr(frappe.local, name, value)
    try:
        yield
    finally:
        if original is missing:
            try:
                delattr(frappe.local, name)
            except AttributeError:
                pass
        else:
            setattr(frappe.local, name, original)
