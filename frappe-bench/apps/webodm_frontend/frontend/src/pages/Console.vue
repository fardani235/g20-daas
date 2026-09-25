<template>
  <div class="flex h-full flex-col">
    <div class="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-border px-4 py-3">
      <h2 class="text-base font-medium text-foreground">
        {{ task?.title || task?.name || 'Task console' }}
      </h2>
      <Badge v-if="task" :variant="statusVariant(task.status)">{{ task.status }}</Badge>
      <span
        v-if="task?.node_progress != null || task?.progress != null"
        class="text-sm text-muted-foreground"
      >
        {{ Math.round(task?.node_progress ?? task?.progress) }}%
      </span>
      <span class="text-xs text-muted-foreground">
        Resolution: {{ resolutionText }} · Images: {{ task?.images?.length || 0 }}
      </span>
      <Button variant="outline" size="sm" class="ml-auto" @click="refreshLogs">
        <RefreshCw />
        Refresh
      </Button>
    </div>

    <div class="flex flex-shrink-0 flex-wrap gap-2 border-b border-border px-4 py-3">
      <template v-if="artifacts.length">
        <a
          v-for="artifact in artifacts"
          :key="artifact.label"
          :href="artifact.href"
          download
          class="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent"
        >
          <component :is="artifact.icon" class="size-3.5" />
          {{ artifact.label }}
        </a>
      </template>
      <span v-else-if="task?.status === 'Completed'" class="text-sm text-muted-foreground">
        No artifacts available
      </span>
      <span v-else class="text-sm text-muted-foreground">
        Artifacts appear when processing completes
      </span>
    </div>

    <!-- Raster metadata: one column per output raster, from the header-only
         extraction done when the outputs landed. Collapsed by default so the
         console stays the focus. -->
    <details
      v-if="rasterCards.length"
      class="flex-shrink-0 border-b border-border px-4 py-2 text-sm"
      :open="rasterDetailsOpen"
      @toggle="rasterDetailsOpen = $event.target.open"
    >
      <summary class="cursor-pointer select-none text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Raster metadata
        <span v-if="rasterMetaLoading" class="ml-2 normal-case tracking-normal">(loading…)</span>
      </summary>
      <div class="mt-2 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="card in rasterCards" :key="card.key" :data-raster="card.key" class="rounded-md border border-border p-3">
          <div class="mb-1.5 flex items-center justify-between gap-2">
            <span class="font-medium text-foreground">{{ card.label }}</span>
            <Badge v-if="card.meta.status === 'Failed'" variant="destructive">Unavailable</Badge>
            <span v-else class="text-xs text-muted-foreground">{{ card.meta.dtype }}</span>
          </div>
          <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            <template v-for="row in card.rows" :key="row.label">
              <dt class="text-muted-foreground">{{ row.label }}</dt>
              <dd class="break-words text-foreground">{{ row.value }}</dd>
            </template>
          </dl>
          <Button
            v-if="card.meta.status === 'Failed'"
            variant="outline"
            size="sm"
            class="mt-2"
            @click="retryRasterMetadata(card.key)"
          >
            <RefreshCw />
            Retry extraction
          </Button>
        </div>
      </div>
    </details>

    <p v-if="loading" class="flex flex-1 items-center justify-center text-muted-foreground">
      Loading task…
    </p>
    <div
      v-else
      ref="logEl"
      class="flex-1 overflow-y-auto bg-slate-950 p-4 font-mono text-sm text-emerald-400"
      @scroll="onScroll"
    >
      <div v-for="(line, i) in logs" :key="i" class="whitespace-pre-wrap">{{ line }}</div>
      <p v-if="logs.length === 0" class="text-slate-500">
        {{ RUNNING_STATUSES.includes(task?.status) ? 'Waiting for processing output…' : 'No console output for this task.' }}
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { Box, Download, RefreshCw } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import { statusVariant } from '@/lib/status'
import { toast } from '@/lib/toast'
import {
  RASTER_DATASETS,
  getRasterMetadata,
  detailRows,
  gsdMetres,
} from '@/lib/rasterMetadata'

const route = useRoute()
const task = ref(null)
const logs = ref([])
const loading = ref(true)
const logEl = ref(null)
let pollTimer = null
let nextLine = 0
let stickToBottom = true

const RUNNING_STATUSES = ['Pending', 'Running', 'Queued', 'Provisioning']
const RASTER_LABELS = { orthophoto: 'Orthophoto', dsm: 'DSM', dtm: 'DTM' }

// Header metadata per raster, keyed by dataset; null when the task lacks it.
const rasterMeta = ref({})
const rasterMetaLoading = ref(false)
const rasterDetailsOpen = ref(false)

const rasterCards = computed(() =>
  RASTER_DATASETS
    .filter(key => rasterMeta.value[key])
    .map(key => ({ key, label: RASTER_LABELS[key], meta: rasterMeta.value[key], rows: detailRows(rasterMeta.value[key]) })))

// The task's own resolution (cm/px, set from the ODM options or the ortho GSD)
// or, failing that, the orthophoto's GSD read straight from the metadata.
const resolutionText = computed(() => {
  const r = Number(task.value?.resolution)
  if (Number.isFinite(r) && r > 0) return `${r} cm/px`
  const gsd = gsdMetres(rasterMeta.value.orthophoto)
  if (gsd !== null) return `${(gsd * 100).toFixed(2)} cm/px`
  return 'N/A'
})

async function fetchRasterMetadata({ refresh = false, dataset } = {}) {
  const t = task.value
  if (!t || t.status !== 'Completed') return
  rasterMetaLoading.value = true
  try {
    const out = await getRasterMetadata(t.name, { refresh, dataset })
    if (dataset) rasterMeta.value = { ...rasterMeta.value, [dataset]: out }
    else rasterMeta.value = out || {}
  } catch {
    // Best-effort: the console is usable without it.
  } finally {
    rasterMetaLoading.value = false
  }
}

async function retryRasterMetadata(key) {
  await fetchRasterMetadata({ refresh: true, dataset: key })
  if (rasterMeta.value[key]?.status === 'Failed') toast.error('Metadata could not be read; see the error log')
  else toast.success('Metadata refreshed')
}

// The five artifact links were five near-identical markup blocks; this drives
// them from data instead.
const artifacts = computed(() => {
  const t = task.value
  if (!t || t.status !== 'Completed') return []
  return [
    { label: 'Orthophoto', href: t.orthophoto, icon: Download },
    { label: 'DSM', href: t.dsm, icon: Download },
    { label: 'DTM', href: t.dtm, icon: Download },
    { label: 'Point Cloud', href: t.point_cloud, icon: Download },
    { label: '3D Model', href: t.model, icon: Box },
  ].filter(a => a.href)
})

function csrfHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
  return headers
}

// Keep the view pinned to the newest line unless the user has scrolled up.
function onScroll() {
  const el = logEl.value
  if (!el) return
  stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

async function scrollToBottom() {
  await nextTick()
  const el = logEl.value
  if (el && stickToBottom) el.scrollTop = el.scrollHeight
}

async function fetchTask() {
  try {
    const res = await fetch('/api/method/webodm_core.api.task.get_task_progress', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ task_name: route.params.taskId }),
    })
    if (!res.ok) return
    const { message } = await res.json()
    if (message) task.value = message
  } catch {}
}

// Pull only the new console lines produced by NodeODM since our last offset.
async function fetchConsole() {
  try {
    const res = await fetch('/api/method/webodm_core.api.task.get_task_console', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ task_name: route.params.taskId, line: nextLine }),
    })
    if (!res.ok) return
    const { message } = await res.json()
    if (!message) return
    if (Array.isArray(message.lines) && message.lines.length) {
      logs.value.push(...message.lines)
      await scrollToBottom()
    }
    if (typeof message.next_line === 'number') nextLine = message.next_line
  } catch {}
}

function startPolling() {
  pollTimer = setInterval(async () => {
    await fetchTask()
    await fetchConsole()
    if (!RUNNING_STATUSES.includes(task.value?.status)) {
      clearInterval(pollTimer)
      pollTimer = null
      // Outputs just landed: their metadata was extracted alongside them.
      await fetchRasterMetadata()
    }
  }, 5000)
}

// Manual refresh re-reads the full console from the start.
async function refreshLogs() {
  await fetchTask()
  nextLine = 0
  logs.value = []
  stickToBottom = true
  await fetchConsole()
}

onMounted(async () => {
  loading.value = true
  await fetchTask()
  await fetchConsole()
  loading.value = false
  fetchRasterMetadata()

  if (RUNNING_STATUSES.includes(task.value?.status)) {
    startPolling()
  }
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>
