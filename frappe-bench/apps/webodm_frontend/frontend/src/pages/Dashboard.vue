<template>
  <div class="p-6 space-y-6 bg-gray-50 dark:bg-gray-900 min-h-full">
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100">Dashboard</h1>
    </div>

    <div v-if="loading" class="text-center py-12">
      <p class="text-gray-500 dark:text-gray-400">Loading stats...</p>
    </div>

    <div v-else-if="error" class="text-center py-12">
      <p class="text-red-600 dark:text-red-400">{{ error }}</p>
    </div>

    <template v-else>
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <div class="p-4 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="p-2 rounded-lg bg-blue-100 dark:bg-blue-900/50">
              <FeatherIcon name="folder" class="h-5 w-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.totalProjects }}</p>
              <p class="text-xs text-gray-500 dark:text-gray-400">Projects</p>
            </div>
          </div>
        </div>

        <div class="p-4 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="p-2 rounded-lg bg-purple-100 dark:bg-purple-900/50">
              <FeatherIcon name="layers" class="h-5 w-5 text-purple-600 dark:text-purple-400" />
            </div>
            <div>
              <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.totalTasks }}</p>
              <p class="text-xs text-gray-500 dark:text-gray-400">Total Tasks</p>
            </div>
          </div>
        </div>

        <div class="p-4 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="p-2 rounded-lg bg-green-100 dark:bg-green-900/50">
              <FeatherIcon name="check-circle" class="h-5 w-5 text-green-600 dark:text-green-400" />
            </div>
            <div>
              <p class="text-2xl font-bold text-green-600 dark:text-green-400">{{ stats.completed }}</p>
              <p class="text-xs text-gray-500 dark:text-gray-400">Completed</p>
            </div>
          </div>
        </div>

        <div class="p-4 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="p-2 rounded-lg bg-red-100 dark:bg-red-900/50">
              <FeatherIcon name="x-circle" class="h-5 w-5 text-red-600 dark:text-red-400" />
            </div>
            <div>
              <p class="text-2xl font-bold text-red-600 dark:text-red-400">{{ stats.failed }}</p>
              <p class="text-xs text-gray-500 dark:text-gray-400">Failed</p>
            </div>
          </div>
        </div>

        <div class="p-4 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="p-2 rounded-lg bg-blue-100 dark:bg-blue-900/50">
              <FeatherIcon name="loader" class="h-5 w-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <p class="text-2xl font-bold text-blue-600 dark:text-blue-400">{{ stats.running }}</p>
              <p class="text-xs text-gray-500 dark:text-gray-400">Running</p>
            </div>
          </div>
        </div>

        <div class="p-4 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm">
          <div class="flex items-center gap-3">
            <div class="p-2 rounded-lg bg-orange-100 dark:bg-orange-900/50">
              <FeatherIcon name="clock" class="h-5 w-5 text-orange-600 dark:text-orange-400" />
            </div>
            <div>
              <p class="text-2xl font-bold text-orange-600 dark:text-orange-400">{{ stats.pending }}</p>
              <p class="text-xs text-gray-500 dark:text-gray-400">Pending</p>
            </div>
          </div>
        </div>
      </div>

      <div v-if="recentProjects.length > 0">
        <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-3">Recent Projects</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          <div v-for="project in recentProjects" :key="project.name" class="p-3 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm cursor-pointer hover:shadow-md transition-shadow" @click="openProject(project.name)">
            <div class="flex items-center gap-3">
              <div class="p-1.5 rounded-lg bg-gray-100 dark:bg-gray-700">
                <FeatherIcon name="folder" class="h-4 w-4 text-gray-500 dark:text-gray-400" />
              </div>
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{{ project.title || project.name }}</p>
                <p class="text-xs text-gray-500 dark:text-gray-400">{{ formatDate(project.creation) }}</p>
              </div>
              <Badge :theme="statusTheme(project.status)" size="sm">{{ project.status }}</Badge>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Badge, FeatherIcon } from 'frappe-ui'

const router = useRouter()
const loading = ref(true)
const error = ref(null)
const projects = ref([])
const tasks = ref([])

const stats = reactive({
  totalProjects: 0,
  totalTasks: 0,
  completed: 0,
  failed: 0,
  running: 0,
  pending: 0,
})

const recentProjects = computed(() =>
  [...projects.value].sort((a, b) => new Date(b.modified || b.creation) - new Date(a.modified || a.creation)).slice(0, 5)
)

const statusTheme = (status) => {
  const themes = { 'Planned': 'blue', 'In Progress': 'orange', 'Completed': 'green', 'Failed': 'red', 'Cancelled': 'gray' }
  return themes[status] || 'gray'
}

const formatDate = (d) => {
  if (!d) return ''
  const date = new Date(d)
  return isNaN(date.getTime()) ? '' : date.toLocaleDateString()
}

function openProject(name) {
  router.push(`/project/${encodeURIComponent(name)}`)
}

onMounted(async () => {
  try {
    const [projRes, taskRes] = await Promise.all([
      fetch('/api/resource/WebODM%20Project?fields=["name","title","status","creation","modified"]'),
      fetch('/api/resource/WebODM%20Task?fields=["status"]&limit_page_length=100000'),
    ])
    if (!projRes.ok) throw new Error(`HTTP ${projRes.status}: ${projRes.statusText}`)
    const projData = await projRes.json()
    projects.value = projData.data || []

    const taskData = taskRes.ok ? await taskRes.json() : { data: [] }
    tasks.value = taskData.data || []

    stats.totalProjects = projects.value.length
    stats.totalTasks = tasks.value.length
    stats.completed = tasks.value.filter(t => t.status === 'Completed').length
    stats.failed = tasks.value.filter(t => t.status === 'Failed').length
    stats.running = tasks.value.filter(t => t.status === 'Running').length
    stats.pending = tasks.value.filter(t => t.status === 'Pending').length
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>
