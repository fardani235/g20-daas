<template>
  <Dialog
    :open="open"
    :title="title"
    :description="description"
    @update:open="v => emit('update:open', v)"
  >
    <div class="space-y-4" data-task-settings>
      <div class="space-y-1.5">
        <Label for="task-settings-title">Title</Label>
        <Input id="task-settings-title" v-model="draftTitle" :disabled="busy" />
      </div>

      <div class="space-y-1.5">
        <Label for="task-settings-preset">Preset</Label>
        <Select id="task-settings-preset" v-model="selectedPreset" :disabled="busy" @change="applyPreset">
          <option :value="null">Keep current options</option>
          <option v-for="p in presets" :key="p.name" :value="p.name">{{ p.preset_name }}</option>
        </Select>
        <p class="text-xs text-muted-foreground">Choosing a preset replaces the options below; you can still adjust them.</p>
      </div>

      <p v-if="odm.error.value" class="text-xs text-destructive">{{ odm.error.value }}</p>
      <p v-else-if="odm.loading.value" class="text-xs text-muted-foreground">Loading options…</p>
      <div v-else class="max-h-64 overflow-y-auto pr-1">
        <OdmOptionsForm :catalog="odm.catalog.value" v-model="values" :field-type="odm.fieldType" />
      </div>

      <Alert v-if="warning" variant="warning" data-task-settings-warning>{{ warning }}</Alert>
    </div>
    <template #footer>
      <Button variant="ghost" :disabled="busy" @click="emit('update:open', false)">Cancel</Button>
      <Button variant="outline" :loading="busy === 'save'" :disabled="!!busy || !draftTitle.trim()" data-task-settings-save @click="save(false)">
        Save
      </Button>
      <Button :loading="busy === 'rerun'" :disabled="!!busy || !draftTitle.trim()" data-task-settings-rerun @click="save(true)">
        <Play />
        {{ rerun }}
      </Button>
    </template>
  </Dialog>
</template>

<script setup>
// Edit a task's title and processing options after it exists, with the same
// preset + option catalog the Add Task dialog uses. "Save" only stores the
// settings; "Save and start / re-run" also calls process_task, which is the
// backend's existing restart path (it wipes the previous outputs first).
import { computed, ref, watch } from 'vue'
import { Play } from 'lucide-vue-next'
import { Alert, Button, Dialog, Input, Label, Select } from '@/components/ui'
import OdmOptionsForm from '@/components/OdmOptionsForm.vue'
import { useOdmOptions } from '@/composables/useOdmOptions'
import { listPresets } from '@/lib/presets'
import { optionsToValues, rerunLabel, rerunWarning, startTask, updateTask, valuesToOptions } from '@/lib/tasks'
import { toast } from '@/lib/toast'

const props = defineProps({
  open: { type: Boolean, required: true },
  task: { type: Object, default: null }, // a task payload (needs name, title, status, options)
  title: { type: String, default: 'Task settings' },
})
const emit = defineEmits(['update:open', 'saved', 'started'])

const odm = useOdmOptions()
const presets = ref([])
const selectedPreset = ref(null)
const draftTitle = ref('')
const values = ref({})
const busy = ref('') // '' | 'save' | 'rerun'

const description = computed(() => props.task
  ? `Change the settings of "${props.task.title || props.task.name}". Saving does not start anything.`
  : '')
const rerun = computed(() => rerunLabel(props.task))
const warning = computed(() => rerunWarning(props.task))

async function load() {
  if (!props.task) return
  draftTitle.value = props.task.title || ''
  selectedPreset.value = null
  values.value = optionsToValues(props.task.options ?? props.task.processing_options)
  try {
    presets.value = await listPresets()
  } catch {
    presets.value = []
  }
  await odm.load()
  odm.seedEnumDefaults(values.value)
}

watch(() => [props.open, props.task?.name], ([isOpen]) => { if (isOpen) load() }, { immediate: true })

function applyPreset() {
  const p = presets.value.find(x => x.name === selectedPreset.value)
  if (!p) return
  values.value = Object.fromEntries((p.options || []).map(o => [o.name, o.value]))
  odm.seedEnumDefaults(values.value)
}

async function save(andRun) {
  if (!props.task) return
  busy.value = andRun ? 'rerun' : 'save'
  try {
    const saved = await updateTask(props.task.name, {
      title: draftTitle.value.trim(),
      options: valuesToOptions(values.value),
    })
    emit('saved', saved)
    if (andRun) {
      const msg = await startTask(props.task.name)
      emit('started', { ...saved, status: 'Queued' })
      toast.success(typeof msg === 'string' ? msg : 'Processing started')
    } else {
      toast.success('Settings saved')
    }
    emit('update:open', false)
  } catch (e) {
    toast.error(e.message || 'Could not save the settings')
  } finally {
    busy.value = ''
  }
}
</script>
