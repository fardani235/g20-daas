import L from 'leaflet'

// Approved basemaps: OSM Streets, Esri Satellite, Esri Topographic.
export const BASEMAPS = [
  {
    id: 'osm',
    label: 'Streets',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  },
  {
    id: 'esri-satellite',
    label: 'Satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri, Maxar, Earthstar Geographics',
    maxZoom: 19,
  },
  {
    id: 'esri-topo',
    label: 'Topographic',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
  },
]

export function getBasemapDef(id) {
  return BASEMAPS.find(b => b.id === id) || BASEMAPS[0]
}

export function createBasemap(id) {
  const def = getBasemapDef(id)
  return L.tileLayer(def.url, {
    attribution: def.attribution,
    maxZoom: def.maxZoom,
  })
}
