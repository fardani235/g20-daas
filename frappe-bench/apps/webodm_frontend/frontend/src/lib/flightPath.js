// Order drone images for the flight-path polyline.
// Prefer EXIF capture_time; fall back to natural-numeric filename order.

function naturalChunks(name) {
  // "DJI_0018.jpg" -> ["DJI_", "0018", ".jpg"] so numeric runs compare as numbers.
  return String(name || '').match(/(\d+|\D+)/g) || []
}

function compareNatural(a, b) {
  const ka = naturalChunks(a.filename)
  const kb = naturalChunks(b.filename)
  const n = Math.min(ka.length, kb.length)
  for (let i = 0; i < n; i++) {
    const x = ka[i]
    const y = kb[i]
    const nx = Number(x)
    const ny = Number(y)
    const bothNum = !Number.isNaN(nx) && !Number.isNaN(ny) && x.trim() !== '' && y.trim() !== ''
    if (bothNum) {
      if (nx !== ny) return nx - ny
    } else if (x !== y) {
      return x < y ? -1 : 1
    }
  }
  return ka.length - kb.length
}

function hasGps(img) {
  const lat = parseFloat(img.latitude)
  const lng = parseFloat(img.longitude)
  return !Number.isNaN(lat) && !Number.isNaN(lng) && !(lat === 0 && lng === 0)
}

export function sortImagesByCapture(images) {
  const usable = (images || []).filter(hasGps)
  const timed = usable.filter(img => img.capture_time)
  const untimed = usable.filter(img => !img.capture_time)
  timed.sort((a, b) => {
    if (a.capture_time < b.capture_time) return -1
    if (a.capture_time > b.capture_time) return 1
    return compareNatural(a, b)
  })
  untimed.sort(compareNatural)
  return [...timed, ...untimed]
}
