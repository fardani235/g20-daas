// Base-surface methods for DSM volume measurement, mirroring WebODM's measure
// plugin. The base is interpolated from DSM elevations at the drawn polygon's
// boundary vertices; fill is above it, cut below.
export const VOLUME_BASE_METHODS = [
  { value: 'triangulate', label: 'Triangulate', hint: 'TIN through the boundary — follows uneven ground' },
  { value: 'plane', label: 'Plane', hint: 'Single tilted best-fit plane' },
  { value: 'average', label: 'Average', hint: 'Flat base at the mean boundary elevation' },
  { value: 'highest', label: 'Highest', hint: 'Flat base at the highest boundary elevation' },
  { value: 'lowest', label: 'Lowest', hint: 'Flat base at the lowest boundary elevation' },
]

export const DEFAULT_VOLUME_BASE_METHOD = 'triangulate'

// Matches WebODM, which persists the selection in localStorage.
export const VOLUME_BASE_METHOD_STORAGE_KEY = 'measure_base_method'

function isKnown(method) {
  return VOLUME_BASE_METHODS.some(m => m.value === method)
}

export function loadVolumeBaseMethod() {
  try {
    const stored = localStorage.getItem(VOLUME_BASE_METHOD_STORAGE_KEY)
    return isKnown(stored) ? stored : DEFAULT_VOLUME_BASE_METHOD
  } catch {
    // Private-mode / disabled storage: fall back to the default.
    return DEFAULT_VOLUME_BASE_METHOD
  }
}

export function saveVolumeBaseMethod(method) {
  if (!isKnown(method)) return
  try {
    localStorage.setItem(VOLUME_BASE_METHOD_STORAGE_KEY, method)
  } catch {
    // Persistence is a convenience; ignore storage failures.
  }
}
