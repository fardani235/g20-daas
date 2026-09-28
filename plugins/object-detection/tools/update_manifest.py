"""Keep ``plugin.json`` in step with the model cards.

The platform validates a run's ``model`` parameter against the manifest's
enum before the plugin ever starts, so the enum must list every card. Rather
than editing it by hand this script rewrites ``params_schema.properties.model``
(enum, default and the description's model list) from ``models/*.json``.
``tools/build.sh`` runs it; ``--check`` (used by the tests) exits 1 when the
manifest is stale.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
MANIFEST = os.path.join(PLUGIN_DIR, "plugin.json")
MODELS = os.path.join(PLUGIN_DIR, "models")

sys.path.insert(0, PLUGIN_DIR)
from detplugin.registry import default_model_id, load_cards  # noqa: E402


def model_property(cards) -> dict:
    default = default_model_id(cards)
    ids = [default] + [i for i in cards.all_ids if i != default]
    lines = "; ".join(f"{cards[i].id}: {cards[i].label}" for i in ids)
    return {
        "type": "string",
        "title": "Model",
        "description": f"Detector to run (a model card in models/). {lines}. "
                       f"Parameters left at 0/-1 use the card's recommended values.",
        "enum": ids,
        "default": default,
    }


def render(manifest: dict, cards) -> str:
    manifest = json.loads(json.dumps(manifest))
    manifest["params_schema"]["properties"]["model"] = model_property(cards)
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if plugin.json is out of date")
    args = ap.parse_args(argv)

    cards = load_cards(MODELS, require_files=False)
    with open(MANIFEST, encoding="utf-8") as f:
        current = f.read()
    rendered = render(json.loads(current), cards)
    if rendered == current:
        print("plugin.json is up to date")
        return 0
    if args.check:
        print("plugin.json is out of date with models/*.json; run tools/update_manifest.py", file=sys.stderr)
        return 1
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write(rendered)
    print(f"plugin.json updated: model enum = {', '.join(model_property(cards)['enum'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
