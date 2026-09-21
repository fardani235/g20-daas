<template>
  <div class="flex h-full flex-col bg-background">
    <div class="flex flex-shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2 sm:px-4">
      <Button variant="ghost" size="icon" class="h-8 w-8" title="Back to project" aria-label="Back to project" @click="backToProject">
        <ArrowLeft />
      </Button>
      <h2 class="truncate text-base font-medium text-foreground">
        {{ task?.title || task?.name || (taskLoading ? 'Loading…' : '3D viewer') }}
      </h2>
      <Badge v-if="task" :variant="statusVariant(task.status)">{{ task.status }}</Badge>

      <div class="ml-auto flex flex-wrap items-center gap-2">
        <Select
          v-if="datasets.length > 1"
          :model-value="taskId"
          class="h-8 w-auto max-w-[14rem] py-0 text-xs"
          title="Switch to another 3D model in this project"
          aria-label="Dataset"
          @update:model-value="switchDataset"
        >
          <option v-for="d in datasets" :key="d.name" :value="d.name">{{ d.title || d.name }}</option>
        </Select>
        <Button variant="outline" size="sm" @click="openConsole">
          <Terminal />
          <span class="hidden sm:inline">Console</span>
        </Button>
        <a
          v-if="task?.model"
          :href="task.model"
          download
          class="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs font-medium text-foreground transition-colors hover:bg-accent"
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
      tabindex="0"
      aria-label="3D model viewer"
      @keydown="onKeydown"
      @pointerdown="stageRef?.focus({ preventScroll: true })"
    >
      <div ref="canvasRef" class="absolute inset-0" />

      <template v-if="viewer.state.status === 'ready'">
        <ModelToolbar
          :mode="viewer.state.mode"
          :grid-visible="viewer.state.gridVisible"
          :fullscreen="viewer.state.fullscreen"
          @update:mode="setMode"
          @zoom-in="act('zoomIn')"
          @zoom-out="act('zoomOut')"
          @reset="act('reset')"
          @view="p => act(`view${p[0].toUpperCase()}${p.slice(1)}`)"
          @toggle-grid="act('toggleGrid')"
          @toggle-fullscreen="toggleFullscreen"
          @help="helpOpen = true"
        />

        <Transition
          enter-active-class="transition-opacity duration-300"
          leave-active-class="transition-opacity duration-500"
          enter-from-class="opacity-0"
          leave-to-class="opacity-0"
        >
          <p
            v-if="hintVisible"
            class="pointer-events-none absolute bottom-3 left-3 z-10 max-w-[calc(100%-1.5rem)] rounded-md bg-black/50 px-2.5 py-1 text-xs text-white/90 backdrop-blur"
          >
            {{ hintText }}
          </p>
        </Transition>

        <p
          class="pointer-events-auto absolute bottom-3 right-3 z-10 hidden rounded-md bg-black/50 px-2.5 py-1 font-mono text-[11px] text-white/80 backdrop-blur sm:block"
          :title="statsTitle"
        >
          {{ formatCount(viewer.state.stats.triangles) }} tris · {{ formatBytes(viewer.state.stats.bytes) }}
        </p>
      </template>

      <!-- Loading -->
      <div v-if="viewer.state.status === 'loading' || taskLoading" class="absolute inset-0 z-20 flex items-center justify-center bg-[#1c2030]/85">
        <div class="w-64 text-center">
          <LoaderCircle class="mx-auto mb-3 size-8 animate-spin text-primary" />
          <p class="text-sm text-white/90">{{ loadingLabel }}</p>
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

      <!-- Error -->
      <div v-else-if="viewer.state.status === 'error'" class="absolute inset-0 z-20 flex items-center justify-center bg-[#1c2030]/85 p-4">
        <div class="max-w-md text-center">
          <TriangleAlert class="mx-auto mb-3 size-10 text-warning" />
          <p class="text-sm font-medium text-white">The 3D model could not be displayed</p>
          <p class="mt-1 text-sm text-white/70">{{ viewer.state.error }}</p>
          <div class="mt-4 flex justify-center gap-2">
            <Button size="sm" @click="loadModel">
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
            <a v-if="task?.model" :href="task.model" download class="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90">
              <Download class="size-3.5" />
              Download model
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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Box, Download, LoaderCircle, MonitorX, RefreshCw, Terminal, TriangleAlert } from 'lucide-vue-next'
import { Badge, Button, Dialog, Select } from '@/components/ui'
import ModelToolbar from '@/components/ModelToolbar.vue'
import { statusVariant } from '@/lib/status'
import { useModelViewer } from '@/composables/useModelViewer'
import {
  PROCESSING_STATUSES,
  emptyStateFor,
  formatBytes,
  formatCount,
  hintFor,
  keyAction,
  progressPercent,
} from '@/lib/modelViewer'

const KEYBOARD_HELP = [
  [['←', '→', '↑', '↓'], 'Pan (also W A S D)'],
  [['+', '−'], 'Zoom in / out'],
  [['R'], 'Reset view'],
  [['1', '2', '3', '4'], 'Isometric / Top / North / East'],
  [['G'], 'Toggle ground grid'],
  [['F'], 'Fullscreen'],
  [['?'], 'This help'],
]

const POLL_MS = 5000
const HINT_MS = 6000

const route = useRoute()
const router = useRouter()

const stageRef = ref(null)
const canvasRef = ref(null)
const task = ref(null)
const taskLoading = ref(true)
const datasets = ref([])
const helpOpen = ref(false)
const hintVisible = ref(false)

let pollTimer = null
let hintTimer = null
let requestSeq = 0

const viewer = useModelViewer(canvasRef, { onInteract: () => showHint(false) })
// Dev-only hook so browser tests can read camera state without scraping pixels.
if (import.meta.env.DEV) window.__modelViewer = viewer

const taskId = computed(() => String(route.params.taskId || ''))
const projectId = computed(() => String(route.params.id || ''))

const emptyState = computed(() => (taskLoading.value ? null : emptyStateFor(task.value)))

const taskProgress = computed(() => {
  const p = task.value?.node_progress ?? task.value?.progress
  return p == null ? null : Math.round(Number(p))
})

const downloadPercent = computed(() => progressPercent(viewer.state.loaded, viewer.state.total))

const coarsePointer = typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches
const hintText = computed(() => hintFor(viewer.state.mode, coarsePointer))

const statsTitle = computed(() => {
  const { vertices, textures, textureCap } = viewer.state.stats
  const parts = [`${vertices.toLocaleString()} vertices`, `${textures} textures`]
  if (textureCap) parts.push(`textures limited to ${textureCap} px for this device`)
  return parts.join(' · ')
})

const loadingLabel = computed(() => {
  if (taskLoading.value) return 'Loading task…'
  switch (viewer.state.phase) {
    case 'download': return 'Downloading model…'
    case 'extract': return 'Extracting archive…'
    case 'parse':
      return viewer.state.texturesTotal
        ? `Decoding textures ${viewer.state.texturesDone}/${viewer.state.texturesTotal}…`
        : 'Decoding geometry…'
    case 'prepare': return 'Preparing scene…'
    default: return 'Loading…'
  }
})

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

// Other tasks in this project that already have a model, for the switcher.
async function fetchDatasets() {
  try {
    const filters = JSON.stringify([['project', '=', projectId.value], ['model', 'is', 'set']])
    const fields = JSON.stringify(['name', 'title', 'status', 'model'])
    const res = await fetch(`/api/resource/WebODM%20Task?filters=${encodeURIComponent(filters)}&fields=${encodeURIComponent(fields)}&limit_page_length=200`)
    if (!res.ok) return
    const data = await res.json()
    datasets.value = data.data || []
  } catch {
    datasets.value = []
  }
}

// ------------------------------------------------------------- lifecycle

async function loadTask() {
  const seq = ++requestSeq
  stopPolling()
  taskLoading.value = true
  task.value = null
  viewer.clear()

  const [fetched] = await Promise.all([fetchTask(taskId.value), fetchDatasets()])
  if (seq !== requestSeq) return
  task.value = fetched
  taskLoading.value = false

  if (fetched?.model) {
    await loadModel()
  } else if (fetched && PROCESSING_STATUSES.includes(fetched.status)) {
    startPolling()
  }
}

async function loadModel() {
  if (!task.value?.model) return
  await viewer.load(task.value.model)
  if (viewer.state.status === 'ready') showHint(true)
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    const updated = await fetchTask(taskId.value)
    if (!updated || updated.name !== task.value?.name) return
    task.value = updated
    if (updated.model) {
      stopPolling()
      fetchDatasets()
      await loadModel()
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
    return
  }
  event.preventDefault()
  if (action === 'toggleHelp') { helpOpen.value = !helpOpen.value; return }
  if (action === 'toggleFullscreen') { toggleFullscreen(); return }
  if (viewer.state.status === 'ready') act(action)
}

function switchDataset(name) {
  if (!name || name === taskId.value) return
  router.push(`/project/${encodeURIComponent(projectId.value)}/task/${encodeURIComponent(name)}/model`)
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

onMounted(() => {
  viewer.init()
  loadTask()
})

onBeforeUnmount(() => {
  requestSeq++
  stopPolling()
  clearTimeout(hintTimer)
  viewer.dispose()
})
</script>
