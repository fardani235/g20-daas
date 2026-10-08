// Styling, legend and popups for change-detection overlays (render_kind
// "change"): regions classed as fill / cut / vegetation / appearance, each
// carrying its own numbers. Colours are fixed rather than hashed so "cut is
// red, fill is blue" holds on every map and matches the plugin's own legend.

export const CHANGE_CLASSES = {
  fill: { label: 'Fill', color: '#2563eb' },
  cut: { label: 'Cut', color: '#dc2626' },
  vegetation: { label: 'Vegetation (not counted)', color: '#16a34a' },
  appearance: { label: 'Looks different, same height', color: '#d97706' },
}
const FALLBACK = { label: 'Change', color: '#6b7280' }
const ORDER = Object.keys(CHANGE_CLASSES)

const classOf = feature => CHANGE_CLASSES[feature?.properties?.class] || FALLBACK

// Leaflet style callback for a change region.
export function changeStyle(feature) {
  const { color } = classOf(feature)
  // Earthwork reads stronger than the informational classes.
  const earthwork = ['fill', 'cut'].includes(feature?.properties?.class)
  return { color, weight: earthwork ? 1.5 : 1, fillColor: color, fillOpacity: earthwork ? 0.4 : 0.2 }
}

function num(value, digits) {
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return n.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
}

// "1,250 m³" — one decimal under 10, none above 100.
function quantity(value, unit) {
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return `${num(n, Math.abs(n) >= 100 ? 0 : Math.abs(n) >= 10 ? 1 : 2)} ${unit}`
}

// [{ class, count, area, volume, color }] in a fixed order (fill, cut,
// vegetation, appearance) for the classes present. `class` is the display
// text the Layers panel prints next to the swatch.
export function changeLegend(features) {
  const groups = new Map()
  for (const feature of features || []) {
    const key = feature?.properties?.class
    const g = groups.get(key) || { count: 0, area: 0, volume: 0 }
    g.count += 1
    g.area += Number(feature?.properties?.area) || 0
    g.volume += Number(feature?.properties?.volume_m3) || 0
    groups.set(key, g)
  }
  const rank = key => (ORDER.includes(key) ? ORDER.indexOf(key) : ORDER.length)
  return [...groups.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]))
    .map(([key, g]) => {
      const meta = CHANGE_CLASSES[key] || FALLBACK
      // Volumes only mean earthwork for cut and fill.
      const amount = ['fill', 'cut'].includes(key) ? quantity(g.volume, 'm³') : quantity(g.area, 'm²')
      return {
        class: amount ? `${meta.label} · ${amount}` : meta.label,
        count: g.count,
        area: g.area,
        volume: g.volume,
        color: meta.color,
      }
    })
}

// Label/value rows describing one region, for its popup.
export function changeRows(feature) {
  const p = feature?.properties || {}
  const rows = [{ label: 'Area', value: quantity(p.area, 'm²') }]
  if (p.volume_m3 !== undefined) {
    const sigma = quantity(p.volume_uncertainty_m3, 'm³')
    rows.push({ label: 'Volume', value: `${quantity(p.volume_m3, 'm³')}${sigma ? ` ± ${sigma}` : ''}` })
    rows.push({ label: 'Average change', value: quantity(p.mean_change_m, 'm') })
    rows.push({ label: 'Largest change', value: quantity(p.max_change_m, 'm') })
  }
  return rows.filter(r => r.value)
}

const escapeHtml = text => String(text).replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

// Popup markup for a region. Everything from the feature is escaped: the
// GeoJSON is produced by a plugin, which may be an organization's own upload.
export function changePopupHtml(feature) {
  const title = feature?.properties?.label || classOf(feature).label
  const rows = changeRows(feature)
    .map(r => `<tr><td style="padding-right:8px;color:#666">${escapeHtml(r.label)}</td><td>${escapeHtml(r.value)}</td></tr>`)
    .join('')
  return `<div style="font-size:12px"><strong>${escapeHtml(title)}</strong><table style="margin-top:4px">${rows}</table></div>`
}
