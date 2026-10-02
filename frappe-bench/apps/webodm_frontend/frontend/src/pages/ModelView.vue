<template>
  <div class="flex h-full flex-col bg-background">
    <div class="flex flex-shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2 sm:px-4">
      <Button variant="ghost" size="icon" class="h-8 w-8" title="Back to project" aria-label="Back to project" @click="backToProject">
        <ArrowLeft />
      </Button>
      <h2 class="truncate text-base font-medium text-foreground">
        {{ task?.title || task?.name || (taskLoading ? 'Loading…' : '3D viewer') }}
        <span v-if="source.kind === 'run'" class="text-muted-foreground">· {{ sourceLabel }}</span>
        <span v-else-if="isPointCloud" class="text-muted-foreground">· Point cloud</span>
      </h2>
      <Badge v-if="task" :variant="statusVariant(task.status)">{{ task.status }}</Badge>

      <div class="ml-auto flex flex-wrap items-center gap-2">
        <Select
          v-if="choices.length > 1"
          :model-value="currentChoice"
          class="h-8 w-auto max-w-[16rem] py-0 text-xs"
          title="Switch between the task's model, its 3D reconstructions, its point cloud and other tasks"
          aria-label="Model"
          @update:model-value="switchDataset"
        >
          <option v-for="c in choices" :key="c.value" :value="c.value">{{ c.label }}</option>
        </Select>
        <Button variant="outline" size="sm" @click="openConsole">
          <Terminal />
          <span class="hidden sm:inline">Console</span>
        </Button>
        <a
          v-if="source.url"
          :href="source.url"
          download
          class="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-accent"
          :title="isPointCloud ? 'Download the point cloud (LAZ)' : 'Download the model'"
        >
          <Download class="size-3.5" />
          <span class="hidden sm:inline">Download</span>
        </a>
      </div>
    </div>

    <!-- Stage: canvas + overlays. Focusable so keyboard shortcuts work after a click. -->
    <div
      ref="stageRef"
      class="relative min-h-0 flex-1 bg-[#1c2030] outline-none"
      :class="viewer.measure.kind && 'cursor-crosshair'"
      tabindex="0"
      :aria-label="isPointCloud ? '3D point cloud viewer' : '3D model viewer'"
      @keydown="onKeydown"
      @pointerdown="stageRef?.focus({ preventScroll: true })"
    >
      <div ref="canvasRef" class="absolute inset-0" />

      <template v-if="viewer.state.status === 'ready'">
        <ModelToolbar
          :mode="viewer.state.mode"
          :grid-visible="viewer.state.gridVisible"
          :fullscreen="viewer.state.fullscreen"
          :measure-enabled="isPointCloud"
          :measure-kind="viewer.measure.kind"
          :volume-enabled="hasDsm"
          :has-measurements="viewer.measure.measurements.length > 0"
          @update:mode="setMode"
          @zoom-in="act('zoomIn')"
          @zoom-out="act('zoomOut')"
          @reset="act('reset')"
          @view="p => act(`view${p[0].toUpperCase()}${p.slice(1)}`)"
          @toggle-grid="act('toggleGrid')"
          @toggle-fullscreen="toggleFullscreen"
          @help="helpOpen = true"
          @measure="setMeasure"
          @clear-measurements="viewer.clearMeasurements()"
        />

        <!-- Point cloud controls (right side) -->
        <div v-if="isPointCloud && viewer.pointCloud.metadata" class="pointer-events-none absolute right-3 top-3 z-10 flex flex-col items-end gap-2">
          <PointCloudPanel
            :metadata="viewer.pointCloud.metadata"
            :settings="pcSettings"
            :budget-cap="budgetCap"
            :stats="viewer.pointCloud"
            @update="setPointCloudSetting"
          />
        </div>

        <!-- Measurement labels, anchored on the shapes -->
        <div class="pointer-events-none absolute inset-0 z-10 overflow-hidden" data-measure-labels>
          <div
            v-for="label in viewer.measure.labels"
            :key="label.id"
            class="pointer-events-auto absolute flex -translate-x-1/2 -translate-y-full items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 font-mono text-[11px] shadow"
            :class="label.status === 'drawing' ? 'bg-sky-500/90 text-white' : label.status === 'error' ? 'bg-destructive text-destructive-foreground' : 'bg-amber-500/95 text-black'"
            :style="{ left: `${label.x}px`, top: `${label.y - 10}px` }"
            :data-measure-label="label.kind"
          >
            <LoaderCircle v-if="label.status === 'pending'" class="size-3 animate-spin" />
            <span>{{ label.text }}</span>
            <button
              v-if="label.status !== 'drawing'"
              type="button"
              class="ml-1 rounded px-1 text-black/60 hover:bg-black/10 hover:text-black"
              title="Remove measurement"
              aria-label="Remove measurement"
              @click="viewer.removeMeasurement(label.id)"
            >
              ×
            </button>
          </div>
        </div>

        <Transition
          enter-active-class="transition-opacity duration-300"
          leave-active-class="transition-opacity duration-500"
          enter-from-class="opacity-0"
          leave-to-class="opacity-0"
        >
          <p
            v-if="hintVisible || viewer.measure.kind"
            class="pointer-events-none absolute bottom-3 left-3 z-10 max-w-[calc(100%-1.5rem)] rounded-md bg-black/50 px-2.5 py-1 text-xs text-white/90 backdrop-blur"
          >
            {{ viewer.measure.kind ? measureHint : hintText }}
          </p>
        </Transition>

        <p
          class="pointer-events-auto absolute bottom-3 right-3 z-10 hidden rounded-md bg-black/50 px-2.5 py-1 font-mono text-[11px] text-white/80 backdrop-blur sm:block"
          :title="statsTitle"
        >
          <template v-if="isPointCloud">
            {{ formatCount(viewer.pointCloud.visiblePoints) }} / {{ formatCount(viewer.pointCloud.totalPoints) }} pts<template v-if="viewer.pointCloud.loadingNodes"> · loading…</template>
          </template>
          <template v-else>
            {{ formatCount(viewer.state.stats.triangles) }} tris · {{ formatBytes(viewer.state.stats.bytes) }}
          </template>
        </p>
      </template>

      <!-- Loading (download / parse, or the first-open octree conversion) -->
      <div v-if="viewer.state.status === 'loading' || taskLoading || converting" class="absolute inset-0 z-20 flex items-center justify-center bg-[#1c2030]/85">
        <div class="w-72 text-center" data-loading>
          <LoaderCircle class="mx-auto mb-3 size-8 animate-spin text-primary" />
          <p class="text-sm text-white/90">{{ loadingLabel }}</p>
          <p v-if="converting" class="mt-1 text-xs text-white/60">
            The first open converts the point cloud into a streamable octree. This can take a few minutes for large clouds; later opens are instant.
          </p>
          <template v-if="viewer.state.phase === 'download'">
            <div class="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-white/15">
              <div
                class="h-full rounded-full bg-primary transition-[width] duration-200"
                :class="downloadPercent === null && 'animate-pulse w-1/3'"
                :style="downloadPercent !== null ? { width: `${downloadPercent}%` } : null"
              />
            </div>
            <p class="mt-1.5 font-mono text-[11px] text-white/60">
              {{ formatBytes(viewer.state.loaded) }}<template v-if="viewer.state.total"> / {{ formatBytes(viewer.state.total) }}</template>
            </p>
          </template>
        </div>
      </div>

      <!-- Error (viewer or conversion) -->
      <div v-else-if="viewer.state.status === 'error' || conversionError" class="absolute inset-0 z-20 flex items-center justify-center bg-[#1c2030]/85 p-4">
        <div class="max-w-md text-center" data-error>
          <TriangleAlert class="mx-auto mb-3 size-10 text-warning" />
          <p class="text-sm font-medium text-white">
            {{ conversionError ? 'The point cloud could not be prepared' : isPointCloud ? 'The point cloud could not be displayed' : 'The 3D model could not be displayed' }}
          </p>
          <p class="mt-1 text-sm text-white/70">{{ conversionError || viewer.state.error }}</p>
          <div class="mt-4 flex justify-center gap-2">
            <Button size="sm" @click="retry">
              <RefreshCw />
              Retry
            </Button>
            <Button size="sm" variant="outline" @click="backToProject">Back to project</Button>
          </div>
        </div>
      </div>

      <!-- WebGL unavailable -->
      <div v-else-if="viewer.state.status === 'unsupported'" class="absolute inset-0 z-20 flex items-center justify-center bg-[#1c2030] p-4">
        <div class="max-w-md text-center">
          <MonitorX class="mx-auto mb-3 size-10 text-white/60" />
          <p class="text-sm font-medium text-white">3D rendering is not available in this browser</p>
          <p class="mt-1 text-sm text-white/70">
            WebGL is disabled or unsupported. Enable hardware acceleration or use a recent version of Chrome, Firefox, Safari or Edge.
          </p>
          <div class="mt-4 flex justify-center gap-2">
            <a v-if="source.url" :href="source.url" download class="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90">
              <Download class="size-3.5" />
              {{ isPointCloud ? 'Download point cloud' : 'Download model' }}
            </a>
            <Button size="sm" variant="outline" @click="backToProject">Back to project</Button>
          </div>
        </div>
      </div>

      <!-- Empty: no model yet / never / task missing -->
      <div v-else-if="emptyState" class="absolute inset-0 z-20 flex items-center justify-center bg-[#1c2030] p-4">
        <div class="max-w-md text-center">
          <LoaderCircle v-if="emptyState.kind === 'processing'" class="mx-auto mb-3 size-10 animate-spin text-primary" />
          <Box v-else class="mx-auto mb-3 size-10 text-white/50" />
          <p class="text-sm font-medium text-white">{{ emptyState.title }}</p>
          <p v-if="emptyState.kind === 'processing' && taskProgress !== null" class="mt-1 font-mono text-sm text-white/80">
            {{ taskProgress }}%
          </p>
          <p class="mt-1 text-sm text-white/70">{{ emptyState.detail }}</p>
          <div class="mt-4 flex justify-center gap-2">
            <Button v-if="emptyState.kind !== 'missing'" size="sm" variant="outline" @click="openConsole">
              <Terminal />
              Open console
            </Button>
            <Button size="sm" variant="outline" @click="backToProject">Back to project</Button>
          </div>
        </div>
      </div>
    </div>

    <Dialog v-model:open="helpOpen" title="Viewer controls" description="Mouse, touch and keyboard shortcuts.">
      <div class="mt-4 grid gap-4 text-sm sm:grid-cols-2">
        <div>
          <p class="mb-1.5 font-medium text-foreground">Mouse</p>
          <dl class="space-y-1 text-muted-foreground">
            <div class="flex justify-between gap-3"><dt>Rotate</dt><dd class="text-right">Left-drag</dd></div>
            <div class="flex justify-between gap-3"><dt>Pan</dt><dd class="text-right">Right-drag</dd></div>
            <div class="flex justify-between gap-3"><dt>Zoom</dt><dd class="text-right">Scroll / middle-drag</dd></div>
            <div class="flex justify-between gap-3"><dt>Focus point</dt><dd class="text-right">Double-click</dd></div>
          </dl>
          <p class="mb-1.5 mt-4 font-medium text-foreground">Touch</p>
          <dl class="space-y-1 text-muted-foreground">
            <div class="flex justify-between gap-3"><dt>Rotate</dt><dd class="text-right">One finger</dd></div>
            <div class="flex justify-between gap-3"><dt>Zoom &amp; pan</dt><dd class="text-right">Two fingers</dd></div>
          </dl>
          <p class="mt-3 text-xs text-muted-foreground">The Rotate / Pan / Zoom buttons change what the left button and one finger do.</p>
          <template v-if="isPointCloud">
            <p class="mb-1.5 mt-4 font-medium text-foreground">Measuring (point cloud)</p>
            <dl class="space-y-1 text-muted-foreground">
              <div class="flex justify-between gap-3"><dt>Add a point</dt><dd class="text-right">Click on the cloud</dd></div>
              <div class="flex justify-between gap-3"><dt>Finish</dt><dd class="text-right">Enter, double-click, or click the first point</dd></div>
              <div class="flex justify-between gap-3"><dt>Cancel</dt><dd class="text-right">Esc</dd></div>
            </dl>
            <p class="mt-2 text-xs text-muted-foreground">Distances follow the picked points in 3D; areas are horizontal. Volumes come from the task's DSM, never from the points, so they need a DSM.</p>
          </template>
        </div>
        <div>
          <p class="mb-1.5 font-medium text-foreground">Keyboard</p>
          <dl class="space-y-1 text-muted-foreground">
            <div v-for="row in KEYBOARD_HELP" :key="row[1]" class="flex justify-between gap-3">
              <dt>{{ row[1] }}</dt>
              <dd class="flex gap-1">
                <kbd v-for="k in row[0]" :key="k" class="rounded border border-border bg-muted px-1.5 font-mono text-[11px] text-foreground">{{ k }}</kbd>
              </dd>
            </div>
          </dl>
        </div>
      </div>
      <div class="mt-5 flex justify-end">
        <Button size="sm" @click="helpOpen = false">Done</Button>
      </div>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Box, Download, LoaderCircle, MonitorX, RefreshCw, Terminal, TriangleAlert } from 'lucide-vue-next'
import { Badge, Button, Dialog, Select } from '@/components/ui'
import ModelToolbar from '@/components/ModelToolbar.vue'
import PointCloudPanel from '@/components/PointCloudPanel.vue'
import { statusVariant } from '@/lib/status'
import { toast } from '@/lib/toast'
import { useModelViewer } from '@/composables/useModelViewer'
import {
  PROCESSING_STATUSES,
  emptyStateFor,
  formatBytes,
  formatCount,
  hintFor,
  keyAction,
  modelChoices,
  modelSourceFor,
  progressPercent,
} from '@/lib/modelViewer'
import { listPlugins, listRuns } from '@/lib/plugins'
import {
  CONVERTING_STATUSES,
  MEASURE_LABELS,
  POINT_CLOUD_SOURCE,
  budgetCapFor,
  computeVolumeNative,
  conversionLabel,
  defaultColorMode,
  getPotreeState,
  loadPointCloudSettings,
  pointCloudRoute,
  savePointCloudSettings,
} from '@/lib/potree'
import { loadVolumeBaseMethod } from '@/lib/volumeMethods'

const KEYBOARD_HELP = [
  [['←', '→', '↑', '↓'], 'Pan (also W A S D)'],
  [['+', '−'], 'Zoom in / out'],
  [['R'], 'Reset view'],
  [['1', '2', '3', '4'], 'Isometric / Top / North / East'],
  [['G'], 'Toggle ground grid'],
  [['F'], 'Fullscreen'],
  [['Enter'], 'Finish measurement'],
  [['Esc'], 'Cancel measurement'],
  [['?'], 'This help'],
]

const POLL_MS = 5000
const CONVERSION_POLL_MS = 4000
const HINT_MS = 6000

const route = useRoute()
const router = useRouter()

const stageRef = ref(null)
const canvasRef = ref(null)
const task = ref(null)
const taskLoading = ref(true)
const datasets = ref([])
// Completed plugin runs with a model output for this task (3D reconstructions).
const modelRuns = ref([])
const pluginLabels = ref({})
const helpOpen = ref(false)
const hintVisible = ref(false)
// Octree conversion state from the backend (point cloud source only).
const potreeState = ref(null)

let pollTimer = null
let conversionTimer = null
let hintTimer = null
let requestSeq = 0

const viewer = useModelViewer(canvasRef, {
  onInteract: () => showHint(false),
  onVolume: computeVolume,
  onMeasureMiss: () => toast.info('No point under the cursor — click on the point cloud'),
})
// Dev-only hook so browser tests can read camera state without scraping pixels.
if (import.meta.env.DEV) window.__modelViewer = viewer

const taskId = computed(() => String(route.params.taskId || ''))
const projectId = computed(() => String(route.params.id || ''))
const runId = computed(() => (route.query.run ? String(route.query.run) : ''))
const sourceKind = computed(() => (route.query.source === POINT_CLOUD_SOURCE ? POINT_CLOUD_SOURCE : ''))

// What is shown: the task's ODM model, a reconstruction run's model (?run=),
// or the task's point cloud (?source=pointcloud).
const source = computed(() => modelSourceFor(task.value, modelRuns.value, runId.value, sourceKind.value))
const isPointCloud = computed(() => source.value.kind === 'pointcloud')
const labelFor = id => pluginLabels.value[id] || String(id || '').split('.').pop()
const sourceLabel = computed(() => (source.value.run ? labelFor(source.value.run.plugin) : ''))
const choices = computed(() => modelChoices(datasets.value, modelRuns.value, taskId.value, labelFor))
const currentChoice = computed(() => {
  if (source.value.kind === 'run') return `run:${runId.value}`
  if (sourceKind.value === POINT_CLOUD_SOURCE) return `pointcloud:${taskId.value}`
  return `task:${taskId.value}`
})

const emptyState = computed(() => (taskLoading.value ? null : emptyStateFor(task.value, source.value)))
const hasDsm = computed(() => !!(task.value?.dsm || potreeState.value?.has_dsm))

const converting = computed(() => isPointCloud.value && !!potreeState.value
  && (CONVERTING_STATUSES.includes(potreeState.value.status) || (potreeState.value.status === 'Ready' && potreeState.value.cache === 'warming')))
const conversionError = computed(() => (isPointCloud.value && potreeState.value?.status === 'Failed'
  ? potreeState.value.error || 'The conversion failed' : null))

const taskProgress = computed(() => {
  const p = task.value?.node_progress ?? task.value?.progress
  return p == null ? null : Math.round(Number(p))
})

const downloadPercent = computed(() => progressPercent(viewer.state.loaded, viewer.state.total))

const coarsePointer = typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches
const hintText = computed(() => hintFor(viewer.state.mode, coarsePointer))
const measureHint = computed(() => {
  const kind = viewer.measure.kind
  if (!kind) return ''
  const n = viewer.measure.points.length
  const need = kind === 'distance' ? 2 : 3
  if (n < need) return `${MEASURE_LABELS[kind]}: click ${need - n} more point${need - n === 1 ? '' : 's'} on the cloud (Esc to cancel)`
  return kind === 'distance'
    ? `${viewer.measure.text} — click to add points, Enter or double-click to finish`
    : `${viewer.measure.text} — click the first point, Enter or double-click to close`
})

const statsTitle = computed(() => {
  if (isPointCloud.value) {
    const pc = viewer.pointCloud
    return `${pc.visiblePoints.toLocaleString()} of ${pc.totalPoints.toLocaleString()} points drawn · ${pc.visibleNodes} octree nodes`
  }
  const { vertices, textures, textureCap } = viewer.state.stats
  const parts = [`${vertices.toLocaleString()} vertices`, `${textures} textures`]
  if (textureCap) parts.push(`textures limited to ${textureCap} px for this device`)
  return parts.join(' · ')
})

const loadingLabel = computed(() => {
  if (taskLoading.value) return 'Loading task…'
  if (converting.value) return conversionLabel(potreeState.value)
  switch (viewer.state.phase) {
    case 'download': return 'Downloading model…'
    case 'extract': return 'Extracting archive…'
    case 'parse':
      return viewer.state.texturesTotal
        ? `Decoding textures ${viewer.state.texturesDone}/${viewer.state.texturesTotal}…`
        : 'Decoding geometry…'
    case 'prepare': return 'Preparing scene…'
    case 'octree': return 'Loading point cloud…'
    default: return 'Loading…'
  }
})

// ------------------------------------------------------- point cloud UI

const budgetCap = budgetCapFor(typeof navigator !== 'undefined' ? navigator.deviceMemory : undefined, coarsePointer)
const pcSettings = reactive({
  ...loadPointCloudSettings(),
  colorMode: 'rgb',
  elevationFilter: null,
  hiddenClasses: [],
})
if (pcSettings.pointBudget > budgetCap) pcSettings.pointBudget = budgetCap

function setPointCloudSetting(key, value) {
  pcSettings[key] = value
  viewer.setPointCloudOption(key, value)
  if (['pointSize', 'pointBudget', 'background'].includes(key)) savePointCloudSettings(pcSettings)
}

function applyPointCloudSettings() {
  pcSettings.colorMode = defaultColorMode(viewer.pointCloud.metadata)
  pcSettings.elevationFilter = null
  pcSettings.hiddenClasses = []
  for (const key of ['colorMode', 'pointSize', 'pointBudget', 'background', 'elevationFilter', 'hiddenClasses']) {
    viewer.setPointCloudOption(key, pcSettings[key])
  }
}

function setMeasure(kind) {
  if (kind === 'volume' && !hasDsm.value) {
    toast.error('Volume measurement needs a DSM; this task has none')
    return
  }
  viewer.startMeasurement(kind)
  showHint(false)
}

// Volume is never derived from the points: the polygon (native CRS) goes to
// the DSM volume endpoint, which refuses when the task has no surface.
async function computeVolume(points) {
  if (!hasDsm.value) throw new Error('this task has no DSM')
  const projection = viewer.pointCloud.metadata?.projection || 'native'
  return computeVolumeNative(taskId.value, points, projection, loadVolumeBaseMethod())
}

// ------------------------------------------------------------ data access

function csrfHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
  return headers
}

async function fetchTask(name) {
  try {
    const res = await fetch('/api/method/webodm_core.api.task.get_task_progress', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ task_name: name }),
    })
    if (!res.ok) return null
    const { message } = await res.json()
    return message || null
  } catch {
    return null
  }
}

// Other tasks in this project that already have a model or a point cloud, for the switcher.
async function fetchDatasets() {
  try {
    const filters = JSON.stringify([['project', '=', projectId.value]])
    const orFilters = JSON.stringify([['model', 'is', 'set'], ['point_cloud', 'is', 'set']])
    const fields = JSON.stringify(['name', 'title', 'status', 'model', 'point_cloud'])
    const res = await fetch(`/api/resource/WebODM%20Task?filters=${encodeURIComponent(filters)}&or_filters=${encodeURIComponent(orFilters)}&fields=${encodeURIComponent(fields)}&limit_page_length=200`)
    if (!res.ok) return
    const data = await res.json()
    datasets.value = data.data || []
  } catch {
    datasets.value = []
  }
}

// Reconstruction runs for this task, so the switcher can offer them and
// ?run= can be resolved to a file. Failures just leave the list empty.
async function fetchModelRuns() {
  try {
    const runs = await listRuns(taskId.value)
    modelRuns.value = (runs || []).filter(r => r.output_kind === 'model')
  } catch {
    modelRuns.value = []
  }
}

async function fetchPluginLabels() {
  if (Object.keys(pluginLabels.value).length) return
  try {
    const out = {}
    for (const p of await listPlugins()) out[p.op_id || p.name] = p.label
    pluginLabels.value = out
  } catch {
    pluginLabels.value = {}
  }
}

// ------------------------------------------------------------- lifecycle

async function loadTask() {
  const seq = ++requestSeq
  stopPolling()
  stopConversionPolling()
  taskLoading.value = true
  task.value = null
  potreeState.value = null
  viewer.clear()

  const [fetched] = await Promise.all([fetchTask(taskId.value), fetchDatasets(), fetchModelRuns(), fetchPluginLabels()])
  if (seq !== requestSeq) return
  task.value = fetched
  taskLoading.value = false

  if (isPointCloud.value && source.value.url) {
    await openPointCloud({ start: true })
  } else if (source.value.url) {
    await loadModel()
  } else if (fetched && PROCESSING_STATUSES.includes(fetched.status)) {
    startPolling()
  }
}

async function loadModel() {
  const url = source.value.url
  if (!url) return
  await viewer.load(url)
  if (viewer.state.status === 'ready') showHint(true)
}

// Point cloud: ask the backend for the octree (starting the conversion on the
// first open), poll while it is Queued/Running or being refetched from
// object storage, then stream it.
async function openPointCloud({ start = false, retry = false } = {}) {
  const seq = requestSeq
  let state
  try {
    state = await getPotreeState(taskId.value, { start, retry })
  } catch (e) {
    state = { status: 'Failed', error: e?.message || 'The conversion state could not be read' }
  }
  if (seq !== requestSeq) return
  potreeState.value = state
  if (state.status === 'Ready' && state.cache !== 'warming' && state.files) {
    stopConversionPolling()
    await viewer.loadPointCloud(taskId.value, state.files)
    if (seq !== requestSeq) return
    if (viewer.state.status === 'ready') {
      applyPointCloudSettings()
      showHint(true)
    }
  } else if (CONVERTING_STATUSES.includes(state.status) || (state.status === 'Ready' && state.cache === 'warming')) {
    startConversionPolling()
  } else if (!state.status && !start) {
    // Not converted and we did not ask to start: a stale state after reprocessing.
    await openPointCloud({ start: true })
  }
}

function startConversionPolling() {
  stopConversionPolling()
  conversionTimer = setTimeout(() => openPointCloud(), CONVERSION_POLL_MS)
}

function stopConversionPolling() {
  if (conversionTimer) clearTimeout(conversionTimer)
  conversionTimer = null
}

function retry() {
  if (conversionError.value) {
    potreeState.value = { ...potreeState.value, status: 'Queued', error: null }
    openPointCloud({ retry: true })
  } else if (isPointCloud.value) {
    openPointCloud({ start: true })
  } else {
    loadModel()
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    const updated = await fetchTask(taskId.value)
    if (!updated || updated.name !== task.value?.name) return
    task.value = updated
    if (source.value.url) {
      stopPolling()
      fetchDatasets()
      if (isPointCloud.value) await openPointCloud({ start: true })
      else await loadModel()
    } else if (!PROCESSING_STATUSES.includes(updated.status)) {
      stopPolling()
    }
  }, POLL_MS)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function showHint(on) {
  clearTimeout(hintTimer)
  hintVisible.value = on
  if (on) hintTimer = setTimeout(() => { hintVisible.value = false }, HINT_MS)
}

// --------------------------------------------------------------- actions

function act(action) {
  viewer.performAction(action)
}

function setMode(mode) {
  viewer.setMode(mode)
  showHint(true)
}

function toggleFullscreen() {
  viewer.toggleFullscreen(stageRef.value)
}

function onKeydown(event) {
  const action = keyAction(event)
  if (!action) return
  if (action === 'escape') {
    if (helpOpen.value) { helpOpen.value = false; event.preventDefault() }
    else if (viewer.measure.kind) { viewer.cancelMeasurement(); event.preventDefault() }
    return
  }
  if (action === 'finishMeasure') {
    if (viewer.measure.kind) { viewer.finishMeasurement(); event.preventDefault() }
    return
  }
  event.preventDefault()
  if (action === 'toggleHelp') { helpOpen.value = !helpOpen.value; return }
  if (action === 'toggleFullscreen') { toggleFullscreen(); return }
  if (viewer.state.status === 'ready') act(action)
}

function switchDataset(value) {
  if (!value || value === currentChoice.value) return
  const [kind, name] = String(value).split(/:(.+)/)
  if (kind === 'pointcloud') {
    router.push(pointCloudRoute(projectId.value, name))
    return
  }
  const base = `/project/${encodeURIComponent(projectId.value)}/task/${encodeURIComponent(kind === 'run' ? taskId.value : name)}/model`
  router.push(kind === 'run' ? `${base}?run=${encodeURIComponent(name)}` : base)
}

function backToProject() {
  router.push(`/project/${encodeURIComponent(projectId.value)}`)
}

function openConsole() {
  router.push(`/project/${encodeURIComponent(projectId.value)}/task/${encodeURIComponent(taskId.value)}/console`)
}

watch(taskId, (next, prev) => {
  if (next && next !== prev) loadTask()
})
watch([runId, sourceKind], ([run, kind], [prevRun, prevKind]) => {
  if ((run !== prevRun || kind !== prevKind) && !taskLoading.value) loadTask()
})

onMounted(() => {
  viewer.init()
  loadTask()
})

onBeforeUnmount(() => {
  requestSeq++
  stopPolling()
  stopConversionPolling()
  clearTimeout(hintTimer)
  viewer.dispose()
})
</script>
