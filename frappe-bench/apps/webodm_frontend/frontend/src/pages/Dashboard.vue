<template>
  <div class="space-y-6">
    <PageHeader title="Dashboard" description="Processing activity across your organization." />

    <p v-if="loading" class="py-12 text-center text-muted-foreground">Loading stats…</p>

    <Alert v-else-if="error" variant="destructive" :title="error" />

    <template v-else>
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        <div
          v-for="card in statCards"
          :key="card.label"
          class="rounded-lg border border-border bg-card p-4"
        >
          <div class="flex items-center gap-3">
            <div class="rounded-md bg-muted p-2">
              <component :is="card.icon" class="size-5" :class="card.tone" />
            </div>
            <div class="min-w-0">
              <p class="text-2xl font-semibold tracking-tight text-card-foreground">
                {{ card.value }}
              </p>
              <p class="text-xs text-muted-foreground">{{ card.label }}</p>
            </div>
          </div>
        </div>
      </div>

      <section v-if="recentProjects.length">
        <h2 class="mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground">
          Recent projects
        </h2>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          <button
            v-for="project in recentProjects"
            :key="project.name"
            type="button"
            class="rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/40 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            @click="openProject(project.name)"
          >
            <div class="flex items-center gap-3">
              <div class="rounded-md bg-muted p-1.5">
                <Folder class="size-4 text-muted-foreground" />
              </div>
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-card-foreground">
                  {{ project.title || project.name }}
                </p>
                <p class="text-xs text-muted-foreground">{{ formatDate(project.creation) }}</p>
              </div>
              <Badge :variant="statusVariant(project.status)">{{ project.status }}</Badge>
            </div>
          </button>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  CircleCheck,
  CircleX,
  Clock,
  Folder,
  Layers,
  Loader,
} from 'lucide-vue-next'
import { Alert, Badge } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { statusVariant } from '@/lib/status'

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

const statCards = computed(() => [
  { label: 'Projects', value: stats.totalProjects, icon: Folder, tone: 'text-primary' },
  { label: 'Total Tasks', value: stats.totalTasks, icon: Layers, tone: 'text-muted-foreground' },
  { label: 'Completed', value: stats.completed, icon: CircleCheck, tone: 'text-success' },
  { label: 'Failed', value: stats.failed, icon: CircleX, tone: 'text-destructive' },
  { label: 'Running', value: stats.running, icon: Loader, tone: 'text-primary' },
  { label: 'Pending', value: stats.pending, icon: Clock, tone: 'text-warning' },
])

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
