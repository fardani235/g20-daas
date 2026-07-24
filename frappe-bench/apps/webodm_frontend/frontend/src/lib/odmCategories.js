// Static category map for NodeODM processing options. NodeODM's /options
// endpoint returns a flat list with no category metadata, so we define the
// grouping here. Any option NOT named below falls into a trailing "Advanced"
// catch-all, so new/unknown options are surfaced rather than dropped.
export const ODM_CATEGORIES = [
  { name: 'General', options: [
    'auto-boundary', 'auto-boundary-distance', 'boundary', 'crop',
    'fast-orthophoto', 'feature-quality', 'feature-type', 'min-num-features',
    'matcher-neighbors', 'matcher-order', 'matcher-type', 'sfm-algorithm',
    'max-concurrency', 'use-exif', 'ignore-gsd',
  ] },
  { name: 'Elevation (DEM)', options: [
    'dsm', 'dtm', 'dem-resolution', 'dem-decimation', 'dem-euclidean-map',
    'dem-gapfill-steps', 'smrf-scalar', 'smrf-slope', 'smrf-threshold',
    'smrf-window',
  ] },
  { name: 'Orthophoto', options: [
    'orthophoto-resolution', 'orthophoto-compression', 'orthophoto-cutline',
    'orthophoto-kmz', 'orthophoto-no-tiled', 'orthophoto-png', 'skip-orthophoto',
    'cog', 'build-overviews', 'tiles',
  ] },
  { name: 'Point Cloud', options: [
    'pc-quality', 'pc-classify', 'pc-copc', 'pc-csv', 'pc-ept', 'pc-filter',
    'pc-las', 'pc-rectify', 'pc-sample', 'pc-skip-geometric',
  ] },
  { name: 'Meshing & Texturing', options: [
    'mesh-octree-depth', 'mesh-size', 'use-3dmesh', 'skip-3dmodel',
    'texturing-keep-unseen-faces', 'texturing-single-material',
    'texturing-skip-global-seam-leveling', '3d-tiles', 'gltf',
  ] },
  { name: 'Georeferencing & Camera', options: [
    'cameras', 'camera-lens', 'force-gps', 'gps-accuracy', 'gps-z-offset',
    'use-fixed-camera-params', 'use-hybrid-bundle-adjustment', 'rolling-shutter',
    'rolling-shutter-readout', 'sfm-no-partial',
  ] },
  { name: 'Radiometric & Multispectral', options: [
    'radiometric-calibration', 'primary-band', 'skip-band-alignment',
    'bg-removal', 'sky-removal',
  ] },
  { name: 'Split-Merge', options: [
    'split', 'split-overlap', 'sm-cluster', 'sm-no-align', 'merge',
  ] },
]

const ADVANCED = 'Advanced'

// Group a live NodeODM option catalog into rendered sections.
//   - Preserves ODM_CATEGORIES order; Advanced (if any) is always last.
//   - An option not named in any category lands in Advanced (never dropped).
//   - Categories (incl. Advanced) with zero matching catalog entries are omitted.
//   - Every catalog option appears exactly once.
export function groupOptions(catalog) {
  const byName = new Map((catalog || []).map(o => [o.name, o]))
  const used = new Set()
  const groups = []

  for (const category of ODM_CATEGORIES) {
    const options = []
    for (const name of category.options) {
      const opt = byName.get(name)
      if (opt && !used.has(name)) {
        options.push(opt)
        used.add(name)
      }
    }
    if (options.length) groups.push({ name: category.name, options })
  }

  const leftover = (catalog || []).filter(o => !used.has(o.name))
  if (leftover.length) groups.push({ name: ADVANCED, options: leftover })

  return groups
}
