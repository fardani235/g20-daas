"""Install / migrate lifecycle hooks.

Frappe's ``installer.install_app`` calls ``set_all_patches_as_completed`` with
``set_as_patched=True`` on a fresh install, which inserts a "Patch Log" row for
every patch in ``patches.txt`` *without executing it*. As a result the
``seed_system_presets`` and ``seed_processing_node`` patches never run during
``bench new-site``, and a fresh site comes up with an empty preset catalog and
no processing node (the Patch Log claims otherwise).

These hooks run the same seeders at lifecycle points that do fire:
``after_install`` on a fresh install, and ``after_migrate`` so a site whose
patch was falsely marked complete self-heals on the next migrate. Both seeders
are idempotent — presets are upserted by name, the node skips if present — so
repeated calls are safe and this never duplicates rows.
"""

from webodm_core.patches import seed_processing_node, seed_system_presets


def seed_defaults():
	"""Seed the platform-global defaults. Idempotent."""
	seed_system_presets.execute()
	seed_processing_node.execute()


def after_install():
	seed_defaults()


def after_migrate():
	seed_defaults()
