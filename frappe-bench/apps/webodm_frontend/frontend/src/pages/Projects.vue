<template>
  <div class="p-6 bg-gray-50 dark:bg-gray-900 min-h-full">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100">Projects</h1>
      <Button variant="solid" theme="blue" @click="showNewProject = true">
        <template #prefix>
          <FeatherIcon name="plus" class="h-4 w-4" />
        </template>
        New Project
      </Button>
    </div>

    <div v-if="loading" class="text-center py-12">
      <p class="text-gray-500 dark:text-gray-400">Loading projects...</p>
    </div>

    <div v-else-if="error" class="text-center py-12">
      <p class="text-red-600 dark:text-red-400">{{ error }}</p>
    </div>

    <div v-else-if="projects.length === 0" class="text-center py-12">
      <FeatherIcon name="folder" class="h-12 w-12 mx-auto text-gray-300 dark:text-gray-600 mb-4" />
      <p class="text-gray-500 dark:text-gray-400 text-lg">No projects yet</p>
      <p class="text-gray-400 dark:text-gray-500 text-sm mt-1">Create your first project to get started</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <div v-for="project in projects" :key="project.name" class="p-4 bg-white dark:bg-gray-800 rounded-xl border dark:border-gray-700 shadow-sm cursor-pointer hover:shadow-md transition-shadow" @click="openProject(project.name)">
        <div class="flex items-start justify-between mb-3">
          <h3 class="font-medium text-gray-900 dark:text-gray-100 truncate">{{ project.title || project.name }}</h3>
          <div class="flex items-center gap-1 flex-shrink-0">
            <Badge :theme="statusTheme(project.status)" size="sm">{{ project.status }}</Badge>
            <button
              class="text-gray-400 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 transition-colors p-1"
              @click.stop="openEdit(project)"
              title="Edit project"
            >
              <FeatherIcon name="edit-3" class="h-3.5 w-3.5" />
            </button>
            <button
              class="text-gray-400 dark:text-gray-500 hover:text-red-600 dark:hover:text-red-400 transition-colors p-1"
              @click.stop="confirmDelete(project)"
              title="Delete project"
            >
              <FeatherIcon name="trash-2" class="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <p v-if="project.description" class="text-sm text-gray-500 dark:text-gray-400 line-clamp-2 mb-3">{{ project.description }}</p>
        <div class="flex items-center justify-between">
          <div class="flex items-center text-xs text-gray-400 dark:text-gray-500">
            <FeatherIcon name="calendar" class="h-3 w-3 mr-1" />
            {{ formatDate(project.creation) }}
          </div>
          <div v-if="project._taskStatus" class="flex items-center gap-2 text-xs">
            <span v-if="project._taskStatus.Pending > 0" class="text-orange-500 dark:text-orange-400">{{ project._taskStatus.Pending }} pending</span>
            <span v-if="project._taskStatus.Running > 0" class="text-blue-500 dark:text-blue-400">{{ project._taskStatus.Running }} running</span>
            <span v-if="project._taskStatus.Completed > 0" class="text-green-500 dark:text-green-400">{{ project._taskStatus.Completed }} done</span>
            <span v-if="project._taskStatus.Failed > 0" class="text-red-500 dark:text-red-400">{{ project._taskStatus.Failed }} failed</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="deleteTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="deleteTarget = null">
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-sm mx-4 p-6">
        <h2 class="text-lg font-semibold mb-2 text-gray-900 dark:text-gray-100">Delete Project</h2>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-6">Are you sure you want to delete <strong>{{ deleteTarget.title || deleteTarget.name }}</strong>? This action cannot be undone.</p>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="deleteTarget = null">Cancel</Button>
          <Button variant="solid" theme="red" :loading="deleting" @click="deleteProject">Delete</Button>
        </div>
      </div>
    </div>

    <div v-if="editTarget" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="editTarget = null">
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-gray-100">Edit Project</h2>
        <div class="space-y-4">
          <FormControl label="Title" type="text" v-model="editForm.title" :required="true" />
          <FormControl label="Description" type="textarea" v-model="editForm.description" />
          <FormControl label="Status" type="select" v-model="editForm.status" :options="['Planned', 'In Progress', 'Completed', 'Cancelled']" />
        </div>
        <div class="flex justify-end gap-2 mt-6">
          <Button variant="ghost" @click="editTarget = null">Cancel</Button>
          <Button variant="solid" :loading="editing" @click="updateProject">Save</Button>
        </div>
      </div>
    </div>

    <div v-if="showNewProject" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="showNewProject = false">
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
        <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-gray-100">New Project</h2>
        <div class="space-y-4">
          <FormControl label="Title" type="text" v-model="form.title" :required="true" />
          <FormControl label="Description" type="textarea" v-model="form.description" />
          <FormControl label="Status" type="select" v-model="form.status" :options="['Planned', 'In Progress']" />
        </div>
        <div class="flex justify-end gap-2 mt-6">
          <Button variant="ghost" @click="showNewProject = false">Cancel</Button>
          <Button variant="solid" :loading="saving" @click="createProject">Create</Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { Badge, Button, FeatherIcon, FormControl } from 'frappe-ui'
import { toast } from 'frappe-ui'

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

const statusTheme = (status) => {
  const themes = { 'Planned': 'blue', 'In Progress': 'orange', 'Completed': 'green', 'Failed': 'red', 'Cancelled': 'gray' }
  return themes[status] || 'gray'
}

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
