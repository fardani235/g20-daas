<template>
  <div class="space-y-6">
    <div v-if="error" class="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
      {{ error }}
    </div>

    <template v-else-if="dataset">
      <PageHeader :title="dataset.title" :description="dataset.description || ''">
        <template #actions>
          <Button variant="outline" @click="openEdit">
            <Pencil />
            Edit
          </Button>
          <Button
            variant="outline"
            class="text-destructive hover:text-destructive"
            :title="dataset.tasks.length ? 'Still used by a task' : 'Delete dataset'"
            @click="confirmDelete"
          >
            <Trash2 />
            Delete
          </Button>
        </template>
      </PageHeader>

      <div class="grid gap-4 sm:grid-cols-4">
        <div class="rounded-lg border border-border bg-card p-4">
          <p class="text-xs uppercase tracking-wide text-muted-foreground">Images</p>
          <p class="mt-1 text-lg font-medium text-card-foreground">{{ dataset.image_count }}</p>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <p class="text-xs uppercase tracking-wide text-muted-foreground">Size</p>
          <p class="mt-1 text-lg font-medium text-card-foreground">{{ formatBytes(dataset.total_size) }}</p>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <p class="text-xs uppercase tracking-wide text-muted-foreground">Created by</p>
          <p class="mt-1 truncate text-sm font-medium text-card-foreground" :title="dataset.created_by || ''">{{ dataset.created_by || '—' }}</p>
          <p class="text-xs text-muted-foreground">{{ formatDate(dataset.creation) }}</p>
        </div>
        <div class="rounded-lg border border-border bg-card p-4">
          <p class="text-xs uppercase tracking-wide text-muted-foreground">Used by</p>
          <p class="mt-1 text-lg font-medium text-card-foreground">{{ dataset.tasks.length }} task{{ dataset.tasks.length === 1 ? '' : 's' }}</p>
        </div>
      </div>

      <Alert v-if="inUseMessage" variant="warning" data-in-use-alert>{{ inUseMessage }}</Alert>

      <section v-if="dataset.tasks.length" class="rounded-lg border border-border bg-card">
        <h2 class="border-b border-border px-4 py-3 text-sm font-medium text-card-foreground">Tasks using this dataset</h2>
        <ul class="divide-y divide-border text-sm">
          <li v-for="t in dataset.tasks" :key="t.name" class="flex items-center justify-between px-4 py-2" data-task-row>
            <router-link :to="`/project/${t.project}`" class="text-card-foreground hover:underline">{{ t.title || t.name }}</router-link>
            <Badge :variant="statusVariant(t.status)">{{ t.status }}</Badge>
          </li>
        </ul>
      </section>

      <section class="space-y-3">
        <div class="flex items-baseline justify-between">
          <h2 class="text-sm font-medium text-muted-foreground uppercase tracking-wide">Images</h2>
          <p class="text-xs text-muted-foreground">Images are fixed once the dataset exists. To change the inputs, create a new dataset.</p>
        </div>
        <div class="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
          <figure
            v-for="img in dataset.images"
            :key="img.name"
            data-image-tile
            class="overflow-hidden rounded-lg border border-border bg-card"
          >
            <div class="aspect-square bg-muted">
              <img
                :src="thumbnailUrl(img, 256)"
                :alt="img.filename"
                loading="lazy"
                decoding="async"
                class="h-full w-full object-cover"
                @error="e => e.target.style.visibility = 'hidden'"
              />
            </div>
            <figcaption class="space-y-0.5 p-2 text-xs">
              <p class="truncate font-medium text-card-foreground" :title="img.filename">{{ img.filename }}</p>
              <p class="text-muted-foreground">{{ formatBytes(img.file_size) }}</p>
              <p v-if="hasGps(img)" class="truncate text-muted-foreground" :title="`${img.latitude}, ${img.longitude}`">
                {{ Number(img.latitude).toFixed(5) }}, {{ Number(img.longitude).toFixed(5) }}
                <span v-if="img.altitude !== null && img.altitude !== undefined"> · {{ Number(img.altitude).toFixed(0) }} m</span>
              </p>
              <p v-if="img.capture_time" class="text-muted-foreground">{{ img.capture_time }}</p>
            </figcaption>
          </figure>
        </div>
      </section>
    </template>

    <p v-else class="text-sm text-muted-foreground">Loading…</p>

    <Dialog v-model:open="showEdit" title="Edit dataset" description="Only the title and description can change.">
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="edit-title">Title</Label>
          <Input id="edit-title" v-model="draft.title" />
        </div>
        <div class="space-y-1.5">
          <Label for="edit-description">Description</Label>
          <Textarea id="edit-description" v-model="draft.description" :rows="3" />
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="showEdit = false">Cancel</Button>
        <Button :loading="saving" @click="onSave">Save</Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Pencil, Trash2 } from 'lucide-vue-next'
import { Alert, Badge, Button, Dialog, Input, Label, Textarea } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { statusVariant } from '@/lib/status'
import { toast } from '@/lib/toast'
import { deleteDataset, formatBytes, getDataset, imageCountLabel, isInUseError, thumbnailUrl, updateDataset } from '@/lib/datasets'

const route = useRoute()
const router = useRouter()
const dataset = ref(null)
const error = ref('')
const inUseMessage = ref('')
const showEdit = ref(false)
const saving = ref(false)
const draft = reactive({ title: '', description: '' })

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString()
}

function hasGps(img) {
  const lat = Number(img.latitude)
  const lng = Number(img.longitude)
  return Number.isFinite(lat) && Number.isFinite(lng) && !(lat === 0 && lng === 0)
}

async function load() {
  error.value = ''
  try {
    dataset.value = await getDataset(route.params.id)
  } catch (e) {
    dataset.value = null
    error.value = e.message || 'Dataset not found'
  }
}

watch(() => route.params.id, load, { immediate: true })

function openEdit() {
  draft.title = dataset.value.title
  draft.description = dataset.value.description || ''
  showEdit.value = true
}

async function onSave() {
  if (!draft.title.trim()) { toast.error('Title is required'); return }
  saving.value = true
  try {
    await updateDataset(dataset.value.name, { title: draft.title.trim(), description: draft.description })
    toast.success('Dataset updated')
    showEdit.value = false
    await load()
  } catch (e) {
    toast.error(e.message || 'Failed to update dataset')
  } finally {
    saving.value = false
  }
}

function confirmDelete() {
  const d = dataset.value
  if (confirm(`Delete dataset "${d.title}" and its ${imageCountLabel(d.image_count)}?`)) onDelete()
}

async function onDelete() {
  inUseMessage.value = ''
  try {
    await deleteDataset(dataset.value.name)
    toast.success('Dataset deleted')
    router.push('/datasets')
  } catch (e) {
    if (isInUseError(e)) inUseMessage.value = e.message
    else toast.error(e.message || 'Failed to delete dataset')
  }
}
</script>
