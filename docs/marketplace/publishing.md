# Publishing to the marketplace

Publishing is a platform-admin task in this version. Two tools: Frappe Desk
(`https://admin.<domain>/desk`) for everything, and
`bench execute` for scripted release uploads (CI, first-party plugins).
Concepts and rules: [`README.md`](README.md).

---

## 1. One-time: reference data

`bench migrate` / a fresh install seed the license registry, browse
categories and the first-party publisher (`g20-tech`) via
`webodm_core/patches/seed_marketplace.py`. Add or edit in Desk:

- **WebODM License** — `license_id` (SPDX where one exists), name, URL,
  summary, and `permits_redistribution`. That checkbox decides whether
  products released under the license may offer anonymous downloads; the
  seeder re-asserts it on every migrate for the seeded rows.
- **WebODM Marketplace Category** — slug + label.
- **WebODM Publisher** — slug, display name, `First-party` | `Partner`,
  website, contact, `Active` | `Suspended`.

## 2. Create a product (Desk → WebODM Product)

1. `Product ID`: lowercase slug, becomes the URL (`/marketplace/<id>`).
2. Title, summary (≤ 200 chars, shown on cards), publisher, artifact kind
   (`plugin`), categories, icon (public image), Markdown description and
   docs, links, media rows (public images).
3. Leave `Status = Draft` until the first release is published.
4. `Allow Anonymous Download` stays off unless every published release's
   license permits redistribution (the save is refused otherwise).

## 3. Publish a release

### In Desk (WebODM Product Release → New)

1. Product, version (`1.2.0`; for plugins it must equal the manifest
   version), status `Published` (or `Draft` to stage), license, optional
   license notes ("Weights: DeepForest (MIT)") and release notes.
2. Attach the zip in *Artifact* as a **private** file.
3. Save. The controller validates the package like an upload
   (`plugins/package.inspect_package`), records sha-256, size and the
   normalized manifest, checks the manifest id matches earlier releases, and
   sets `published_on`.

### From a shell

Build the package first, e.g. `plugins/object-detection/tools/build.sh`
(fetch model weights before building where the plugin needs them), then:

```bash
bench --site <site> execute webodm_core.marketplace.publishing.publish_release \
  --kwargs '{"product": "object-detection",
             "artifact_path": "/path/to/object-detection-1.0.0.zip",
             "license": "MIT",
             "license_notes": "Default weights: DeepForest tree crowns (MIT).",
             "release_notes": "Initial marketplace release."}'
```

`version` defaults to the manifest version. The file is copied (not moved)
into private files and attached to the new release. A product can be created
or updated the same way:

```bash
bench --site <site> execute webodm_core.marketplace.publishing.upsert_product \
  --kwargs '{"product_id": "object-detection", "title": "Object Detection",
             "publisher": "g20-tech", "status": "Published",
             "summary": "Detect and count objects on the orthophoto.",
             "categories": ["detection", "vegetation"],
             "docs_url": "https://github.com/<org>/<repo>/blob/master/docs/plugins/object-detection.md"}'
```

Inside the compose stack, run these through the Frappe image with
`FRAPPE_ROLE=exec` (see `AGENTS.md`, Phase 10) and a bind mount for the zip.

## 4. After publishing

- Set the product to `Published` (Desk) if it was a draft. It now appears on
  `/marketplace` for everyone; organizations with an older version see
  *Update to vX*.
- **Never edit a published artifact.** Status `Published ↔ Yanked`, release
  notes and license notes remain editable; artifact, hash, manifest and
  license are frozen. Publish the next version instead.
- **Yank** a broken release: it stays listed as *Yanked*, cannot be installed,
  and organizations that already installed it are not affected.
- **Retire** a product to hide it and stop installs without deleting anything.
- **Suspend** a publisher to do the same for all of its products.

## 5. Checklist for first-party plugins

| Plugin | Artifact | License | Notes |
|---|---|---|---|
| `docs/plugins/examples/elevation-mask` | zip of `plugin.json` + `main.py` | MIT | Starter example; safe for anonymous download |
| `plugins/semantic-segmentation` | `tools/build.sh` (two zips: raster and polygons) | MIT (code) | FLAIR U-Net weights are Etalab-2.0, SegFormer MIT — list them in license notes |
| `plugins/object-detection` | `tools/build.sh` after `tools/fetch_models.py` | MIT (code) | DeepForest MIT; VisDrone YOLO is **AGPL-3.0** — publish it as a separate product or state the terms in notes; do not enable anonymous download unless every bundled component permits it |
| `plugins/3d-reconstruction` | `tools/build.sh` | MIT | — |

Each of these needs a product (Desk or `upsert_product`) and one
`publish_release` per version.
