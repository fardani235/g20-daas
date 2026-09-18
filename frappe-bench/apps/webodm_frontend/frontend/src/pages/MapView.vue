<template>
  <div class="flex h-full" @mousemove="onResize" @mouseup="stopResize">
    <div class="bg-card border-r border-border overflow-y-auto flex-shrink-0" :style="{ width: sidebarWidth + 'px' }">
      <div class="border-b border-border p-4">
        <h2 class="text-base font-medium text-foreground">
          {{ project?.title || project?.name }}
        </h2>
      </div>
      <div class="p-4 space-y-2">
        <h3 class="text-sm font-medium text-muted-foreground uppercase tracking-wide">Tasks</h3>
        <div v-if="tasks.length === 0" class="text-sm text-muted-foreground py-4 text-center">
          No tasks yet. Upload images to start processing.
        </div>
        <div v-for="task in tasks" :key="task.name"
          class="p-3 rounded-lg border border-border cursor-pointer hover:bg-accent text-foreground"
          :class="{ 'border-primary bg-primary/10': selectedTask === task.name }"
          @click="selectTask(task)"
        >
          <div class="flex items-start justify-between">
            <div class="font-medium text-sm">{{ task.title || task.name }}</div>
            <Button
              variant="ghost"
              size="icon"
              class="size-7 flex-shrink-0 text-muted-foreground hover:text-destructive"
              title="Delete task"
              @click.stop="confirmDeleteTask(task)"
            >
              <Trash2 />
              <span class="sr-only">Delete task</span>
            </Button>
          </div>
          <div class="text-xs text-muted-foreground mt-1 flex items-center gap-2">
            <Badge :variant="statusVariant(task.status)">{{ task.status }}</Badge>
            <span v-if="task.images?.length">📷 {{ task.images.length }}</span>
            <span v-if="task.progress > 0">{{ task.progress }}%</span>
          </div>
          <div v-if="task.progress > 0 && task.status !== 'Completed'" class="mt-2 h-1.5 bg-muted rounded-full overflow-hidden">
            <div class="h-full bg-primary rounded-full transition-all" :style="{ width: task.progress + '%' }"></div>
          </div>
          <div v-if="selectedTask === task.name">
            <div v-if="task.images?.length" class="mt-2 pt-2 border-t border-border">
              <p class="text-xs font-medium text-muted-foreground mb-2">{{ task.images.length }} image(s)</p>
              <div class="flex flex-wrap gap-1">
                <div v-for="img in task.images.slice(0, 9)" :key="img.name" class="w-[72px] h-[72px] rounded overflow-hidden bg-muted flex-shrink-0">
                  <img :src="img.image" :alt="img.filename" class="w-full h-full object-cover" @error="e => e.target.style.display = 'none'" />
                </div>
                <div v-if="task.images.length > 9" class="w-[72px] h-[72px] rounded bg-muted flex items-center justify-center text-xs text-muted-foreground font-medium flex-shrink-0">
                  +{{ task.images.length - 9 }}
                </div>
              </div>
            </div>
            <div class="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border">
              <Button
                v-if="task.status === 'Pending'"
                variant="success"
                size="sm"
                @click.stop="startProcessing(task)"
              >
                <Play />
                Start
              </Button>
              <Button
                v-if="['Pending', 'Queued', 'Running'].includes(task.status)"
                variant="outline"
                size="sm"
                class="text-destructive"
                @click.stop="cancelTask(task)"
              >
                <CircleX />
                Cancel
              </Button>
              <Button variant="outline" size="sm" @click.stop="openTaskConsole(task)">
                <Terminal />
                Console
              </Button>
              <Button variant="outline" size="sm" @click.stop="openTaskModel(task)">
                <Box />
                3D
              </Button>
            </div>

            <div class="mt-2 pt-2 border-t border-border space-y-2">
              <p class="text-xs font-medium text-muted-foreground uppercase tracking-wide">Analysis</p>
              <div v-if="runnablePlugins.length" class="flex flex-wrap gap-1.5">
                <Button
                  v-for="p in runnablePlugins"
                  :key="p.op_id"
                  variant="outline"
                  size="sm"
                  @click.stop="openRunDialog(p)"
                >
                  <Sparkles />
                  {{ p.label }}
                </Button>
              </div>
              <p v-else class="text-xs text-muted-foreground">
                No analysis plugins enabled for your organization.
              </p>
              <div v-if="displayRuns.length" class="space-y-1">
                <div
                  v-for="run in displayRuns"
                  :key="run.name"
                  class="flex items-center justify-between gap-2 text-xs"
                >
                  <span class="truncate text-foreground">{{ pluginLabel(run.plugin) }}</span>
                  <Badge :variant="statusVariant(run.status)">{{ run.status }}</Badge>
                  <span class="flex items-center gap-2">
                    <a
                      v-if="run.status === 'Completed' && run.output_file"
                      :href="runDownloadUrl(run.name)"
                      class="text-primary hover:underline"
                      @click.stop
                    >Download</a>
                    <Button
                      v-if="['Queued', 'Running'].includes(run.status)"
                      variant="ghost"
                      size="sm"
                      class="h-6 px-1 text-destructive"
                      @click.stop="cancelPluginRun(run)"
                    >Cancel</Button>
                  </span>
                </div>
              </div>
            </div>

          </div>
        </div>
        <Button class="mt-4 w-full" @click="openUpload">
          <CloudUpload />
          Add Task
        </Button>
      </div>
    </div>

    <div
      class="w-1.5 bg-border hover:bg-primary/60 cursor-col-resize flex-shrink-0 transition-colors"
      @mousedown.prevent="startResize"
    ></div>
    <div class="flex-1 relative z-0 bg-background">
      <div id="map" class="h-full w-full"></div>
      <!--
        Overlay layer. `pl-14` keeps everything clear of Leaflet's default zoom
        control (top-left, 10px + ~30px wide); children opt back into pointer
        events so the map stays draggable through the gaps.
      -->
      <div
        class="pointer-events-none absolute inset-0 z-[1000] flex items-start justify-between gap-2 p-4 pl-14"
      >
        <div
          class="pointer-events-auto flex min-w-0 max-w-full flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-2 shadow-md"
        >
          <Button
            size="sm"
            :variant="measure.state.mode === 'distance' ? 'default' : 'outline'"
            title="Measure distance"
            @click="startMeasure('distance')"
          >
            <Minus />
            <span class="hidden sm:inline">Distance</span>
          </Button>
          <Button
            size="sm"
            :variant="measure.state.mode === 'area' ? 'default' : 'outline'"
            title="Measure area"
            @click="startMeasure('area')"
          >
            <Square />
            <span class="hidden sm:inline">Area</span>
          </Button>
          <Button
            size="sm"
            :variant="measure.state.mode === 'volume' ? 'default' : 'outline'"
            :disabled="!hasDsm"
            :title="hasDsm ? 'Measure volume (needs DSM)' : 'Volume requires a DSM'"
            @click="startMeasure('volume')"
          >
            <Box />
            <span class="hidden sm:inline">Volume</span>
          </Button>
          <Select
            v-if="measure.state.mode === 'volume'"
            :model-value="volumeBaseMethod"
            class="h-8 w-auto"
            title="Base surface used to separate fill from cut"
            aria-label="Volume base method"
            @update:model-value="onVolumeBaseMethodChange"
          >
            <option v-for="m in VOLUME_BASE_METHODS" :key="m.value" :value="m.value">
              {{ m.label }}
            </option>
          </Select>
          <Button
            v-if="measure.state.drawing"
            size="sm"
            variant="success"
            title="Finish measurement"
            @click="finishMeasure"
          >
            <Check />
            <span class="hidden sm:inline">Finish</span>
          </Button>
          <Button size="sm" variant="ghost" title="Clear measurement" @click="clearMeasure">
            <Trash2 />
            <span class="sr-only">Clear measurement</span>
          </Button>
          <span
            v-if="measure.state.drawing"
            class="w-full text-xs text-muted-foreground sm:w-auto sm:pl-1"
          >
            Click points, then Finish (or click the first point).
          </span>
          <span
            v-else-if="measure.state.formatted"
            class="w-full break-words text-sm font-medium text-foreground sm:w-auto sm:pl-1"
          >
            {{ measure.state.formatted }}
          </span>
        </div>
        <div class="pointer-events-auto flex max-h-full min-h-0 flex-col items-end gap-2">
          <div class="flex items-center gap-2">
            <Button variant="outline" size="sm" title="Zoom to fit" @click="zoomToFit">
              Zoom To Fit
            </Button>
            <Button
              variant="outline"
              size="icon"
              class="h-8 w-8"
              :title="panelOpen ? 'Hide map controls' : 'Show map controls'"
              :aria-expanded="panelOpen"
              @click="panelOpen = !panelOpen"
            >
              <X v-if="panelOpen" />
              <Layers v-else />
              <span class="sr-only">{{ panelOpen ? 'Hide map controls' : 'Show map controls' }}</span>
            </Button>
          </div>
          <div
            v-show="panelOpen"
            class="min-h-0 w-[200px] max-w-full overflow-y-auto rounded-lg border border-border bg-card text-sm shadow-md"
          >
            <!-- Basemap -->
            <div class="p-3 border-b border-border">
              <p class="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Basemap</p>
              <label v-for="b in BASEMAPS" :key="b.id" class="flex items-center gap-2 text-foreground py-0.5 cursor-pointer">
                <input type="radio" name="basemap" :value="b.id" :checked="currentBasemap === b.id" @change="setBasemap(b.id)" />
                {{ b.label }}
              </label>
            </div>
            <!-- Layers + opacity -->
            <div v-if="overlays.length" class="p-3 border-b border-border">
              <p class="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Layers</p>
              <div v-for="o in overlays" :key="o.key" class="py-1">
                <label class="flex items-center gap-2 text-foreground cursor-pointer">
                  <input type="checkbox" :checked="o.visible" @change="toggleOverlay(o)" class="rounded" />
                  {{ o.label }}
                </label>
                <div v-if="o.legend?.length" class="mt-1 ml-6 space-y-0.5">
                  <div
                    v-for="item in o.legend"
                    :key="item.class"
                    class="flex items-center gap-1.5 text-xs text-muted-foreground"
                  >
                    <span class="inline-block size-2.5 rounded-sm" :style="{ background: item.color }"></span>
                    <span>{{ item.class }} ({{ item.count }})</span>
                  </div>
                </div>
                <input
                  v-if="o.visible && o.kind !== 'vector'"
                  type="range" min="0" max="100" step="5"
                  :value="o.opacity"
                  @input="setOverlayOpacity(o, $event.target.value)"
                  class="w-full mt-1"
                />
              </div>
            </div>
            <!-- Show toggles -->
            <div class="p-3">
              <p class="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Show</p>
              <label
                class="flex items-center gap-2 py-0.5"
                :class="hasGps ? 'text-foreground cursor-pointer' : 'text-muted-foreground cursor-not-allowed'"
              >
                <input type="checkbox" :checked="showMarkers" :disabled="!hasGps" @change="toggleMarkers" class="rounded" />
                Image markers
              </label>
              <label
                class="flex items-center gap-2 py-0.5"
                :class="hasGps ? 'text-foreground cursor-pointer' : 'text-muted-foreground cursor-not-allowed'"
              >
                <input type="checkbox" :checked="showFlightPath" :disabled="!hasGps" @change="toggleFlightPath" class="rounded" />
                Flight path
              </label>
            </div>
          </div>
        </div>
      </div>
    </div>

    <Dialog v-model:open="showUpload" title="Add task" description="Select images to upload for processing.">
      <div class="space-y-4">
        <input
          type="file"
          multiple
          accept="image/*"
          class="block w-full text-sm text-muted-foreground file:mr-4 file:rounded-md file:border-0 file:bg-primary/10 file:px-4 file:py-2 file:text-sm file:font-medium file:text-primary hover:file:bg-primary/20"
          @change="uploadFiles"
        />

        <div class="space-y-1.5">
          <Label for="upload-preset">Preset</Label>
          <Select id="upload-preset" v-model="selectedPreset" @change="applyPreset">
            <option :value="null">None (defaults)</option>
            <option v-for="p in uploadPresets" :key="p.name" :value="p.name">
              {{ p.preset_name }}
            </option>
          </Select>
        </div>

        <p v-if="uploadOdm.error.value" class="text-xs text-destructive">
          {{ uploadOdm.error.value }}
        </p>
        <p v-else-if="uploadOdm.loading.value" class="text-xs text-muted-foreground">
          Loading options…
        </p>
        <div v-else class="max-h-64 overflow-y-auto pr-1">
          <OdmOptionsForm
            :catalog="uploadOdm.catalog.value"
            v-model="uploadValues"
            :field-type="uploadOdm.fieldType"
          />
        </div>

        <div v-if="uploading" class="rounded-md bg-primary/10 px-4 py-3 text-sm text-primary">
          {{ uploadProgress }}
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="showUpload = false">Cancel</Button>
      </template>
    </Dialog>

    <Dialog v-model:open="showRunDialog" :title="`Run ${runPluginDoc?.label || ''}`">
      <div v-if="runPluginDoc?.models?.length" class="mb-3 space-y-1.5">
        <Label for="run-model">Model</Label>
        <Select id="run-model" :model-value="selectedModelId" @update:model-value="chooseModel">
          <option v-for="m in runPluginDoc.models" :key="m.id" :value="m.id">{{ m.label }}</option>
          <option value="">Custom (type a model below)</option>
        </Select>
      </div>
      <PluginParamsForm
        v-if="runPluginDoc"
        :schema="runPluginDoc.params_schema"
        v-model="runParams"
      />
      <template #footer>
        <Button variant="ghost" @click="showRunDialog = false">Cancel</Button>
        <Button :loading="runStarting" @click="startPluginRun">Run</Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Box,
  Check,
  CircleX,
  CloudUpload,
  Layers,
  Minus,
  Play,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  X,
} from 'lucide-vue-next'
import { Badge, Button, Dialog, Label, Select } from '@/components/ui'
import { statusVariant } from '@/lib/status'
import { toast } from '@/lib/toast'
import L from 'leaflet'
import { formatVolume } from '@/lib/format'
import {
  VOLUME_BASE_METHODS,
  loadVolumeBaseMethod,
  saveVolumeBaseMethod,
} from '@/lib/volumeMethods'
import { BASEMAPS, createBasemap } from '@/lib/mapLayers'
import { sortImagesByCapture } from '@/lib/flightPath'
import { useMeasure } from '@/composables/useMeasure'
import { listPresets, getSettings } from '@/lib/presets'
import { useOdmOptions } from '@/composables/useOdmOptions'
import OdmOptionsForm from '@/components/OdmOptionsForm.vue'
import PluginParamsForm from '@/components/PluginParamsForm.vue'
import {
  listPlugins,
  listRuns,
  runPlugin,
  cancelRun,
  getRunGeojson,
  runTileUrl,
  runDownloadUrl,
  schemaDefaults,
  latestRunPerPlugin,
  shouldRenderVector,
} from '@/lib/plugins'
import { detectionStyle, detectionLegend } from '@/lib/detections'
import { segmentationStyle, segmentationLegend } from '@/lib/segmentation'
import { applyModelChoice, matchingModel } from '@/lib/knownModels'

const route = useRoute()
const router = useRouter()
const project = ref(null)
const tasks = ref([])
const selectedTask = ref(null)
const showUpload = ref(false)
const uploading = ref(false)
const uploadProgress = ref('')
const uploadPresets = ref([])
const selectedPreset = ref(null)
const uploadValues = ref({}) // { optionName: value }
const uploadOdm = useOdmOptions()

async function loadUploadForm() {
  try {
    uploadPresets.value = await listPresets()
  } catch (e) {
    uploadPresets.value = []
  }
  try {
    const s = await getSettings()
    selectedPreset.value = s.default_preset || null
  } catch (e) {
    selectedPreset.value = null
  }
  await uploadOdm.load()
  applyPreset()
}

function applyPreset() {
  const p = uploadPresets.value.find(x => x.name === selectedPreset.value)
  uploadValues.value = p ? Object.fromEntries((p.options || []).map(o => [o.name, o.value])) : {}
  // Re-seed enum defaults after (re)selecting a preset so switching presets
  // never leaves a shown-but-unsubmitted select value.
  uploadOdm.seedEnumDefaults(uploadValues.value)
}

function uploadOptionsArray() {
  const out = []
  for (const [name, value] of Object.entries(uploadValues.value)) {
    if (value === '' || value === null || value === undefined || value === false) continue
    out.push({ name, value })
  }
  return out
}

function openUpload() {
  showUpload.value = true
  loadUploadForm()
}
const sidebarWidth = ref(360)
// Collapsible so the basemap/layers/show card never eats a narrow map pane.
const panelOpen = ref(true)
// Raster overlays available for the selected task: { key, label, visible }.
const overlays = ref([])
const currentBasemap = ref('osm')
const showMarkers = ref(true)
const currentImages = ref([])
const currentTask = ref(null)
const hasDsm = computed(() => !!currentTask.value?.dsm)
const volumeBaseMethod = ref(loadVolumeBaseMethod())
const hasGps = computed(() =>
  currentImages.value.some(img => {
    const lat = parseFloat(img.latitude)
    const lng = parseFloat(img.longitude)
    return !isNaN(lat) && !isNaN(lng) && !(lat === 0 && lng === 0)
  })
)
let resizing = false
let map = null
// Union of everything currently plotted (markers + overlay extents) so
// "Zoom To Fit" frames the data instead of the whole globe.
let dataBounds = null
let imageMarkers = null
let baseLayer = null
let flightPathLayer = null
const showFlightPath = ref(false)
const measure = useMeasure(() => map, { onVolume: computeVolume })
const overlayLayers = {}  // key -> Leaflet tileLayer/geojson layer
const warnedLargeRuns = new Set()  // run names already warned as too large

// Analysis plugins enabled for the caller's organization, and the runs for the
// selected task. Populated lazily; labels fall back to the plugin id.
const availablePlugins = ref([])
const taskRuns = ref([])
const showRunDialog = ref(false)
const runPluginDoc = ref(null)
const runParams = ref({})
const runStarting = ref(false)

const runnablePlugins = computed(() => availablePlugins.value.filter(p => p.runnable))

// One row per plugin (its newest run), so repeated runs never accumulate.
const displayRuns = computed(() => latestRunPerPlugin(taskRuns.value))

function pluginLabel(opId) {
  return availablePlugins.value.find(p => p.op_id === opId)?.label || opId
}

async function loadPlugins() {
  try {
    availablePlugins.value = await listPlugins()
  } catch {
    availablePlugins.value = []
  }
}

async function loadTaskRuns(taskName) {
  if (!taskName) {
    taskRuns.value = []
    return
  }
  try {
    taskRuns.value = await listRuns(taskName)
  } catch {
    taskRuns.value = []
  }
}

function openRunDialog(plugin) {
  runPluginDoc.value = plugin
  // Start from the operation's schema defaults, then the organization's saved
  // settings, so the dialog never opens blank and users don't invent values.
  runParams.value = { ...schemaDefaults(plugin.params_schema), ...(plugin.settings || {}) }
  showRunDialog.value = true
}

const selectedModelId = computed(() => {
  const model = matchingModel(runPluginDoc.value?.models, runParams.value)
  return model ? model.id : ''
})

function chooseModel(id) {
  const model = (runPluginDoc.value?.models || []).find(m => m.id === id)
  if (model) runParams.value = applyModelChoice(runParams.value, model)
}

async function startPluginRun() {
  if (!runPluginDoc.value || !selectedTask.value) return
  runStarting.value = true
  try {
    await runPlugin({
      plugin: runPluginDoc.value.op_id,
      task: selectedTask.value,
      params: runParams.value,
    })
    showRunDialog.value = false
    toast.success('Analysis started')
    await startRunPolling()
  } catch (e) {
    toast.error(e.message || 'Failed to start analysis')
  } finally {
    runStarting.value = false
  }
}

const RUN_TERMINAL = ['Completed', 'Failed', 'Cancelled']
const isRunTerminal = status => RUN_TERMINAL.includes(status)
let runPollTimer = null

// Poll the task's run list (not a single id) so the UI keeps up even if a poll
// fails or the tab was backgrounded. Stops once no run is Queued/Running.
async function pollRuns() {
  runPollTimer = null
  if (!selectedTask.value) return
  try {
    await loadTaskRuns(selectedTask.value)
  } catch {
    // Keep polling through transient errors.
  }
  const active = taskRuns.value.some(r => !isRunTerminal(r.status))
  if (active) {
    runPollTimer = setTimeout(pollRuns, 2500)
  } else if (currentTask.value) {
    await loadOverlays(currentTask.value)
  }
}

async function startRunPolling() {
  if (runPollTimer) clearTimeout(runPollTimer)
  await loadTaskRuns(selectedTask.value)
  runPollTimer = setTimeout(pollRuns, 1500)
}

function onVisibility() {
  // A backgrounded tab throttles timers, so refresh immediately on return.
  if (document.visibilityState === 'visible' && selectedTask.value) startRunPolling()
}

async function cancelPluginRun(run) {
  try {
    await cancelRun(run.name)
    toast.success('Analysis cancelled')
    await loadTaskRuns(selectedTask.value)
  } catch (e) {
    toast.error(e.message || 'Failed to cancel analysis')
  }
}

const DATASET_LABELS = { orthophoto: 'Orthophoto', dsm: 'DSM', dtm: 'DTM' }
const DATASET_EXTENT_FIELD = {
  orthophoto: 'orthophoto_extent',
  dsm: 'dsm_extent',
  dtm: 'dtm_extent',
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
    extendDataBounds(bounds)
    map.fitBounds(bounds, { padding: [50, 50] })
  }
}

// Accumulate plotted extents; `null` bounds and empty groups are ignored.
function extendDataBounds(bounds) {
  if (!bounds || !bounds.isValid || !bounds.isValid()) return
  if (dataBounds) dataBounds.extend(bounds)
  else dataBounds = L.latLngBounds(bounds.getSouthWest(), bounds.getNorthEast())
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
// populated during asset download, plus overlays for its plugin-run outputs.
async function loadOverlays(task) {
  clearOverlays()
  taskRuns.value = []
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
    overlays.value.push({ key, label: DATASET_LABELS[key], visible, opacity: 100, kind: 'raster' })
    extendDataBounds(bounds)
    if (visible && bounds) fitB = bounds
  }
  if (fitB) map.fitBounds(fitB, { padding: [30, 30] })

  await loadRunOverlays(task.name)
}

// Plugin-run outputs: raster outputs become tile layers, vector outputs become
// GeoJSON layers. Both start hidden so the base orthophoto stays readable.
async function loadRunOverlays(taskName) {
  let runs = []
  try {
    runs = await listRuns(taskName)
  } catch {
    return
  }
  taskRuns.value = runs

  // Only the newest run per plugin gets a layer, keyed by plugin so a new run
  // supersedes the previous one instead of stacking another layer.
  for (const run of latestRunPerPlugin(runs)) {
    if (run.status !== 'Completed' || !run.output_file) continue
    const key = `plugin:${run.plugin}`
    if (overlayLayers[key]) continue
    const label = pluginLabel(run.plugin)

    if (run.output_kind === 'raster') {
      const bounds = parseExtentBounds(run.output_extent)
      const layer = L.tileLayer(runTileUrl(run.name), {
        bounds: bounds || undefined,
        maxNativeZoom: 22,
        maxZoom: 24,
        tileSize: 256,
        opacity: 1,
      })
      overlayLayers[key] = layer
      overlays.value.push({ key, label, visible: false, opacity: 80, kind: 'raster' })
      extendDataBounds(bounds)
    } else if (run.output_kind === 'vector') {
      try {
        const geojson = await getRunGeojson(run.name)
        const count = Array.isArray(geojson?.features) ? geojson.features.length : 0
        if (!shouldRenderVector(count)) {
          if (!warnedLargeRuns.has(run.name)) {
            warnedLargeRuns.add(run.name)
            const hint = run.render_kind === 'segmentation'
              ? 're-run with a larger minimum segment area'
              : 're-run with a coarser interval'
            toast.error(
              `${label}: ${count.toLocaleString()} features is too large to display. ` +
              `Download it, or ${hint}.`,
            )
          }
          continue
        }
        const style = run.render_kind === 'detections'
          ? detectionStyle
          : run.render_kind === 'segmentation'
            ? segmentationStyle
            : { color: '#2563eb', weight: 1.5 }
        const legend = run.render_kind === 'detections'
          ? detectionLegend(geojson.features)
          : run.render_kind === 'segmentation'
            ? segmentationLegend(geojson.features)
            : null
        const layer = L.geoJSON(geojson, {
          renderer: L.canvas(),  // canvas avoids per-feature DOM/SVG overhead
          smoothFactor: 2,
          style,
        })
        overlayLayers[key] = layer
        overlays.value.push({
          key, label, visible: false, opacity: 100, kind: 'vector',
          legend,
        })
        const bounds = layer.getBounds()
        if (bounds && bounds.isValid()) extendDataBounds(bounds)
      } catch {
        // Conversion can fail when the geo service is down; skip this overlay.
      }
    }
  }
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
  dataBounds = null // recomputed by the two plotting calls below
  plotImageMarkers(full.images)
  loadOverlays(full)
  if (showFlightPath.value) buildFlightPath(currentImages.value)
}

function startMeasure(mode) {
  measure.start(mode)
}

function finishMeasure() {
  measure.finish()
}

function clearMeasure() {
  measure.clear()
}

// Persist the choice like WebODM does, and re-measure any polygon already drawn
// so the readout matches the newly selected base surface.
function onVolumeBaseMethodChange(method) {
  volumeBaseMethod.value = method
  saveVolumeBaseMethod(method)
  measure.recomputeVolume()
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
      body: JSON.stringify({
        task_name: selectedTask.value,
        polygon: JSON.stringify(polygon),
        method: volumeBaseMethod.value,
      }),
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
  if (!map) return
  // Frame the loaded task (markers + raster extents); only fall back to the
  // whole world when nothing has been plotted yet.
  if (dataBounds && dataBounds.isValid()) map.fitBounds(dataBounds, { padding: [30, 30] })
  else map.fitWorld()
}

function startResize() {
  resizing = true
}

function stopResize() {
  if (!resizing) return
  resizing = false
  // Leaflet only watches window resize, so the map pane stays mis-sized after
  // the sidebar drag until we tell it the container changed.
  if (map) map.invalidateSize()
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
    // Mirror the backend's Pending -> Queued handoff so the row stops offering
    // Start immediately; writing 'Pending' here left the button visible.
    const idx = tasks.value.findIndex(t => t.name === task.name)
    if (idx !== -1) tasks.value[idx].status = 'Queued'
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
    if (!res.ok) {
      // Backend fails loud when the node won't acknowledge the cancel — surface
      // its real message rather than a generic one, and do NOT flip status to
      // Cancelled (the task may still be running on the node).
      let msg = 'Failed to cancel task'
      try {
        const err = await res.json()
        const server = err?._server_messages ? JSON.parse(JSON.parse(err._server_messages)[0])?.message : null
        msg = server || err?.message || msg
      } catch {}
      throw new Error(msg)
    }
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
    formData.append('options', JSON.stringify(uploadOptionsArray()))
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
  loadPlugins()
  document.addEventListener('visibilitychange', onVisibility)

  map = L.map('map').setView([0, 0], 2)
  baseLayer = createBasemap(currentBasemap.value)
  baseLayer.addTo(map)
})

onUnmounted(() => {
  document.removeEventListener('visibilitychange', onVisibility)
  if (runPollTimer) clearTimeout(runPollTimer)
  measure.clear()
  removeFlightPath()
  if (map) {
    map.remove()
    map = null
  }
})
</script>
