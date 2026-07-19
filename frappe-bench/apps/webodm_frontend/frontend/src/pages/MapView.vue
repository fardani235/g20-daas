<template>
  <div class="h-screen flex" @mousemove="onResize" @mouseup="stopResize">
    <div class="bg-white dark:bg-gray-900 border-r dark:border-gray-700 overflow-y-auto flex-shrink-0" :style="{ width: sidebarWidth + 'px' }">
      <div class="p-4 border-b dark:border-gray-700">
        <Button variant="outline" size="sm" @click="$router.push('/projects')">
          &larr; Back
        </Button>
        <h2 class="text-lg font-semibold mt-2 text-gray-900 dark:text-gray-100">{{ project?.title || project?.name }}</h2>
      </div>
      <div class="p-4 space-y-2">
        <h3 class="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Tasks</h3>
        <div v-if="tasks.length === 0" class="text-sm text-gray-400 dark:text-gray-500 py-4 text-center">
          No tasks yet. Upload images to start processing.
        </div>
        <div v-for="task in tasks" :key="task.name"
          class="p-3 rounded-lg border dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-900 dark:text-gray-100"
          :class="{ 'border-blue-500 dark:border-blue-400 bg-blue-50 dark:bg-blue-900/30': selectedTask === task.name }"
          @click="selectTask(task)"
        >
          <div class="flex items-start justify-between">
            <div class="font-medium text-sm">{{ task.title || task.name }}</div>
            <button
              class="text-gray-400 dark:text-gray-500 hover:text-red-600 dark:hover:text-red-400 transition-colors p-0.5 flex-shrink-0 ml-2"
              @click.stop="confirmDeleteTask(task)"
              title="Delete task"
            >
              <FeatherIcon name="trash-2" class="h-3.5 w-3.5" />
            </button>
          </div>
          <div class="text-xs text-gray-500 dark:text-gray-400 mt-1 flex items-center gap-2">
            <Badge :theme="statusTheme(task.status)" size="sm">{{ task.status }}</Badge>
            <span v-if="task.images?.length">📷 {{ task.images.length }}</span>
            <span v-if="task.progress > 0">{{ task.progress }}%</span>
          </div>
          <div v-if="task.progress > 0 && task.status !== 'Completed'" class="mt-2 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
            <div class="h-full bg-blue-500 rounded-full transition-all" :style="{ width: task.progress + '%' }"></div>
          </div>
          <div v-if="selectedTask === task.name">
            <div v-if="task.images?.length" class="mt-2 pt-2 border-t dark:border-gray-700">
              <p class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">{{ task.images.length }} image(s)</p>
              <div class="flex flex-wrap gap-1">
                <div v-for="img in task.images.slice(0, 9)" :key="img.name" class="w-[72px] h-[72px] rounded overflow-hidden bg-gray-100 dark:bg-gray-800 flex-shrink-0">
                  <img :src="img.image" :alt="img.filename" class="w-full h-full object-cover" @error="e => e.target.style.display = 'none'" />
                </div>
                <div v-if="task.images.length > 9" class="w-[72px] h-[72px] rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs text-gray-500 dark:text-gray-400 font-medium flex-shrink-0">
                  +{{ task.images.length - 9 }}
                </div>
              </div>
            </div>
            <div class="flex flex-wrap gap-1.5 mt-2 pt-2 border-t dark:border-gray-700">
              <Button v-if="task.status === 'Pending'" variant="solid" size="sm" theme="green" @click.stop="startProcessing(task)">
                <template #prefix><FeatherIcon name="play" class="h-3.5 w-3.5" /></template>
                Start
              </Button>
              <Button v-if="task.status === 'Running' || task.status === 'Pending'" variant="outline" size="sm" theme="red" @click.stop="cancelTask(task)">
                <template #prefix><FeatherIcon name="x-circle" class="h-3.5 w-3.5" /></template>
                Cancel
              </Button>
              <Button variant="outline" size="sm" theme="gray" @click.stop="openTaskConsole(task)">
                <template #prefix><FeatherIcon name="terminal" class="h-3.5 w-3.5" /></template>
                Console
              </Button>
              <Button variant="outline" size="sm" theme="gray" @click.stop="openTaskModel(task)">
                <template #prefix><FeatherIcon name="box" class="h-3.5 w-3.5" /></template>
                3D
              </Button>
            </div>

          </div>
        </div>
        <Button variant="solid" theme="blue" class="w-full mt-4" @click="showUpload = true">
          <template #prefix><FeatherIcon name="upload-cloud" class="h-4 w-4" /></template>
          Upload Images
        </Button>
      </div>
    </div>

    <div
      class="w-1.5 bg-gray-200 dark:bg-gray-700 hover:bg-blue-400 dark:hover:bg-blue-500 cursor-col-resize flex-shrink-0 transition-colors"
      @mousedown.prevent="startResize"
    ></div>
    <div class="flex-1 relative bg-gray-50 dark:bg-gray-900">
      <div id="map" class="h-full w-full"></div>
      <div class="absolute top-4 left-4 z-[1000] bg-white dark:bg-gray-800 rounded-lg shadow-md border dark:border-gray-700 p-2 flex items-center gap-2">
        <Button size="sm" :variant="measure.state.mode === 'distance' ? 'solid' : 'outline'" @click="startMeasure('distance')" title="Measure distance">
          <template #prefix><FeatherIcon name="minus" class="h-3.5 w-3.5" /></template>
          Distance
        </Button>
        <Button size="sm" :variant="measure.state.mode === 'area' ? 'solid' : 'outline'" @click="startMeasure('area')" title="Measure area">
          <template #prefix><FeatherIcon name="square" class="h-3.5 w-3.5" /></template>
          Area
        </Button>
        <Button
          size="sm"
          :variant="measure.state.mode === 'volume' ? 'solid' : 'outline'"
          :disabled="!hasDsm"
          @click="startMeasure('volume')"
          :title="hasDsm ? 'Measure volume (needs DSM)' : 'Volume requires a DSM'"
        >
          <template #prefix><FeatherIcon name="box" class="h-3.5 w-3.5" /></template>
          Volume
        </Button>
        <Button size="sm" variant="ghost" @click="clearMeasure" title="Clear measurement">
          <FeatherIcon name="trash-2" class="h-3.5 w-3.5" />
        </Button>
        <span v-if="measure.state.formatted" class="text-sm font-medium text-gray-700 dark:text-gray-200 pl-1">
          {{ measure.state.formatted }}
        </span>
      </div>
      <div class="absolute top-4 right-4 z-[1000] space-y-2 flex flex-col items-end">
        <Button variant="outline" size="sm" @click="zoomToFit">Zoom To Fit</Button>
        <div class="bg-white dark:bg-gray-800 rounded-lg shadow-md border dark:border-gray-700 w-[200px] text-sm">
          <!-- Basemap -->
          <div class="p-3 border-b dark:border-gray-700">
            <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Basemap</p>
            <label v-for="b in BASEMAPS" :key="b.id" class="flex items-center gap-2 text-gray-700 dark:text-gray-300 py-0.5 cursor-pointer">
              <input type="radio" name="basemap" :value="b.id" :checked="currentBasemap === b.id" @change="setBasemap(b.id)" />
              {{ b.label }}
            </label>
          </div>
          <!-- Layers + opacity -->
          <div v-if="overlays.length" class="p-3 border-b dark:border-gray-700">
            <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Layers</p>
            <div v-for="o in overlays" :key="o.key" class="py-1">
              <label class="flex items-center gap-2 text-gray-700 dark:text-gray-300 cursor-pointer">
                <input type="checkbox" :checked="o.visible" @change="toggleOverlay(o)" class="rounded dark:bg-gray-700" />
                {{ o.label }}
              </label>
              <input
                v-if="o.visible"
                type="range" min="0" max="100" step="5"
                :value="o.opacity"
                @input="setOverlayOpacity(o, $event.target.value)"
                class="w-full mt-1"
              />
            </div>
          </div>
          <!-- Show toggles -->
          <div class="p-3">
            <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Show</p>
            <label
              class="flex items-center gap-2 py-0.5"
              :class="hasGps ? 'text-gray-700 dark:text-gray-300 cursor-pointer' : 'text-gray-400 dark:text-gray-600 cursor-not-allowed'"
            >
              <input type="checkbox" :checked="showMarkers" :disabled="!hasGps" @change="toggleMarkers" class="rounded dark:bg-gray-700" />
              Image markers
            </label>
            <label
              class="flex items-center gap-2 py-0.5"
              :class="hasGps ? 'text-gray-700 dark:text-gray-300 cursor-pointer' : 'text-gray-400 dark:text-gray-600 cursor-not-allowed'"
            >
              <input type="checkbox" :checked="showFlightPath" :disabled="!hasGps" @change="toggleFlightPath" class="rounded dark:bg-gray-700" />
              Flight path
            </label>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showUpload" class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/50" style="transform: translateZ(0)" @click.self="showUpload = false">
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md mx-4 p-6" style="transform: translateZ(0)">
        <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-gray-100">Upload Images</h2>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">Select images to upload for processing.</p>
        <input
          type="file"
          multiple
          accept="image/*"
          class="block w-full text-sm text-gray-500 dark:text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-50 dark:file:bg-blue-900 file:text-blue-700 dark:file:text-blue-300 hover:file:bg-blue-100 dark:hover:file:bg-blue-800"
          @change="uploadFiles"
        />
        <div class="mt-4 space-y-2">
          <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Outputs</p>
          <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" v-model="outputOpts.orthophoto" checked class="rounded dark:bg-gray-700" /> Orthophoto</label>
          <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" v-model="outputOpts.dsm" class="rounded dark:bg-gray-700" /> DSM</label>
          <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" v-model="outputOpts.dtm" class="rounded dark:bg-gray-700" /> DTM</label>
          <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" v-model="outputOpts.model" class="rounded dark:bg-gray-700" /> 3D Model</label>
          <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300"><input type="checkbox" v-model="outputOpts.pointCloud" class="rounded dark:bg-gray-700" /> Point Cloud</label>
          <div v-if="outputOpts.orthophoto" class="flex items-center gap-2 mt-1">
            <label class="text-xs text-gray-500 dark:text-gray-400 w-28">Resolution (cm/px):</label>
            <input type="number" v-model.number="outputOpts.orthophotoResolution" placeholder="auto" min="0.1" step="0.1" class="w-24 px-2 py-1 text-sm border rounded dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200" />
          </div>
        </div>
        <div v-if="uploading" class="mt-4">
          <div class="bg-blue-50 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 text-sm px-4 py-3 rounded-lg">
            {{ uploadProgress }}
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-6">
          <Button variant="ghost" @click="showUpload = false">Cancel</Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, FeatherIcon } from 'frappe-ui'
import L from 'leaflet'
import { toast } from 'frappe-ui'
import { formatVolume } from '@/lib/format'
import { BASEMAPS, createBasemap } from '@/lib/mapLayers'
import { sortImagesByCapture } from '@/lib/flightPath'
import { useMeasure } from '@/composables/useMeasure'

const route = useRoute()
const router = useRouter()
const project = ref(null)
const tasks = ref([])
const selectedTask = ref(null)
const showUpload = ref(false)
const uploading = ref(false)
const uploadProgress = ref('')
const outputOpts = ref({ orthophoto: true, dsm: false, dtm: false, model: false, pointCloud: false, orthophotoResolution: null })
const sidebarWidth = ref(360)
// Raster overlays available for the selected task: { key, label, visible }.
const overlays = ref([])
const currentBasemap = ref('osm')
const showMarkers = ref(true)
const currentImages = ref([])
const currentTask = ref(null)
const hasDsm = computed(() => !!currentTask.value?.dsm)
const hasGps = computed(() =>
  currentImages.value.some(img => {
    const lat = parseFloat(img.latitude)
    const lng = parseFloat(img.longitude)
    return !isNaN(lat) && !isNaN(lng) && !(lat === 0 && lng === 0)
  })
)
let resizing = false
let map = null
let imageMarkers = null
let baseLayer = null
let flightPathLayer = null
const showFlightPath = ref(false)
const measure = useMeasure(() => map, { onVolume: computeVolume })
const overlayLayers = {}  // key -> Leaflet tileLayer

const DATASET_LABELS = { orthophoto: 'Orthophoto', dsm: 'DSM', dtm: 'DTM' }
const DATASET_EXTENT_FIELD = {
  orthophoto: 'orthophoto_extent',
  dsm: 'dsm_extent',
  dtm: 'dtm_extent',
}

function statusTheme(status) {
  const themes = { completed: 'green', running: 'blue', failed: 'red', queued: 'orange', canceled: 'gray' }
  return themes[status?.toLowerCase()] || 'gray'
}

function plotImageMarkers(images) {
  if (!map) return
  if (imageMarkers) map.removeLayer(imageMarkers)
  imageMarkers = L.featureGroup()
  const bounds = L.latLngBounds()
  let hasGps = false

  for (const img of images || []) {
    const lat = parseFloat(img.latitude)
    const lng = parseFloat(img.longitude)
    if (isNaN(lat) || isNaN(lng) || (lat === 0 && lng === 0)) continue
    hasGps = true
    const marker = L.marker([lat, lng])
    marker.bindPopup(`
      <div style="max-width:200px">
        <img src="${img.image}" style="width:100%;height:auto;border-radius:4px" />
        <p style="margin:4px 0 0;font-size:11px;color:#666">${img.filename}</p>
      </div>
    `)
    imageMarkers.addLayer(marker)
    bounds.extend([lat, lng])
  }

  if (hasGps) {
    if (showMarkers.value) imageMarkers.addTo(map)
    map.fitBounds(bounds, { padding: [50, 50] })
  }
}

function clearOverlays() {
  for (const key of Object.keys(overlayLayers)) {
    if (map) map.removeLayer(overlayLayers[key])
    delete overlayLayers[key]
  }
  overlays.value = []
}

function parseExtentBounds(extent) {
  // GeoJSON Polygon (EPSG:4326) -> Leaflet LatLngBounds.
  if (!extent) return null
  const geo = typeof extent === 'string' ? JSON.parse(extent) : extent
  const ring = geo?.coordinates?.[0]
  if (!ring || !ring.length) return null
  const b = L.latLngBounds()
  for (const [lng, lat] of ring) b.extend([lat, lng])
  return b
}

// Add raster tile overlays for a completed task, driven by the extent fields
// populated during asset download (recommendation #2).
function loadOverlays(task) {
  clearOverlays()
  if (!map || task.status !== 'Completed') return

  let fitB = null
  for (const key of ['orthophoto', 'dsm', 'dtm']) {
    if (!task[key]) continue
    const bounds = parseExtentBounds(task[DATASET_EXTENT_FIELD[key]])
    const url = `/api/method/webodm_core.api.tiles.serve`
      + `?task_name=${encodeURIComponent(task.name)}&dataset=${key}`
      + `&z={z}&x={x}&y={y}`
    const layer = L.tileLayer(url, {
      bounds: bounds || undefined,
      maxNativeZoom: 22,
      maxZoom: 24,
      tileSize: 256,
      opacity: 1,
    })
    const visible = key === 'orthophoto'  // show orthophoto by default
    if (visible) layer.addTo(map)
    overlayLayers[key] = layer
    overlays.value.push({ key, label: DATASET_LABELS[key], visible, opacity: 100 })
    if (visible && bounds) fitB = bounds
  }
  if (fitB) map.fitBounds(fitB, { padding: [30, 30] })
}

function toggleOverlay(o) {
  o.visible = !o.visible
  const layer = overlayLayers[o.key]
  if (!layer || !map) return
  if (o.visible) layer.addTo(map)
  else map.removeLayer(layer)
}

function setOverlayOpacity(o, value) {
  o.opacity = Number(value)
  const layer = overlayLayers[o.key]
  if (layer) layer.setOpacity(o.opacity / 100)
}

function setBasemap(id) {
  currentBasemap.value = id
  if (!map) return
  if (baseLayer) map.removeLayer(baseLayer)
  baseLayer = createBasemap(id)
  baseLayer.addTo(map)
  baseLayer.bringToBack() // keep orthophoto/overlays on top
}

function toggleMarkers() {
  showMarkers.value = !showMarkers.value
  if (!map || !imageMarkers) return
  if (showMarkers.value) imageMarkers.addTo(map)
  else map.removeLayer(imageMarkers)
}

function flightEndpointIcon(label, color) {
  return L.divIcon({
    className: 'flight-endpoint',
    html: `<div style="background:${color};color:#fff;border-radius:9999px;width:20px;height:20px;`
      + `display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;`
      + `box-shadow:0 1px 3px rgba(0,0,0,.4)">${label}</div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  })
}

function flightArrowIcon(angleDeg) {
  return L.divIcon({
    className: 'flight-arrow',
    html: `<div style="transform:rotate(${angleDeg}deg);color:#f59e0b;font-size:14px;line-height:1">▲</div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })
}

// Arrow markers are placed in screen space, so recompute them whenever the
// map is zoomed while the flight path is shown.
let flightLatLngs = []
let flightArrows = []

function drawFlightArrows() {
  if (!map || !flightPathLayer) return
  for (const a of flightArrows) flightPathLayer.removeLayer(a)
  flightArrows = []
  for (let i = 0; i < flightLatLngs.length - 1; i++) {
    const p1 = map.latLngToLayerPoint(flightLatLngs[i])
    const p2 = map.latLngToLayerPoint(flightLatLngs[i + 1])
    const angle = Math.atan2(p2.x - p1.x, -(p2.y - p1.y)) * 180 / Math.PI // 0° = north (▲ up)
    const mid = [
      (flightLatLngs[i][0] + flightLatLngs[i + 1][0]) / 2,
      (flightLatLngs[i][1] + flightLatLngs[i + 1][1]) / 2,
    ]
    const arrow = L.marker(mid, { icon: flightArrowIcon(angle), interactive: false })
    arrow.addTo(flightPathLayer)
    flightArrows.push(arrow)
  }
}

function removeFlightPath() {
  if (map) map.off('zoomend', drawFlightArrows)
  if (map && flightPathLayer) map.removeLayer(flightPathLayer)
  flightPathLayer = null
  flightArrows = []
  flightLatLngs = []
}

function buildFlightPath(images) {
  removeFlightPath()
  if (!map) return
  const ordered = sortImagesByCapture(images)
  if (ordered.length < 2) return
  flightLatLngs = ordered.map(img => [parseFloat(img.latitude), parseFloat(img.longitude)])
  flightPathLayer = L.featureGroup()
  L.polyline(flightLatLngs, { color: '#f59e0b', weight: 2, opacity: 0.9 }).addTo(flightPathLayer)
  L.marker(flightLatLngs[0], { icon: flightEndpointIcon('A', '#16a34a') }).addTo(flightPathLayer)
  L.marker(flightLatLngs[flightLatLngs.length - 1], { icon: flightEndpointIcon('B', '#dc2626') }).addTo(flightPathLayer)
  flightPathLayer.addTo(map)
  drawFlightArrows()
  map.on('zoomend', drawFlightArrows)
}

function toggleFlightPath() {
  showFlightPath.value = !showFlightPath.value
  if (showFlightPath.value) buildFlightPath(currentImages.value)
  else removeFlightPath()
}

async function selectTask(task) {
  selectedTask.value = task.name
  let full = task
  if (!task.images || !task.images.length || task.orthophoto_extent === undefined) {
    try {
      const res = await fetch(`/api/resource/WebODM%20Task/${encodeURIComponent(task.name)}`)
      if (res.ok) {
        const { data } = await res.json()
        const idx = tasks.value.findIndex(t => t.name === task.name)
        if (idx !== -1) tasks.value[idx] = data
        full = data
      }
    } catch {}
  }
  currentImages.value = full.images || []
  currentTask.value = full
  measure.clear()
  plotImageMarkers(full.images)
  loadOverlays(full)
  if (showFlightPath.value) buildFlightPath(currentImages.value)
}

function startMeasure(mode) {
  measure.start(mode)
}

function clearMeasure() {
  measure.clear()
}

async function computeVolume(latlngs) {
  try {
    const ring = latlngs.map(p => [p.lng, p.lat])
    if (ring.length) {
      const [fx, fy] = ring[0]
      const [lx, ly] = ring[ring.length - 1]
      if (fx !== lx || fy !== ly) ring.push([fx, fy]) // close the ring
    }
    const polygon = { type: 'Polygon', coordinates: [ring] }
    const headers = { 'Content-Type': 'application/json' }
    if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
    const res = await fetch('/api/method/webodm_core.api.tiles.volume', {
      method: 'POST',
      headers,
      body: JSON.stringify({ task_name: selectedTask.value, polygon: JSON.stringify(polygon) }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.message || 'Volume calculation failed')
    }
    const data = await res.json()
    return formatVolume(data.message || data)
  } catch (e) {
    toast.error(e.message || 'Volume calculation failed')
    throw e // let useMeasure show "Volume failed"
  }
}

function openTaskConsole(task) {
  router.push(`/project/${route.params.id}/task/${encodeURIComponent(task.name)}/console`)
}

function openTaskModel(task) {
  router.push(`/project/${route.params.id}/task/${encodeURIComponent(task.name)}/model`)
}

function zoomToFit() {
  if (map) {
    map.fitWorld()
  }
}

function startResize() {
  resizing = true
}

function stopResize() {
  resizing = false
}

function onResize(e) {
  if (!resizing) return
  const w = Math.max(280, Math.min(800, e.clientX))
  sidebarWidth.value = w
}

async function startProcessing(task) {
  try {
    const headers = { 'Content-Type': 'application/json' }
    if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
    const res = await fetch(`/api/method/webodm_core.api.task.process_task`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ task_name: task.name }),
    })
    if (!res.ok) throw new Error('Failed to start processing')
    const result = await res.json()
    toast.success(result.message || 'Processing started')
    const idx = tasks.value.findIndex(t => t.name === task.name)
    if (idx !== -1) tasks.value[idx].status = 'Pending'
  } catch (e) {
    toast.error(e.message)
  }
}

async function cancelTask(task) {
  try {
    const headers = { 'Content-Type': 'application/json' }
    if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
    const res = await fetch(`/api/method/webodm_core.api.task.cancel_task`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ task_name: task.name }),
    })
    if (!res.ok) throw new Error('Failed to cancel task')
    const result = await res.json()
    toast.success(result.message || 'Task cancelled')
    const idx = tasks.value.findIndex(t => t.name === task.name)
    if (idx !== -1) tasks.value[idx].status = 'Cancelled'
  } catch (e) {
    toast.error(e.message)
  }
}

function confirmDeleteTask(task) {
  const ok = confirm(`Delete task "${task.title || task.name}"?`)
  if (ok) deleteTask(task)
}

async function deleteTask(task) {
  try {
    const headers = {}
    if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
    const res = await fetch(`/api/resource/WebODM%20Task/${encodeURIComponent(task.name)}`, {
      method: 'DELETE',
      headers,
    })
    if (!res.ok) throw new Error('Failed to delete task')
    tasks.value = tasks.value.filter(t => t.name !== task.name)
    if (selectedTask.value === task.name) selectedTask.value = null
    toast.success('Task deleted')
  } catch (e) {
    toast.error(e.message)
  }
}

async function uploadFiles(e) {
  const files = e.target.files
  if (!files.length) return
  uploading.value = true
  uploadProgress.value = `Uploading ${files.length} file(s)...`
  try {
    const formData = new FormData()
    for (const file of files) {
      formData.append('files', file)
    }
    formData.append('project_id', route.params.id)
    formData.append('options', JSON.stringify(outputOpts.value))
    const headers = {}
    if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
    const res = await fetch('/api/method/webodm_core.api.task.upload_images', {
      method: 'POST',
      headers,
      body: formData,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.message || 'Upload failed')
    }
    toast.success('Images uploaded')
    showUpload.value = false
    fetchTasks()
  } catch (err) {
    toast.error(err.message)
  } finally {
    uploading.value = false
    uploadProgress.value = ''
  }
}

async function fetchProject() {
  try {
    const res = await fetch(`/api/resource/WebODM%20Project/${route.params.id}`)
    const data = await res.json()
    project.value = data.data
  } catch (err) {
    console.error('Failed to fetch project', err)
  }
}

async function fetchTasks() {
  try {
    const filters = JSON.stringify([["project", "=", route.params.id]])
    const res = await fetch(
      `/api/resource/WebODM%20Task?filters=${encodeURIComponent(filters)}&fields=["*"]`
    )
    const data = await res.json()
    tasks.value = data.data || []
  } catch (err) {
    console.error('Failed to fetch tasks', err)
  }
}

onMounted(() => {
  fetchProject()
  fetchTasks()

  map = L.map('map').setView([0, 0], 2)
  baseLayer = createBasemap(currentBasemap.value)
  baseLayer.addTo(map)
})

onUnmounted(() => {
  measure.clear()
  removeFlightPath()
  if (map) {
    map.remove()
    map = null
  }
})
</script>
