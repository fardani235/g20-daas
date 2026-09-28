"""Fetch the model weights that are not committed to the repository.

No detector weights ship in git: they are large, and the YOLO exports trained
with Ultralytics are AGPL-3.0. Every model card in ``models/`` that names a
``source.url`` and a ``sha256`` can be downloaded and verified with this
script, which is the fetch-at-build step run before ``tools/build.sh``:

    python3 tools/fetch_models.py                 # every card (prints each licence)
    python3 tools/fetch_models.py --only deepforest-tree-crowns
    python3 tools/fetch_models.py --skip visdrone-yolov11s
    python3 tools/fetch_models.py --list          # show cards, licences and status

A card whose weights were not fetched is still valid: the plugin reports that
model as unavailable at run time and the other models keep working. To ship
your own detector, add a card and its ``.onnx`` file to ``models/`` instead
(see README.md); ``sha256`` is optional for local files.
"""

import argparse
import hashlib
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
MODELS = os.path.join(PLUGIN_DIR, "models")

sys.path.insert(0, PLUGIN_DIR)
from detplugin.registry import load_cards  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, expected_sha):
    if os.path.isfile(dest) and sha256(dest) == expected_sha:
        print(f"already have {os.path.relpath(dest, PLUGIN_DIR)}")
        return
    print(f"downloading {url}")
    tmp = dest + ".part"
    urllib.request.urlretrieve(url, tmp)
    actual = sha256(tmp)
    if actual != expected_sha:
        os.remove(tmp)
        raise SystemExit(f"checksum mismatch for {url}: expected {expected_sha}, got {actual}")
    os.replace(tmp, dest)
    print(f"saved {os.path.relpath(dest, PLUGIN_DIR)} ({os.path.getsize(dest) / 1e6:.1f} MB)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[], metavar="ID", help="fetch only this card (repeatable)")
    ap.add_argument("--skip", action="append", default=[], metavar="ID", help="do not fetch this card (repeatable)")
    ap.add_argument("--list", action="store_true", help="list cards and exit")
    args = ap.parse_args(argv)

    cards = load_cards(MODELS, require_files=False)
    unknown = [i for i in args.only + args.skip if i not in cards]
    if unknown:
        raise SystemExit(f"unknown model id(s): {', '.join(unknown)}; cards: {', '.join(sorted(cards))}")

    for cid in sorted(cards):
        card = cards[cid]
        present = os.path.isfile(card.model_path)
        url = card.source.get("url")
        if args.list:
            status = "present" if present else ("fetchable" if url and card.sha256 else "supply models/" + card.file)
            print(f"{cid:28s} {card.license:10s} {status}")
            continue
        if args.only and cid not in args.only:
            continue
        if cid in args.skip:
            print(f"skipping {cid}")
            continue
        if not url or not card.sha256:
            print(f"{cid}: no source url/sha256 in its card; " + (
                "weights present" if present else f"add models/{card.file} yourself"))
            continue
        print(f"{cid}: licence {card.license}")
        download(url, card.model_path, card.sha256)
    return 0


if __name__ == "__main__":
    sys.exit(main())
