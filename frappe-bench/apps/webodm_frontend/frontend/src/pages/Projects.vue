<template>
  <div class="space-y-6">
    <PageHeader title="Projects" description="Every mapping project in your organization.">
      <template #actions>
        <Button @click="showNewProject = true">
          <Plus />
          New project
        </Button>
      </template>
    </PageHeader>

    <p v-if="loading" class="py-12 text-center text-muted-foreground">Loading projects…</p>

    <p v-else-if="error" class="py-12 text-center text-destructive">{{ error }}</p>

    <div
      v-else-if="projects.length === 0"
      class="rounded-lg border border-dashed border-border py-16 text-center"
    >
      <Folder class="mx-auto mb-4 size-12 text-muted-foreground/40" />
      <p class="text-lg text-foreground">No projects yet</p>
      <p class="mt-1 text-sm text-muted-foreground">
        Create your first project to get started.
      </p>
      <Button class="mt-6" @click="showNewProject = true">
        <Plus />
        New project
      </Button>
    </div>

    <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      <div
        v-for="project in projects"
        :key="project.name"
        class="cursor-pointer rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40"
        @click="openProject(project.name)"
      >
        <div class="mb-3 flex items-start justify-between gap-2">
          <h3 class="truncate font-medium text-card-foreground">
            {{ project.title || project.name }}
          </h3>
          <div class="flex flex-shrink-0 items-center gap-1">
            <Badge :variant="statusVariant(project.status)">{{ project.status }}</Badge>
            <Button
              variant="ghost"
              size="icon"
              class="size-7"
              title="Edit project"
              @click.stop="openEdit(project)"
            >
              <Pencil />
              <span class="sr-only">Edit {{ project.title || project.name }}</span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              class="size-7 text-muted-foreground hover:text-destructive"
              title="Delete project"
              @click.stop="confirmDelete(project)"
            >
              <Trash2 />
              <span class="sr-only">Delete {{ project.title || project.name }}</span>
            </Button>
          </div>
        </div>
        <p v-if="project.description" class="mb-3 line-clamp-2 text-sm text-muted-foreground">
          {{ project.description }}
        </p>
        <div class="flex items-center justify-between text-xs">
          <span class="flex items-center gap-1 text-muted-foreground">
            <Calendar class="size-3" />
            {{ formatDate(project.creation) }}
          </span>
          <span v-if="project._taskStatus" class="flex items-center gap-2">
            <span v-if="project._taskStatus.Pending > 0" class="text-warning">
              {{ project._taskStatus.Pending }} pending
            </span>
            <span v-if="project._taskStatus.Running > 0" class="text-primary">
              {{ project._taskStatus.Running }} running
            </span>
            <span v-if="project._taskStatus.Completed > 0" class="text-success">
              {{ project._taskStatus.Completed }} done
            </span>
            <span v-if="project._taskStatus.Failed > 0" class="text-destructive">
              {{ project._taskStatus.Failed }} failed
            </span>
          </span>
        </div>
      </div>
    </div>

    <Dialog
      :open="!!deleteTarget"
      title="Delete project"
      class="sm:max-w-sm"
      @update:open="value => { if (!value) deleteTarget = null }"
    >
      <p class="text-sm text-muted-foreground">
        Are you sure you want to delete
        <strong class="text-foreground">{{ deleteTarget?.title || deleteTarget?.name }}</strong>?
        This also deletes its tasks and cannot be undone.
      </p>
      <template #footer>
        <Button variant="ghost" @click="deleteTarget = null">Cancel</Button>
        <Button variant="destructive" :loading="deleting" @click="deleteProject">Delete</Button>
      </template>
    </Dialog>

    <Dialog
      :open="!!editTarget"
      title="Edit project"
      @update:open="value => { if (!value) editTarget = null }"
    >
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="edit-title">Title</Label>
          <Input id="edit-title" v-model="editForm.title" required />
        </div>
        <div class="space-y-1.5">
          <Label for="edit-description">Description</Label>
          <Textarea id="edit-description" v-model="editForm.description" />
        </div>
        <div class="space-y-1.5">
          <Label for="edit-status">Status</Label>
          <Select id="edit-status" v-model="editForm.status">
            <option v-for="s in EDIT_STATUSES" :key="s" :value="s">{{ s }}</option>
          </Select>
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="editTarget = null">Cancel</Button>
        <Button :loading="editing" @click="updateProject">Save</Button>
      </template>
    </Dialog>

    <Dialog v-model:open="showNewProject" title="New project">
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="new-title">Title</Label>
          <Input id="new-title" v-model="form.title" required />
        </div>
        <div class="space-y-1.5">
          <Label for="new-description">Description</Label>
          <Textarea id="new-description" v-model="form.description" />
        </div>
        <div class="space-y-1.5">
          <Label for="new-status">Status</Label>
          <Select id="new-status" v-model="form.status">
            <option v-for="s in NEW_STATUSES" :key="s" :value="s">{{ s }}</option>
          </Select>
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="showNewProject = false">Cancel</Button>
        <Button :loading="saving" @click="createProject">Create</Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { Calendar, Folder, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import {
  Badge,
  Button,
  Dialog,
  Input,
  Label,
  Select,
  Textarea,
} from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { statusVariant } from '@/lib/status'
import { toast } from '@/lib/toast'

const router = useRouter()
const projects = ref([])
const loading = ref(true)
const error = ref(null)
const showNewProject = ref(false)
const saving = ref(false)
const form = reactive({ title: '', description: '', status: 'Planned' })

const deleteTarget = ref(null)
const deleting = ref(false)
const editTarget = ref(null)
const editing = ref(false)
const editForm = reactive({ title: '', description: '', status: 'Planned' })

// A new project cannot start out Completed or Cancelled.
const EDIT_STATUSES = ['Planned', 'In Progress', 'Completed', 'Cancelled']
const NEW_STATUSES = ['Planned', 'In Progress']

const formatDate = (d) => {
  if (!d) return ''
  const date = new Date(d)
  return isNaN(date.getTime()) ? '' : date.toLocaleDateString()
}

function confirmDelete(project) {
  deleteTarget.value = project
}

async function deleteProject() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    const deleteFilters = JSON.stringify([["project", "=", deleteTarget.value.name]])
    const tasksRes = await fetch(
      `/api/resource/WebODM%20Task?filters=${encodeURIComponent(deleteFilters)}&fields=["name"]`
    )
    if (tasksRes.ok) {
      const { data: taskList } = await tasksRes.json()
      for (const task of taskList || []) {
        await fetch(`/api/resource/WebODM%20Task/${encodeURIComponent(task.name)}`, {
          method: 'DELETE',
          headers: csrfHeaders(),
        })
      }
    }
    const res = await fetch(`/api/resource/WebODM%20Project/${encodeURIComponent(deleteTarget.value.name)}`, {
      method: 'DELETE',
      headers: csrfHeaders(),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.message || 'Failed to delete project')
    }
    projects.value = projects.value.filter(p => p.name !== deleteTarget.value.name)
    toast.success('Project deleted')
    deleteTarget.value = null
  } catch (e) {
    toast.error(e.message)
  } finally {
    deleting.value = false
  }
}

function openEdit(project) {
  editForm.title = project.title || ''
  editForm.description = project.description || ''
  editForm.status = project.status || 'Planned'
  editTarget.value = project
}

async function updateProject() {
  if (!editTarget.value) return
  editing.value = true
  try {
    const res = await fetch(`/api/resource/WebODM%20Project/${encodeURIComponent(editTarget.value.name)}`, {
      method: 'PUT',
      headers: csrfHeaders(),
      body: JSON.stringify({
        title: editForm.title,
        description: editForm.description,
        status: editForm.status,
      }),
    })
    if (!res.ok) throw new Error('Failed to update project')
    const { data } = await res.json()
    const idx = projects.value.findIndex(p => p.name === editTarget.value.name)
    if (idx !== -1) projects.value[idx] = data
    toast.success('Project updated')
    editTarget.value = null
  } catch (e) {
    toast.error(e.message)
  } finally {
    editing.value = false
  }
}

onMounted(async () => {
  try {
    const [projRes, taskRes] = await Promise.all([
      fetch('/api/resource/WebODM%20Project?fields=["*"]'),
      fetch('/api/resource/WebODM%20Task?fields=["project","status"]&limit_page_length=10000'),
    ])
    if (!projRes.ok) throw new Error(`HTTP ${projRes.status}: ${projRes.statusText}`)
    const projData = await projRes.json()
    projects.value = projData.data || []

    const taskData = taskRes.ok ? await taskRes.json() : { data: [] }
    const statusMap = {}
    for (const t of taskData.data || []) {
      if (!statusMap[t.project]) statusMap[t.project] = {}
      statusMap[t.project][t.status] = (statusMap[t.project][t.status] || 0) + 1
    }
    for (const p of projects.value) {
      p._taskStatus = statusMap[p.name] || {}
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

function csrfHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
  return headers
}

async function createProject() {
  saving.value = true
  try {
    const res = await fetch('/api/resource/WebODM%20Project', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ title: form.title, description: form.description, status: form.status }),
    })
    if (!res.ok) throw new Error('Failed to create project')
    const { data } = await res.json()
    projects.value.push(data)
    showNewProject.value = false
    form.title = ''; form.description = ''; form.status = 'Planned'
    toast.success('Project created')
  } catch (e) {
    toast.error(e.message)
  } finally {
    saving.value = false
  }
}

function openProject(name) {
  router.push(`/project/${encodeURIComponent(name)}`)
}
</script>
