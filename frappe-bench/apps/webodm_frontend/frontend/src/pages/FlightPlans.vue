<template>
  <div>
    <div class="space-y-6">
      <PageHeader
        :title="projectId ? `Flight plans · ${project?.title || projectId}` : 'Flight plans'"
        description="Plan drone mapping missions for an area or a corridor: flight lines, resolution, flight time and batteries for the aircraft you fly, then export to its flight app."
      >
        <template #actions>
          <Button variant="outline" @click="router.push('/flight-plans/equipment')">
            <Camera />
            Drones and cameras
          </Button>
          <Button data-new-plan @click="newPlan">
            <Plus />
            New flight plan
          </Button>
        </template>
      </PageHeader>

      <p v-if="loading" class="py-12 text-center text-muted-foreground">Loading flight plans…</p>
      <p v-else-if="error" class="py-12 text-center text-destructive">{{ error }}</p>

      <div v-else-if="!plans.length" class="rounded-lg border border-dashed border-border py-16 text-center" data-plans-empty>
        <Route class="mx-auto mb-4 size-12 text-muted-foreground/40" />
        <p class="text-lg text-foreground">No flight plans yet</p>
        <p class="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
          Find your site, draw the area or the corridor to survey, pick your drone and camera, and get the
          flight lines with an estimate of the photos, flight time and batteries.
        </p>
        <Button class="mt-6" @click="newPlan">
          <Plus />
          New flight plan
        </Button>
      </div>

      <div v-else class="overflow-x-auto rounded-lg border border-border bg-card">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-left text-muted-foreground">
              <th class="px-4 py-3 font-medium">Plan</th>
              <th v-if="!projectId" class="px-4 py-3 font-medium">Project</th>
              <th class="px-4 py-3 font-medium">Aircraft</th>
              <th class="px-4 py-3 font-medium" title="Planning estimates">Estimates</th>
              <th class="px-4 py-3 font-medium">Checks</th>
              <th class="px-4 py-3 font-medium">Updated</th>
              <th class="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in plans"
              :key="p.name"
              data-plan-row
              class="border-b border-border transition-colors last:border-0 hover:bg-accent"
            >
              <td class="px-4 py-3">
                <router-link :to="planRoute(p)" class="font-medium text-card-foreground hover:underline">{{ p.title }}</router-link>
                <p class="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Badge variant="outline">{{ p.plan_type === 'corridor' ? 'Corridor' : 'Area' }}</Badge>
                  <span v-if="p.description" class="line-clamp-1">{{ p.description }}</span>
                </p>
              </td>
              <td v-if="!projectId" class="px-4 py-3">
                <router-link :to="`/project/${encodeURIComponent(p.project)}/plans`" class="text-muted-foreground hover:underline">
                  {{ p.project_title }}
                </router-link>
              </td>
              <td class="px-4 py-3 text-muted-foreground">
                {{ p.drone_label }}
                <p class="text-xs">{{ p.camera_label }}</p>
              </td>
              <td class="px-4 py-3 text-muted-foreground">
                {{ formatAreaHa(p.area_m2) }} · {{ formatGsd(p.gsd_cm) }}
                <p class="text-xs">
                  {{ formatCount(p.photo_count) }} photos · {{ formatDuration(p.duration_s) }} ·
                  {{ p.batteries }} batter{{ p.batteries === 1 ? 'y' : 'ies' }}
                </p>
              </td>
              <td class="px-4 py-3">
                <Badge :variant="rowStatus(p).variant">{{ rowStatus(p).label }}</Badge>
              </td>
              <td class="px-4 py-3 text-muted-foreground" :title="p.created_by || ''">{{ formatDate(p.modified) }}</td>
              <td class="whitespace-nowrap px-4 py-3 text-right">
                <Button
                  variant="ghost" size="icon" class="size-8"
                  title="Simulate the flight in 2D and 3D"
                  :disabled="!p.waypoint_count"
                  :data-plan-simulate="p.name"
                  @click="router.push({ path: planRoute(p), query: { simulate: '1' } })"
                >
                  <Play />
                  <span class="sr-only">Simulate {{ p.title }}</span>
                </Button>
                <Button variant="ghost" size="icon" class="size-8" title="Export" @click="exportTarget = p; showExport = true">
                  <FileDown />
                  <span class="sr-only">Export {{ p.title }}</span>
                </Button>
                <Button variant="ghost" size="icon" class="size-8" title="Duplicate or reuse in another project" @click="openDuplicate(p)">
                  <Copy />
                  <span class="sr-only">Duplicate {{ p.title }}</span>
                </Button>
                <Button variant="ghost" size="icon" class="size-8 text-muted-foreground hover:text-destructive" title="Delete" @click="deleteTarget = p">
                  <Trash2 />
                  <span class="sr-only">Delete {{ p.title }}</span>
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="text-xs text-muted-foreground">
        Figures are planning estimates from nominal equipment data, not verified flight-safety information.
      </p>
    </div>

    <Dialog v-model:open="showNew" title="New flight plan" description="A flight plan belongs to a project, next to the surveys processed from it.">
      <div class="space-y-1.5">
        <Label for="new-plan-project">Project</Label>
        <Select id="new-plan-project" v-model="newProject">
          <option :value="null">Choose a project…</option>
          <option v-for="pr in projects" :key="pr.name" :value="pr.name">{{ pr.title || pr.name }}</option>
        </Select>
        <p v-if="!projects.length" class="text-xs text-muted-foreground">
          No projects yet. Create one on the
          <router-link to="/projects" class="text-primary hover:underline">Projects</router-link> page first.
        </p>
      </div>
      <template #footer>
        <Button variant="ghost" @click="showNew = false">Cancel</Button>
        <Button :disabled="!newProject" @click="goNew(newProject)">Start planning</Button>
      </template>
    </Dialog>

    <Dialog
      :open="!!duplicateTarget"
      title="Duplicate flight plan"
      description="The copy is recomputed, so it uses the current drone and camera profiles."
      @update:open="v => { if (!v) duplicateTarget = null }"
    >
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="dup-title">Title</Label>
          <Input id="dup-title" v-model="duplicateForm.title" />
        </div>
        <div class="space-y-1.5">
          <Label for="dup-project">Project</Label>
          <Select id="dup-project" v-model="duplicateForm.project">
            <option v-for="pr in projects" :key="pr.name" :value="pr.name">{{ pr.title || pr.name }}</option>
          </Select>
          <p class="text-xs text-muted-foreground">Choose another project to reuse the same survey shape and settings there.</p>
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="duplicateTarget = null">Cancel</Button>
        <Button :loading="duplicating" @click="doDuplicate">Duplicate</Button>
      </template>
    </Dialog>

    <Dialog
      :open="!!deleteTarget"
      title="Delete flight plan"
      class="sm:max-w-sm"
      @update:open="v => { if (!v) deleteTarget = null }"
    >
      <p class="text-sm text-muted-foreground">
        Delete <strong class="text-foreground">{{ deleteTarget?.title }}</strong>? Files already exported from it are not affected.
      </p>
      <template #footer>
        <Button variant="ghost" @click="deleteTarget = null">Cancel</Button>
        <Button variant="destructive" :loading="deleting" @click="doDelete">Delete</Button>
      </template>
    </Dialog>

    <ExportDialog v-model:open="showExport" :plan="exportTarget" />
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Camera, Copy, FileDown, Play, Plus, Route, Trash2 } from 'lucide-vue-next'
import { Badge, Button, Dialog, Input, Label, Select } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import ExportDialog from '@/components/flightplan/ExportDialog.vue'
import { toast } from '@/lib/toast'
import {
  deletePlan, duplicatePlan, formatAreaHa, formatCount, formatDuration, formatGsd, listPlans, planStatus,
} from '@/lib/flightplan'

const route = useRoute()
const router = useRouter()
// The same page lists one project's plans (/project/:id/plans) or all of them (/flight-plans).
const projectId = computed(() => (route.params.id ? String(route.params.id) : null))

const plans = ref([])
const projects = ref([])
const project = ref(null)
const loading = ref(true)
const error = ref('')
const showNew = ref(false)
const newProject = ref(null)
const showExport = ref(false)
const exportTarget = ref(null)
const duplicateTarget = ref(null)
const duplicateForm = reactive({ title: '', project: null })
const duplicating = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)

const planRoute = p => `/project/${encodeURIComponent(p.project)}/plans/${encodeURIComponent(p.name)}`
const rowStatus = p => planStatus({ error: p.issue_errors, warning: p.issue_warnings })

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleDateString()
}

async function loadProjects() {
  try {
    const res = await fetch('/api/resource/WebODM%20Project?fields=["name","title"]&limit_page_length=1000')
    projects.value = res.ok ? ((await res.json()).data || []) : []
  } catch {
    projects.value = []
  }
  project.value = projects.value.find(p => p.name === projectId.value) || null
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    plans.value = await listPlans(projectId.value || undefined)
  } catch (e) {
    error.value = e.message || 'Failed to load flight plans'
  } finally {
    loading.value = false
  }
}

function goNew(id) {
  showNew.value = false
  router.push(`/project/${encodeURIComponent(id)}/plans/new`)
}

function newPlan() {
  if (projectId.value) { goNew(projectId.value); return }
  newProject.value = projects.value.length === 1 ? projects.value[0].name : null
  showNew.value = true
}

function openDuplicate(p) {
  duplicateForm.title = `${p.title} (copy)`
  duplicateForm.project = p.project
  duplicateTarget.value = p
}

async function doDuplicate() {
  duplicating.value = true
  try {
    const copy = await duplicatePlan(duplicateTarget.value.name, {
      project: duplicateForm.project, title: duplicateForm.title.trim() || undefined,
    })
    toast.success(`Duplicated as "${copy.title}"`)
    duplicateTarget.value = null
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Could not duplicate the plan')
  } finally {
    duplicating.value = false
  }
}

async function doDelete() {
  deleting.value = true
  try {
    await deletePlan(deleteTarget.value.name)
    toast.success('Flight plan deleted')
    deleteTarget.value = null
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Could not delete the plan')
  } finally {
    deleting.value = false
  }
}

watch(projectId, () => { loadProjects(); refresh() }, { immediate: true })
</script>
