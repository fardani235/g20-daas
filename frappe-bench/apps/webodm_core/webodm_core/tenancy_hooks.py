"""Shared doc_events hook that stamps the acting user's organization onto every
tenant-owned document at insert time. Org is derived from the actor, never from
the request payload, so callers cannot assign a document to another org.

Exception: platform-global rows (system presets, created by the
seed_system_presets migrate patch as Administrator) have no org and must be
skipped — they are visible to every tenant, gated in permissions.py instead."""
from webodm_core import tenancy


def _is_platform_global(doc):
    # System presets are shared across all orgs; they carry no organization.
    return getattr(doc, "system", 0) and int(doc.system) == 1


def stamp_organization(doc, method=None):
    if _is_platform_global(doc):
        doc.organization = None
        return
    # Always overwrite: read_only field, spoof-proof, deny-by-default via require_org.
    doc.organization = tenancy.require_org()
