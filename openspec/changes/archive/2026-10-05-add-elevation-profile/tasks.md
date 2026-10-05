## 1. Geospatial service

- [x] 1.1 `app/utils/potree_octree.py`: range sources (local `pread`, S3 `Range`), hierarchy parsing with proxy chunks in waves, child boxes, coalesced concurrent reads, structured-array decoding
- [x] 1.2 `app/utils/profile.py`: transect validation, corridor SAT test, level trimming to a budget, nearest-segment assignment, sort-based group stats, class layers, ground (class-2 median / filtered low envelope), gap interpolation, hysteresis climb, gap-free grade windows, response document
- [x] 1.3 `POST /pointcloud/profile` and `GET /pointcloud/profile/limits`; path validation, status codes
- [x] 1.4 Tests: synthetic Potree writer + scene, 31 tests incl. moto S3 and a real-converter check; validated on a 4.86 M-point survey against laspy

## 2. Frappe

- [x] 2.1 `processing.potree.file_sources` (Ready state, ownership, cache-first / S3-second) and `OctreeNotReady`
- [x] 2.2 `plugins.geospatial.pointcloud_profile` client with error mapping
- [x] 2.3 `api.pointcloud.profile` (permission, validation, proxy) and 23 unit tests with `test_tiles`

## 3. Frontend

- [x] 3.1 `profile` measurement kind (`lib/potree.js`), finish → `onProfile` (`useModelViewer`), transect / corridor / sampled lines / marker (`pointCloudLayer`), toolbar button
- [x] 3.2 `lib/profile.js`: API call, scales (fit / 1 : 1), gap-aware paths, hover, stats, CSV, settings
- [x] 3.3 `ProfileChart.vue`, `ProfilePanel.vue`, dock + state in `ModelView.vue`
- [x] 3.4 Tests: `lib/profile.test.js`, `ProfileChart.test.js`, `ProfilePanel.test.js`, potree kind tests; e2e profile section

## 4. Platform and docs

- [x] 4.1 `PROFILE_MAX_POINTS` / `PROFILE_MAX_BINS` in Dockerfile, compose, Helm values + template; CI runs `tests/test_profile.py` in the image
- [x] 4.2 `docs/point-cloud/elevation-profile.md`, point cloud README, geospatial README, SPEC / TODO / README, AGENTS.md phase entry, spec delta + `openspec/specs/point-cloud-viewer/spec.md`
