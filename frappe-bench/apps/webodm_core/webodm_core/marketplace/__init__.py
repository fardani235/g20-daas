"""Marketplace: global product listings, publishers, releases, licenses and
per-organization entitlements.

The catalog (``WebODM Publisher`` / ``WebODM Product`` / ``WebODM Product
Release`` / ``WebODM License``) carries no organization and is readable by
everyone, signed-in or not. Acquiring a product creates a ``WebODM
Entitlement`` for the caller's organization and installs the artifact
through the kind's installer — for plugins that is
``api/plugins.install_user_plugin``, the same path a manual upload takes, so
the installed row is org-scoped and sandboxed exactly like an uploaded one.

Modules: ``kinds`` (artifact kind registry), ``catalog`` (public
serialization and search), ``install`` (entitlement + install/uninstall),
``publishing`` (create releases from a file on disk; ``bench execute``).
"""
