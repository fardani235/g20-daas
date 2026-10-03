<script setup>
// Point cloud controls: colour mode, elevation and classification filters,
// point size, point budget, background. Presentational — it reflects the
// `settings` it is given and emits `update` with a key/value; ModelView.vue
// applies it to the viewer. Only modes/classes that exist in the loaded octree
// are offered (lib/potree.js decides from metadata.json).
import { computed, ref } from 'vue'
import { ChevronDown, ChevronUp, Palette } from 'lucide-vue-next'
import { Button, Select } from '@/components/ui'
import {
  BACKGROUNDS,
  POINT_BUDGETS,
  POINT_SIZE,
  classificationLegend,
  colorModeOptions,
  elevationRangeOf,
  formatElevation,
  isFullElevationRange,
} from '@/lib/potree'

const props = defineProps({
  metadata: { type: Object, default: null },
  settings: { type: Object, required: true },
  // Budget options above this are hidden (device memory / phones).
  budgetCap: { type: Number, default: Infinity },
  stats: { type: Object, default: () => ({ visiblePoints: 0, totalPoints: 0, loadingNodes: 0 }) },
})
const emit = defineEmits(['update'])

const open = ref(true)

const modes = computed(() => colorModeOptions(props.metadata))
const range = computed(() => elevationRangeOf(props.metadata))
const filter = computed(() => props.settings.elevationFilter || range.value)
const classes = computed(() => classificationLegend(props.metadata))
const hidden = computed(() => new Set(props.settings.hiddenClasses || []))
const budgets = computed(() => POINT_BUDGETS.filter(b => b.value <= props.budgetCap))
const sliderStep = computed(() => Math.max((range.value[1] - range.value[0]) / 200, 0.01))
const filterActive = computed(() => !isFullElevationRange(props.settings.elevationFilter, range.value))

function setElevation(which, raw) {
  const v = Number(raw)
  const [lo, hi] = filter.value
  const next = which === 'min' ? [Math.min(v, hi), hi] : [lo, Math.max(v, lo)]
  emit('update', 'elevationFilter', isFullElevationRange(next, range.value) ? null : next)
}

function toggleClass(code) {
  const next = new Set(hidden.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  emit('update', 'hiddenClasses', [...next])
}

function setAllClasses(visible) {
  emit('update', 'hiddenClasses', visible ? [] : classes.value.map(c => c.code))
}
</script>

<template>
  <section
    class="pointer-events-auto w-64 max-w-[calc(100vw-1.5rem)] rounded-lg border border-border bg-card/95 text-xs shadow-sm backdrop-blur"
    data-pointcloud-panel
    aria-label="Point cloud settings"
  >
    <button
      type="button"
      class="flex w-full items-center gap-2 px-3 py-2 text-left font-medium text-foreground"
      :aria-expanded="open"
      @click="open = !open"
    >
      <Palette class="size-3.5 text-muted-foreground" />
      <span class="flex-1">Point cloud</span>
      <span class="font-mono text-[10px] text-muted-foreground" :title="`${stats.visiblePoints.toLocaleString()} of ${stats.totalPoints.toLocaleString()} points drawn`">
        {{ (stats.visiblePoints / 1e6).toFixed(2) }} M pts<template v-if="stats.loadingNodes"> · loading</template>
      </span>
      <ChevronUp v-if="open" class="size-3.5" />
      <ChevronDown v-else class="size-3.5" />
    </button>

    <div v-if="open" class="max-h-[60vh] space-y-3 overflow-y-auto border-t border-border px-3 py-2.5">
      <!-- Colour -->
      <div v-if="modes.length > 1" role="radiogroup" aria-label="Colour by">
        <p class="mb-1 text-muted-foreground">Colour by</p>
        <div class="flex flex-wrap gap-1">
          <Button
            v-for="m in modes"
            :key="m.value"
            size="sm"
            class="h-7 px-2 text-xs"
            :variant="settings.colorMode === m.value ? 'default' : 'outline'"
            role="radio"
            :aria-checked="settings.colorMode === m.value"
            @click="emit('update', 'colorMode', m.value)"
          >
            {{ m.label }}
          </Button>
        </div>
      </div>

      <!-- Elevation filter -->
      <div v-if="range[1] > range[0]" data-elevation-filter>
        <div class="mb-1 flex items-center justify-between text-muted-foreground">
          <span>Elevation</span>
          <button v-if="filterActive" type="button" class="text-primary hover:underline" @click="emit('update', 'elevationFilter', null)">Reset</button>
        </div>
        <label class="flex items-center gap-2">
          <span class="w-8 text-[10px] uppercase text-muted-foreground">Min</span>
          <input
            type="range"
            class="w-full"
            :min="range[0]" :max="range[1]" :step="sliderStep" :value="filter[0]"
            aria-label="Minimum elevation"
            @input="setElevation('min', $event.target.value)"
          />
          <span class="w-16 text-right font-mono text-[10px]">{{ formatElevation(filter[0]) }}</span>
        </label>
        <label class="flex items-center gap-2">
          <span class="w-8 text-[10px] uppercase text-muted-foreground">Max</span>
          <input
            type="range"
            class="w-full"
            :min="range[0]" :max="range[1]" :step="sliderStep" :value="filter[1]"
            aria-label="Maximum elevation"
            @input="setElevation('max', $event.target.value)"
          />
          <span class="w-16 text-right font-mono text-[10px]">{{ formatElevation(filter[1]) }}</span>
        </label>
      </div>

      <!-- Classification filter -->
      <div v-if="classes.length > 1" data-classification-filter>
        <div class="mb-1 flex items-center justify-between text-muted-foreground">
          <span>Classes</span>
          <span class="space-x-2">
            <button type="button" class="text-primary hover:underline" @click="setAllClasses(true)">All</button>
            <button type="button" class="text-primary hover:underline" @click="setAllClasses(false)">None</button>
          </span>
        </div>
        <ul class="space-y-0.5">
          <li v-for="c in classes" :key="c.code">
            <label class="flex cursor-pointer items-center gap-2">
              <input type="checkbox" class="size-3.5 rounded border-border" :checked="!hidden.has(c.code)" @change="toggleClass(c.code)" />
              <span class="inline-block size-3 rounded-sm border border-black/20" :style="{ background: c.color }" />
              <span class="flex-1 truncate">{{ c.label }}</span>
              <span class="font-mono text-[10px] text-muted-foreground">{{ c.code }}</span>
            </label>
          </li>
        </ul>
      </div>

      <!-- Point size -->
      <label class="block">
        <span class="mb-1 flex items-center justify-between text-muted-foreground">
          <span>Point size</span>
          <span class="font-mono text-[10px]">{{ Number(settings.pointSize).toFixed(1) }} px</span>
        </span>
        <input
          type="range"
          class="w-full"
          :min="POINT_SIZE.min" :max="POINT_SIZE.max" :step="POINT_SIZE.step" :value="settings.pointSize"
          aria-label="Point size"
          @input="emit('update', 'pointSize', Number($event.target.value))"
        />
      </label>

      <!-- Budget + background -->
      <div class="grid grid-cols-2 gap-2">
        <label class="block">
          <span class="mb-1 block text-muted-foreground">Point budget</span>
          <Select :model-value="String(settings.pointBudget)" class="h-7 py-0 text-xs" aria-label="Point budget" @update:model-value="v => emit('update', 'pointBudget', Number(v))">
            <option v-for="b in budgets" :key="b.value" :value="String(b.value)">{{ b.label }}</option>
          </Select>
        </label>
        <label class="block">
          <span class="mb-1 block text-muted-foreground">Background</span>
          <Select :model-value="settings.background" class="h-7 py-0 text-xs" aria-label="Background" @update:model-value="v => emit('update', 'background', v)">
            <option v-for="b in BACKGROUNDS" :key="b.value" :value="b.value">{{ b.label }}</option>
          </Select>
        </label>
      </div>
    </div>
  </section>
</template>
