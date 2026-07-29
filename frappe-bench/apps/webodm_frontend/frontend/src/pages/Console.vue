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
        Resolution: {{ task?.resolution || 'N/A' }} · Images: {{ task?.images?.length || 0 }}
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

const route = useRoute()
const task = ref(null)
const logs = ref([])
const loading = ref(true)
const logEl = ref(null)
let pollTimer = null
let nextLine = 0
let stickToBottom = true

const RUNNING_STATUSES = ['Pending', 'Running', 'Queued']

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
