<template>
  <div class="space-y-6">
    <PageHeader
      title="Datasets"
      description="Reusable image sets for your organization. A task points at a dataset, so the same photos can be processed again without re-uploading them."
    >
      <template #actions>
        <Button @click="openCreate">
          <CloudUpload />
          New dataset
        </Button>
      </template>
    </PageHeader>

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Title</th>
            <th class="px-4 py-3 font-medium">Images</th>
            <th class="px-4 py-3 font-medium">Size</th>
            <th class="px-4 py-3 font-medium">Used by</th>
            <th class="px-4 py-3 font-medium">Created</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="d in datasets"
            :key="d.name"
            data-dataset-row
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3">
              <router-link :to="`/datasets/${d.name}`" class="font-medium text-card-foreground hover:underline">
                {{ d.title }}
              </router-link>
              <p v-if="d.description" class="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{{ d.description }}</p>
            </td>
            <td class="px-4 py-3 text-muted-foreground">
              {{ imageCountLabel(d.image_count) }}
              <span v-if="d.gcp" class="ml-1 inline-flex items-center gap-0.5 text-xs" :title="gcpLabel(d.gcp)" data-dataset-gcp>
                <MapPin class="size-3" /> GCP
              </span>
            </td>
            <td class="px-4 py-3 text-muted-foreground">{{ formatBytes(d.total_size) }}</td>
            <td class="px-4 py-3">
              <Badge :variant="d.task_count ? 'default' : 'secondary'">
                {{ d.task_count ? `${d.task_count} task${d.task_count === 1 ? '' : 's'}` : 'unused' }}
              </Badge>
            </td>
            <td class="px-4 py-3 text-muted-foreground" :title="d.created_by || ''">{{ formatDate(d.creation) }}</td>
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" class="size-8" title="Open dataset" @click="router.push(`/datasets/${d.name}`)">
                <Images />
                <span class="sr-only">Open {{ d.title }}</span>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                class="size-8 text-muted-foreground hover:text-destructive"
                :title="d.task_count ? 'Still used by a task' : 'Delete dataset'"
                @click="confirmDelete(d)"
              >
                <Trash2 />
                <span class="sr-only">Delete {{ d.title }}</span>
              </Button>
            </td>
          </tr>
          <tr v-if="!loading && !datasets.length">
            <td colspan="6" class="px-4 py-10 text-center text-muted-foreground">
              No datasets yet. Upload images to create one, or add a task from a project — its uploads become a dataset.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Alert v-if="inUseMessage" variant="warning" data-in-use-alert>
      {{ inUseMessage }}
    </Alert>

    <Dialog v-model:open="showCreate" title="New dataset" description="Upload the images once; any number of tasks can process them.">
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="dataset-title">Title</Label>
          <Input id="dataset-title" v-model="draft.title" placeholder="e.g. North field, 30 Sept flight" />
        </div>
        <div class="space-y-1.5">
          <Label for="dataset-description">Description</Label>
          <Textarea id="dataset-description" v-model="draft.description" :rows="2" />
        </div>
        <div class="space-y-1.5">
          <Label>Images</Label>
          <UploadDropzone v-model="draft.files" :disabled="uploading" @gcp="onDroppedGcp" />
          <p class="text-xs text-muted-foreground">Images are fixed once the dataset exists; ground control can be changed later.</p>
        </div>
        <GcpFileField v-model="draft.gcp" :disabled="uploading" input-id="dataset-gcp" />
        <UploadProgress
          :state="uploadTracker.state"
          :label="`Uploading ${imageCountLabel(draft.files.length)}`"
          @cancel="uploadTracker.cancel()"
        />
      </div>
      <template #footer>
        <Button variant="ghost" :disabled="uploading" @click="showCreate = false">Cancel</Button>
        <Button :disabled="!draft.files.length || uploading" :loading="uploading" @click="onCreate">Create dataset</Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { CloudUpload, Images, MapPin, Trash2 } from 'lucide-vue-next'
import { Alert, Badge, Button, Dialog, Input, Label, Textarea } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import UploadDropzone from '@/components/UploadDropzone.vue'
import UploadProgress from '@/components/UploadProgress.vue'
import GcpFileField from '@/components/GcpFileField.vue'
import { toast } from '@/lib/toast'
import { createUploadTracker, isCancelled, totalBytes } from '@/lib/upload'
import {
  createDataset,
  deleteDataset,
  formatBytes,
  gcpLabel,
  imageCountLabel,
  isInUseError,
  listDatasets,
  sortDatasets,
} from '@/lib/datasets'

const router = useRouter()
const datasets = ref([])
const loading = ref(true)
const showCreate = ref(false)
const uploading = ref(false)
const uploadTracker = createUploadTracker(undefined, reactive)
const inUseMessage = ref('')
const draft = reactive({ title: '', description: '', files: [], gcp: null })

function formatDate(value) {
  if (!value) return '—'
  const d = new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleDateString()
}

async function refresh() {
  loading.value = true
  try {
    datasets.value = sortDatasets(await listDatasets())
  } catch (e) {
    toast.error(e.message || 'Failed to load datasets')
  } finally {
    loading.value = false
  }
}

refresh()

function openCreate() {
  draft.title = ''
  draft.description = ''
  draft.files = []
  draft.gcp = null
  uploadTracker.reset()
  showCreate.value = true
}

function onDroppedGcp(file) {
  if (!draft.gcp) draft.gcp = file
}

async function onCreate() {
  if (!draft.files.length) { toast.error('Choose at least one image'); return }
  uploading.value = true
  const signal = uploadTracker.start(totalBytes(draft.files) + (draft.gcp ? draft.gcp.size : 0))
  try {
    const created = await createDataset({
      files: draft.files,
      title: draft.title || undefined,
      description: draft.description || undefined,
      gcp: draft.gcp || undefined,
      onProgress: uploadTracker.update,
      signal,
    })
    uploadTracker.finish()
    toast.success(`Dataset "${created.title}" created`)
    showCreate.value = false
    await refresh()
  } catch (e) {
    if (isCancelled(e)) {
      toast.info('Upload cancelled — nothing was created')
    } else {
      uploadTracker.fail()
      toast.error(e.message || 'Upload failed')
    }
  } finally {
    uploading.value = false
    if (!showCreate.value) uploadTracker.reset()
  }
}

function confirmDelete(d) {
  if (confirm(`Delete dataset "${d.title}" and its ${imageCountLabel(d.image_count)}?`)) onDelete(d)
}

async function onDelete(d) {
  inUseMessage.value = ''
  try {
    await deleteDataset(d.name)
    toast.success('Dataset deleted')
    await refresh()
  } catch (e) {
    // The backend refuses while a task references the dataset and names the
    // task(s); that is guidance for the user, not a failure of the page.
    if (isInUseError(e)) inUseMessage.value = e.message
    else toast.error(e.message || 'Failed to delete dataset')
  }
}
</script>
