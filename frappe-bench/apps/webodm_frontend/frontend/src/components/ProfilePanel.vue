<script setup>
// Elevation profile dock (bottom of the 3D viewer). Owns the presentation:
// corridor width / bin step / vertical scale controls, the chart, the stats
// strip, the class legend (toggle layers), the sampling and ground-method
// notes, loading and error states, CSV export. ModelView.vue owns the data:
// it draws the transect in the scene, calls the API and passes the result in.
import { computed, ref, watch } from 'vue'
import { ChartLine, Download, LoaderCircle, RefreshCw, TriangleAlert, X } from 'lucide-vue-next'
import { Button, Select } from '@/components/ui'
import ProfileChart from '@/components/ProfileChart.vue'
import { formatDistance } from '@/lib/format'
import {
  PROFILE_STEPS, PROFILE_WIDTHS, classLayers, csvFilename, groundMethodLabel, profileCsv, samplingNote, statRows,
} from '@/lib/profile'

const props = defineProps({
  profile: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: null },
  settings: { type: Object, required: true }, // { width, step, exaggeration }
  taskName: { type: String, default: '' },
  // Bin index highlighted from the 3D view (or null).
  hoverIndex: { type: Number, default: null },
})
const emit = defineEmits(['update', 'hover', 'select', 'close', 'retry'])

const hidden = ref([])
watch(() => props.profile, () => { hidden.value = [] })

const layers = computed(() => classLayers(props.profile))
const rows = computed(() => statRows(props.profile))
const note = computed(() => samplingNote(props.profile))
const groundNote = computed(() => groundMethodLabel(props.profile))
const title = computed(() => (props.profile ? `Profile · ${formatDistance(props.profile.length)} · ${props.profile.width} m corridor` : 'Elevation profile'))

function toggleClass(code) {
  hidden.value = hidden.value.includes(code) ? hidden.value.filter(c => c !== code) : [...hidden.value, code]
}

function exportCsv() {
  const blob = new Blob([profileCsv(props.profile)], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = csvFilename(props.taskName)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<template>
  <section class="flex h-full min-h-0 flex-col border-t border-border bg-card text-xs" data-profile-panel aria-label="Elevation profile">
    <header class="flex flex-shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-1.5">
      <ChartLine class="size-3.5 text-muted-foreground" />
      <span class="font-medium text-foreground" data-profile-title>{{ title }}</span>

      <label class="flex items-center gap-1 text-muted-foreground">
        Width
        <Select :model-value="String(settings.width)" class="h-6 w-auto py-0 text-xs" aria-label="Corridor width" @update:model-value="v => emit('update', 'width', Number(v))">
          <option v-for="w in PROFILE_WIDTHS" :key="w" :value="String(w)">{{ w }} m</option>
        </Select>
      </label>
      <label class="flex items-center gap-1 text-muted-foreground">
        Step
        <Select :model-value="String(settings.step)" class="h-6 w-auto py-0 text-xs" aria-label="Bin step" @update:model-value="v => emit('update', 'step', Number(v))">
          <option v-for="s in PROFILE_STEPS" :key="s.value" :value="String(s.value)">{{ s.label }}</option>
        </Select>
      </label>
      <label class="flex items-center gap-1 text-muted-foreground">
        Scale
        <Select :model-value="String(settings.exaggeration)" class="h-6 w-auto py-0 text-xs" aria-label="Vertical scale" @update:model-value="v => emit('update', 'exaggeration', v === '1' ? 1 : 'auto')">
          <option value="auto">Fit</option>
          <option value="1">1 : 1</option>
        </Select>
      </label>

      <span class="ml-auto flex items-center gap-1">
        <Button v-if="profile" size="sm" variant="ghost" class="h-6 px-2 text-xs" title="Download the profile as CSV" @click="exportCsv">
          <Download />
          <span class="hidden sm:inline">CSV</span>
        </Button>
        <Button size="icon" variant="ghost" class="h-6 w-6" title="Close the profile" aria-label="Close the profile" @click="emit('close')">
          <X />
        </Button>
      </span>
    </header>

    <div class="relative min-h-0 flex-1">
      <ProfileChart
        v-if="profile"
        :profile="profile"
        :exaggeration="settings.exaggeration"
        :hidden-classes="hidden"
        :hover-index="hoverIndex"
        @hover="i => emit('hover', i)"
        @select="i => emit('select', i)"
      />
      <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-card/70" data-profile-loading>
        <LoaderCircle class="mr-2 size-4 animate-spin text-primary" />
        <span>Reading the point cloud along the line…</span>
      </div>
      <div v-else-if="error" class="absolute inset-0 flex flex-col items-center justify-center gap-2 p-3 text-center" data-profile-error>
        <TriangleAlert class="size-5 text-warning" />
        <p class="text-foreground">{{ error }}</p>
        <Button size="sm" variant="outline" class="h-7" @click="emit('retry')">
          <RefreshCw />
          Retry
        </Button>
      </div>
      <div v-else-if="!profile" class="absolute inset-0 flex items-center justify-center text-muted-foreground">
        Click two or more points on the cloud, then press Enter.
      </div>
    </div>

    <footer v-if="profile" class="flex flex-shrink-0 flex-wrap items-start gap-x-4 gap-y-1 border-t border-border px-3 py-1.5" data-profile-stats>
      <dl class="flex flex-wrap gap-x-4 gap-y-1">
        <div v-for="[label, value, hint] in rows" :key="label" :title="hint" class="min-w-[7rem]">
          <dt class="text-[10px] uppercase tracking-wide text-muted-foreground">{{ label }}</dt>
          <dd class="font-mono text-foreground">{{ value }}</dd>
        </div>
      </dl>
      <ul class="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Layers" data-profile-legend>
        <li class="flex items-center gap-1 text-muted-foreground"><span class="inline-block h-0.5 w-4 bg-[#b45309]" /> ground</li>
        <li class="flex items-center gap-1 text-muted-foreground"><span class="inline-block h-0.5 w-4 bg-[#16a34a]" /> top</li>
        <li v-for="l in layers" :key="l.code">
          <button
            type="button"
            class="flex items-center gap-1 rounded px-1 hover:bg-accent"
            :class="hidden.includes(l.code) ? 'text-muted-foreground line-through' : 'text-foreground'"
            :aria-pressed="!hidden.includes(l.code)"
            :title="`${l.label}: up to ${l.maxHeight ?? '—'} m above ground · ${l.points.toLocaleString()} points. Click to toggle.`"
            @click="toggleClass(l.code)"
          >
            <span class="inline-block size-2.5 rounded-sm" :style="{ background: l.color }" />
            {{ l.label }}<span v-if="l.maxHeight !== null && l.maxHeight !== undefined" class="font-mono text-muted-foreground"> {{ l.maxHeight.toFixed(1) }} m</span>
          </button>
        </li>
      </ul>
      <p class="w-full text-[10px] text-muted-foreground" data-profile-notes>
        {{ groundNote }}<template v-if="profile.classification && !profile.classification.available"> Enable the <code>pc-classify</code> processing option for ground / vegetation / building layers.</template>
        <template v-if="note"> {{ note }}</template>
      </p>
    </footer>
  </section>
</template>
