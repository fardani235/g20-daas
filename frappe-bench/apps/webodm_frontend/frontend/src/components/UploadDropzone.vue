<template>
  <div
    data-dropzone
    class="rounded-lg border-2 border-dashed p-4 text-center transition-colors"
    :class="[
      dragging ? 'border-primary bg-primary/10' : 'border-border bg-muted/30',
      disabled ? 'pointer-events-none opacity-60' : 'cursor-pointer hover:border-primary/60',
    ]"
    role="button"
    tabindex="0"
    :aria-disabled="disabled"
    @click="openPicker"
    @keydown.enter.prevent="openPicker"
    @keydown.space.prevent="openPicker"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <input
      ref="inputEl"
      type="file"
      class="sr-only"
      :accept="accept"
      :multiple="multiple"
      :disabled="disabled"
      tabindex="-1"
      @change="onPick"
    />
    <input
      v-if="allowFolders"
      ref="folderEl"
      type="file"
      class="sr-only"
      webkitdirectory
      multiple
      :disabled="disabled"
      tabindex="-1"
      @change="onPick"
    />
    <CloudUpload class="mx-auto size-6 text-muted-foreground" />
    <p class="mt-2 text-sm text-foreground">
      <span class="font-medium text-primary">{{ dragging ? 'Drop to add' : 'Drop images here' }}</span>
      <span v-if="!dragging"> or click to choose</span>
    </p>
    <p class="mt-1 text-xs text-muted-foreground">
      {{ hint }}
      <button
        v-if="allowFolders"
        type="button"
        class="ml-1 text-primary hover:underline"
        @click.stop="openFolderPicker"
      >Choose a folder</button>
    </p>
    <div v-if="modelValue.length" class="mt-3 flex items-center justify-between gap-2 text-xs" data-dropzone-summary>
      <span class="text-foreground">
        {{ summary }}
      </span>
      <button type="button" class="text-muted-foreground hover:text-destructive hover:underline" @click.stop="clear">
        Clear
      </button>
    </div>
    <p v-if="ignoredCount" class="mt-1 text-xs text-warning" data-dropzone-ignored>
      {{ ignoredCount }} file{{ ignoredCount === 1 ? '' : 's' }} skipped (not an image).
    </p>
  </div>
</template>

<script setup>
// Drag-and-drop file picker for imagery. Owns no upload logic: it only turns
// drops, picks and folder picks into the `modelValue` file list (merged,
// de-duplicated, filtered to images) and reports a `.txt` dropped alongside
// as a `gcp` event, so one drag of an export folder fills the whole dialog.
import { computed, ref } from 'vue'
import { CloudUpload } from 'lucide-vue-next'
import { filesFromDataTransfer, mergeFiles, partitionDrop, totalBytes } from '@/lib/upload'
import { formatBytes, imageCountLabel } from '@/lib/datasets'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  accept: { type: String, default: 'image/*,.jpg,.jpeg,.png,.tif,.tiff,.dng,.webp' },
  multiple: { type: Boolean, default: true },
  disabled: { type: Boolean, default: false },
  allowFolders: { type: Boolean, default: true },
  hint: { type: String, default: 'JPEG, PNG, TIFF. A gcp_list.txt dropped with the photos is picked up as ground control.' },
})
const emit = defineEmits(['update:modelValue', 'gcp', 'ignored'])

const inputEl = ref(null)
const folderEl = ref(null)
const dragging = ref(false)
const ignoredCount = ref(0)
let dragDepth = 0

const summary = computed(() => `${imageCountLabel(props.modelValue.length)} selected · ${formatBytes(totalBytes(props.modelValue))}`)

function addFiles(files) {
  const { images, gcp, ignored } = partitionDrop(files)
  ignoredCount.value = ignored.length
  if (ignored.length) emit('ignored', ignored)
  if (images.length) {
    emit('update:modelValue', props.multiple ? mergeFiles(props.modelValue, images) : images.slice(0, 1))
  }
  if (gcp) emit('gcp', gcp)
}

function openPicker() {
  if (props.disabled) return
  inputEl.value?.click()
}

function openFolderPicker() {
  if (props.disabled) return
  folderEl.value?.click()
}

function onPick(e) {
  addFiles(Array.from(e.target.files || []))
  e.target.value = '' // so picking the same files again still fires change
}

function onDragEnter() {
  if (props.disabled) return
  dragDepth++
  dragging.value = true
}

function onDragOver(e) {
  if (props.disabled) return
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
}

function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragging.value = false
}

async function onDrop(e) {
  dragDepth = 0
  dragging.value = false
  if (props.disabled) return
  const files = await filesFromDataTransfer(e.dataTransfer)
  addFiles(files)
}

function clear() {
  ignoredCount.value = 0
  emit('update:modelValue', [])
}
</script>
