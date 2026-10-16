<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Download } from 'lucide-vue-next'
import { Alert, Badge, Button, Dialog, Select } from '@/components/ui'
import PlanFindings from './PlanFindings.vue'
import { exportPlan, exportVerdict, getExportOptions, saveBlob, supportLevel } from '@/lib/flightplan'
import { toast } from '@/lib/toast'

// Export a saved plan. The list is per aircraft: a format the selected
// drone's flight app cannot read is shown as unavailable with the reason,
// a plan with blocking findings gives no mission file, and warnings have to
// be acknowledged first. The server enforces all three; this dialog only
// shows the verdict it returns.
const props = defineProps({
  open: { type: Boolean, required: true },
  plan: { type: Object, default: null },        // { name, title }
})
const emit = defineEmits(['update:open'])

const loading = ref(false)
const error = ref('')
const data = ref(null)
const selected = ref('')
const triggers = reactive({})
const firmware = ref('px4')
const acknowledged = ref(false)
const downloading = ref(false)

const formats = computed(() => data.value?.formats || [])
const mission = computed(() => formats.value.filter(f => f.kind === 'mission'))
const others = computed(() => formats.value.filter(f => f.kind !== 'mission'))
const current = computed(() => formats.value.find(f => f.id === selected.value) || null)
const verdict = computed(() => (current.value ? exportVerdict(current.value) : null))
// Findings to show for the chosen format: its own, plus the plan's warnings
// that the download asks the user to acknowledge (each listed once).
const detailIssues = computed(() => {
  const f = current.value
  if (!f) return []
  const seen = new Set(f.issues.map(i => i.code + i.message))
  const extra = (f.acknowledge || []).filter(i => !seen.has(i.code + i.message))
  return [...extra, ...f.issues]
})
const canDownload = computed(() => {
  const v = verdict.value
  if (!v || v.state === 'unavailable' || v.state === 'blocked') return false
  return v.state !== 'acknowledge' || acknowledged.value
})

async function load() {
  if (!props.plan?.name) return
  loading.value = true
  error.value = ''
  try {
    data.value = await getExportOptions(props.plan.name, { ...triggers })
    for (const f of data.value.formats) if (f.trigger && !triggers[f.id]) triggers[f.id] = f.trigger
    if (!current.value || !current.value.available) {
      const first = data.value.formats.find(f => f.available && f.kind === 'mission')
        || data.value.formats.find(f => f.available)
      selected.value = first ? first.id : ''
    }
  } catch (e) {
    error.value = e.message || 'Could not load the export options'
  } finally {
    loading.value = false
  }
}

watch(() => props.open, value => {
  if (!value) return
  // Every opening starts from the aircraft's own mission format, not from
  // whatever was picked last time (the plan may have changed in between).
  data.value = null
  selected.value = ''
  acknowledged.value = false
  for (const key of Object.keys(triggers)) delete triggers[key]
  load()
})

watch(selected, () => { acknowledged.value = false })

function onTrigger(id, value) {
  triggers[id] = value
  acknowledged.value = false
  load()
}

async function download() {
  const f = current.value
  if (!f || !canDownload.value) return
  downloading.value = true
  try {
    const out = await exportPlan(props.plan.name, {
      format: f.id,
      trigger: f.kind === 'mission' ? triggers[f.id] : undefined,
      acknowledged: acknowledged.value,
      firmware: f.id === 'qgc-plan' ? firmware.value : undefined,
    })
    saveBlob(out.blob, out.filename)
    toast.success(`Downloaded ${out.filename}`)
  } catch (e) {
    toast.error(e.message || 'Export failed')
  } finally {
    downloading.value = false
  }
}
</script>

<template>
  <Dialog
    :open="open"
    :title="`Export ${plan?.title || 'flight plan'}`"
    description="Formats depend on the aircraft: not every drone's flight app can load a mission file."
    class="max-w-2xl"
    @update:open="emit('update:open', $event)"
  >
    <p v-if="loading && !data" class="py-6 text-center text-sm text-muted-foreground">Checking the plan…</p>
    <Alert v-else-if="error" variant="destructive">{{ error }}</Alert>

    <div v-else-if="data" class="space-y-4" data-export-dialog>
      <fieldset class="space-y-1.5">
        <legend class="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Mission files (flown as planned)</legend>
        <label
          v-for="f in mission"
          :key="f.id"
          class="flex items-start gap-2 rounded-md border px-3 py-2 text-sm"
          :class="[
            f.available ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
            selected === f.id ? 'border-primary bg-primary/5' : 'border-border',
          ]"
          :data-format="f.id"
          :data-available="f.available"
        >
          <input v-model="selected" type="radio" name="export-format" :value="f.id" :disabled="!f.available" class="mt-1" />
          <span class="min-w-0 flex-1">
            <span class="flex flex-wrap items-center gap-2">
              <span class="font-medium text-foreground">{{ f.label }}</span>
              <Badge v-if="f.available" :variant="supportLevel(f.support.level).variant" :title="supportLevel(f.support.level).hint">
                {{ supportLevel(f.support.level).label }}
              </Badge>
              <span v-if="f.available && f.support.app" class="text-xs text-muted-foreground">{{ f.support.app }}</span>
            </span>
            <span class="block text-xs text-muted-foreground">{{ f.available ? f.description : f.reason }}</span>
          </span>
        </label>
      </fieldset>

      <fieldset class="space-y-1.5">
        <legend class="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Boundary and reference files</legend>
        <label
          v-for="f in others"
          :key="f.id"
          class="flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm"
          :class="selected === f.id ? 'border-primary bg-primary/5' : 'border-border'"
          :data-format="f.id"
          :data-available="f.available"
        >
          <input v-model="selected" type="radio" name="export-format" :value="f.id" class="mt-1" />
          <span class="min-w-0 flex-1">
            <span class="flex flex-wrap items-center gap-2">
              <span class="font-medium text-foreground">{{ f.label }}</span>
              <Badge :variant="supportLevel(f.support.level).variant" :title="supportLevel(f.support.level).hint">
                {{ supportLevel(f.support.level).label }}
              </Badge>
              <span v-if="f.support.app" class="text-xs text-muted-foreground">{{ f.support.app }}</span>
            </span>
            <span class="block text-xs text-muted-foreground">{{ f.description }}</span>
          </span>
        </label>
      </fieldset>

      <section v-if="current && current.available" class="space-y-3 rounded-md border border-border p-3" data-export-detail>
        <div v-if="current.kind === 'mission'" class="grid gap-3 sm:grid-cols-2">
          <div v-if="current.trigger_choices?.length" class="space-y-1">
            <label for="export-trigger" class="text-xs text-muted-foreground">Photo trigger</label>
            <Select id="export-trigger" :model-value="triggers[current.id]" @update:model-value="onTrigger(current.id, $event)">
              <option v-for="t in current.trigger_choices" :key="t.id" :value="t.id">{{ t.label }}</option>
            </Select>
          </div>
          <div v-if="current.id === 'qgc-plan'" class="space-y-1">
            <label for="export-firmware" class="text-xs text-muted-foreground">Autopilot</label>
            <Select id="export-firmware" v-model="firmware">
              <option value="px4">PX4</option>
              <option value="ardupilot">ArduPilot</option>
            </Select>
          </div>
          <p class="text-xs text-muted-foreground sm:col-span-2">
            {{ current.waypoints }} waypoint{{ current.waypoints === 1 ? '' : 's' }} in
            {{ current.files }} file{{ current.files === 1 ? '' : 's' }}<template v-if="current.waypoint_cap">
              (this aircraft's app takes up to {{ current.waypoint_cap }} per mission)</template>.
          </p>
        </div>

        <p v-if="current.support.note" class="text-xs text-foreground">{{ current.support.note }}</p>

        <Alert v-if="verdict.state === 'blocked'" variant="destructive" data-export-blocked>
          <p class="font-medium">No mission file can be exported yet</p>
          <ul class="mt-1 list-disc space-y-1 pl-4 text-xs">
            <li v-for="i in current.blocking" :key="i.code + i.message">{{ i.message }}</li>
          </ul>
        </Alert>

        <div v-else>
          <PlanFindings :issues="detailIssues" compact />
        </div>

        <details class="text-xs">
          <summary class="cursor-pointer font-medium text-foreground">How to load this file</summary>
          <ol class="mt-1.5 list-decimal space-y-1 pl-4 text-muted-foreground">
            <li v-for="step in current.instructions" :key="step">{{ step }}</li>
          </ol>
        </details>

        <label
          v-if="verdict.state === 'acknowledge'"
          class="flex cursor-pointer items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-2.5 text-xs text-foreground"
          data-export-acknowledge
        >
          <input v-model="acknowledged" type="checkbox" class="mt-0.5 rounded" />
          <span>
            I have read the warnings above. I understand this is a planning estimate, not verified
            flight-safety information, and that I am responsible for checking the route, the airspace
            and the site before flying.
          </span>
        </label>
      </section>

      <p class="text-[11px] leading-relaxed text-muted-foreground">{{ data.notice }}</p>
    </div>

    <template #footer>
      <Button variant="ghost" @click="emit('update:open', false)">Close</Button>
      <Button :disabled="!canDownload" :loading="downloading" data-export-download @click="download">
        <Download />
        Download
      </Button>
    </template>
  </Dialog>
</template>
