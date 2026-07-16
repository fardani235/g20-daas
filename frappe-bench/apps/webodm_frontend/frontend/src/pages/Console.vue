<template>
  <div class="h-full flex flex-col bg-gray-50 dark:bg-gray-900">
    <div class="bg-white dark:bg-gray-900 border-b dark:border-gray-700 px-4 py-2 flex items-center justify-between flex-shrink-0">
      <Button variant="outline" size="sm" @click="$router.back()">&larr; Back</Button>
      <div class="flex items-center gap-3">
        <Badge v-if="task" :theme="statusTheme(task.status)" size="sm">{{ task.status }}</Badge>
        <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">{{ task?.title || task?.name || 'Task Console' }}</h2>
      </div>
      <div class="flex items-center gap-2">
        <span v-if="task?.node_progress != null || task?.progress != null" class="text-sm text-gray-500 dark:text-gray-400">{{ Math.round(task?.node_progress ?? task?.progress) }}%</span>
        <Button variant="ghost" size="sm" @click="refreshLogs">
          <template #prefix><FeatherIcon name="refresh-cw" class="h-3 w-3" /></template>
          Refresh
        </Button>
      </div>
    </div>
    <div class="bg-gray-50 dark:bg-gray-800 border-b dark:border-gray-700 px-4 py-2 flex gap-6 text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">
      <span>Resolution: {{ task?.resolution || 'N/A' }}</span>
      <span>Images: {{ task?.images?.length || 0 }}</span>
    </div>
    <div class="border-b dark:border-gray-700 px-4 py-3 flex gap-3 flex-wrap flex-shrink-0"
         :class="task?.status === 'Completed' ? 'bg-green-50 dark:bg-green-900/30' : 'bg-gray-50 dark:bg-gray-800'">
      <template v-if="task?.status === 'Completed'">
        <a v-if="task.orthophoto" :href="task.orthophoto" download
           class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white border border-green-700 rounded text-xs font-medium hover:bg-green-700">
          <FeatherIcon name="download" class="h-3.5 w-3.5" /> Orthophoto
        </a>
        <a v-if="task.dsm" :href="task.dsm" download
           class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white border border-green-700 rounded text-xs font-medium hover:bg-green-700">
          <FeatherIcon name="download" class="h-3.5 w-3.5" /> DSM
        </a>
        <a v-if="task.dtm" :href="task.dtm" download
           class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white border border-green-700 rounded text-xs font-medium hover:bg-green-700">
          <FeatherIcon name="download" class="h-3.5 w-3.5" /> DTM
        </a>
        <a v-if="task.point_cloud" :href="task.point_cloud" download
           class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-600 text-white border border-green-700 rounded text-xs font-medium hover:bg-green-700">
          <FeatherIcon name="download" class="h-3.5 w-3.5" /> Point Cloud
        </a>
        <a v-if="task.model" :href="task.model" download
           class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-purple-600 text-white border border-purple-700 rounded text-xs font-medium hover:bg-purple-700">
          <FeatherIcon name="box" class="h-3.5 w-3.5" /> 3D Model
        </a>
        <span v-if="!task.orthophoto && !task.dsm && !task.dtm && !task.point_cloud && !task.model" class="text-sm text-text-gray-500 dark:text-gray-400">No artifacts available</span>
      </template>
      <span v-else class="text-sm text-gray-400 dark:text-gray-500">Artifacts appear when processing completes</span>
    </div>
    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">
      Loading task...
    </div>
    <div v-else ref="logEl" class="flex-1 bg-black text-green-400 font-mono text-sm p-4 overflow-y-auto" @scroll="onScroll">
      <div v-for="(line, i) in logs" :key="i" class="whitespace-pre-wrap">{{ line }}</div>
      <div v-if="logs.length === 0" class="text-gray-500 dark:text-gray-400">
        {{ RUNNING_STATUSES.includes(task?.status) ? 'Waiting for processing output...' : 'No console output for this task.' }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { Badge, Button, FeatherIcon } from 'frappe-ui'

const route = useRoute()
const task = ref(null)
const logs = ref([])
const loading = ref(true)
const logEl = ref(null)
let pollTimer = null
let nextLine = 0
let stickToBottom = true

const RUNNING_STATUSES = ['Pending', 'Running', 'Queued']

function csrfHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
  return headers
}

function statusTheme(status) {
  const themes = { completed: 'green', running: 'blue', failed: 'red', queued: 'orange', canceled: 'gray' }
  return themes[status?.toLowerCase()] || 'gray'
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
