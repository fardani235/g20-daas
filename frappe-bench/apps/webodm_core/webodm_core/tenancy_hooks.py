"""Shared doc_events hook that stamps the acting user's organization onto every
tenant-owned document at insert time. Org is derived from the actor, never from
the request payload, so callers cannot assign a document to another org.

Exception: platform-global rows (system presets, created by the
seed_system_presets migrate patch as Administrator) have no org and must be
skipped — they are visible to every tenant, gated in permissions.py instead."""
import frappe

from webodm_core import tenancy


def _is_platform_global(doc):
    # System presets are shared across all orgs; they carry no organization.
    return getattr(doc, "system", 0) and int(doc.system) == 1


def _is_migration_copy(doc):
    """A row a migration patch derives from an existing tenant row.

    Patches run as Administrator with no session org, but a backfill (e.g.
    ``patches.backfill_task_datasets`` splitting a dataset out of a task) must
    keep the *source row's* organization. The bypass needs both the document
    flag and the process-level patch/migrate flag, which is never set while a
    request is served, so a payload can never use it.
    """
    return bool(
        doc.flags.get("organization_from_source")
        and doc.organization
        and (frappe.flags.in_patch or frappe.flags.in_migrate)
    )


def stamp_organization(doc, method=None):
    if _is_platform_global(doc):
        doc.organization = None
        return
    if _is_migration_copy(doc):
        return
    # Always overwrite: read_only field, spoof-proof, deny-by-default via require_org.
    doc.organization = tenancy.require_org()
