<template>
  <div class="space-y-1.5" data-gcp-field>
    <div class="flex items-center justify-between">
      <Label :for="inputId">Ground control <span class="font-normal text-muted-foreground">(optional)</span></Label>
      <button
        v-if="modelValue"
        type="button"
        class="text-xs text-muted-foreground hover:text-destructive hover:underline"
        :disabled="disabled"
        data-gcp-clear
        @click="emit('update:modelValue', null)"
      >
        Remove
      </button>
    </div>
    <div class="flex items-center gap-2">
      <input
        :id="inputId"
        ref="inputEl"
        type="file"
        accept=".txt,text/plain"
        class="sr-only"
        :disabled="disabled"
        @change="onPick"
      />
      <Button type="button" variant="outline" size="sm" :disabled="disabled" @click="inputEl?.click()">
        <FileText />
        {{ modelValue ? 'Replace file' : 'Choose gcp_list.txt' }}
      </Button>
      <span v-if="modelValue" class="truncate text-sm text-foreground" :title="modelValue.name" data-gcp-name>
        {{ modelValue.name }} <span class="text-muted-foreground">({{ formatBytes(modelValue.size) }})</span>
      </span>
      <span v-else class="text-xs text-muted-foreground">
        For images without GPS, or survey-grade accuracy. ODM's gcp_list format.
      </span>
    </div>
    <p v-if="error" class="text-xs text-destructive" data-gcp-error>{{ error }}</p>
  </div>
</template>

<script setup>
// Single-file picker for a ground control file. Only the name/size are shown
// here; the server parses and validates the content and the dataset page
// shows the parsed summary (points, CRS, image names not in the dataset).
import { ref } from 'vue'
import { FileText } from 'lucide-vue-next'
import { Button, Label } from '@/components/ui'
import { formatBytes } from '@/lib/datasets'
import { isGcpFile } from '@/lib/upload'

defineProps({
  modelValue: { type: Object, default: null },
  disabled: { type: Boolean, default: false },
  inputId: { type: String, default: 'gcp-file' },
})
const emit = defineEmits(['update:modelValue'])

const inputEl = ref(null)
const error = ref('')

function onPick(e) {
  const file = (e.target.files || [])[0] || null
  e.target.value = ''
  if (!file) return
  if (!isGcpFile(file)) {
    error.value = 'A ground control file is a plain-text .txt (ODM gcp_list format).'
    return
  }
  error.value = ''
  emit('update:modelValue', file)
}
</script>
