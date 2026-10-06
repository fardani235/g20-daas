<template>
  <div
    v-if="state.phase !== 'idle'"
    data-upload-progress
    :data-phase="state.phase"
    class="rounded-md border px-4 py-3 text-sm"
    :class="toneClass"
    role="status"
    aria-live="polite"
  >
    <div class="flex items-center justify-between gap-3">
      <span class="font-medium">{{ headline }}</span>
      <span v-if="showPercent" class="tabular-nums" data-upload-percent>{{ state.snapshot.percent }}%</span>
    </div>
    <div v-if="showBar" class="mt-2 h-2 overflow-hidden rounded-full bg-muted" aria-hidden="true">
      <div
        class="h-full rounded-full transition-[width] duration-300"
        :class="state.phase === 'processing' ? 'bg-primary/70 animate-pulse' : 'bg-primary'"
        :style="{ width: `${state.snapshot.percent}%` }"
      ></div>
    </div>
    <div class="mt-1 flex items-center justify-between gap-3 text-xs">
      <span class="text-muted-foreground" data-upload-detail>{{ detail }}</span>
      <button
        v-if="cancellable && (state.phase === 'uploading' || state.phase === 'processing')"
        type="button"
        class="text-destructive hover:underline"
        data-upload-cancel
        @click="emit('cancel')"
      >
        Cancel
      </button>
    </div>
  </div>
</template>

<script setup>
// Honest upload feedback. Driven by an upload tracker's `state` (lib/upload):
// bytes sent, average rate and time left while uploading; an indeterminate
// pulse while the server registers the files after the last byte; and a
// terminal line for done / failed / cancelled.
import { computed } from 'vue'
import { UPLOAD_PHASES, progressLabel } from '@/lib/upload'
import { formatBytes } from '@/lib/datasets'

const props = defineProps({
  state: { type: Object, required: true },
  label: { type: String, default: 'Uploading' },
  cancellable: { type: Boolean, default: true },
})
const emit = defineEmits(['cancel'])

const headline = computed(() => {
  switch (props.state.phase) {
    case UPLOAD_PHASES.uploading: return `${props.label}…`
    case UPLOAD_PHASES.processing: return 'Finishing up…'
    case UPLOAD_PHASES.done: return 'Upload complete'
    case UPLOAD_PHASES.failed: return 'Upload failed'
    case UPLOAD_PHASES.cancelled: return 'Upload cancelled'
    default: return ''
  }
})
const detail = computed(() => progressLabel(props.state.phase, props.state.snapshot, { formatBytes }))
const showBar = computed(() => [UPLOAD_PHASES.uploading, UPLOAD_PHASES.processing].includes(props.state.phase))
const showPercent = computed(() => props.state.phase === UPLOAD_PHASES.uploading && props.state.snapshot.total > 0)
const toneClass = computed(() => {
  switch (props.state.phase) {
    case UPLOAD_PHASES.failed: return 'border-destructive/40 bg-destructive/10 text-destructive'
    case UPLOAD_PHASES.cancelled: return 'border-border bg-muted text-muted-foreground'
    case UPLOAD_PHASES.done: return 'border-success/40 bg-success/10 text-foreground'
    default: return 'border-primary/30 bg-primary/10 text-foreground'
  }
})
</script>
