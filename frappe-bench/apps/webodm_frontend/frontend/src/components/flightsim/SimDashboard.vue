<script setup>
import { computed } from 'vue'
import { dashboardTiles } from '@/lib/flightsim/describe'

// The readouts of the simulated flight. Every figure is an estimate from
// the plan; the ones derived from the elevation model or the battery model
// say so in their detail line, and a figure the simulation does not have is
// shown as "not known".
const props = defineProps({
  state: { type: Object, default: null },
  simulation: { type: Object, default: null },
})

const tiles = computed(() => (props.state && props.simulation ? dashboardTiles(props.state, props.simulation) : []))
</script>

<template>
  <dl class="grid grid-cols-2 gap-1.5 sm:grid-cols-3 xl:grid-cols-5" data-sim-dashboard>
    <div
      v-for="t in tiles"
      :key="t.key"
      class="min-w-0 rounded-md border bg-background px-2 py-1.5"
      :class="t.tone === 'warn' ? 'border-warning/60' : 'border-border'"
      :data-sim-tile="t.key"
      :data-tone="t.tone || null"
    >
      <dt class="truncate text-[10px] uppercase tracking-wide text-muted-foreground">{{ t.label }}</dt>
      <dd class="truncate text-sm font-semibold tabular-nums" :class="t.tone === 'warn' ? 'text-warning' : 'text-foreground'">{{ t.value }}</dd>
      <dd class="truncate text-[11px] text-muted-foreground" :title="t.detail">{{ t.detail }}</dd>
      <dd v-if="t.meter !== undefined" class="relative mt-1 h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
        <span
          class="absolute inset-y-0 left-0 rounded-full"
          :class="t.tone === 'warn' ? 'bg-warning' : 'bg-success'"
          :style="{ width: `${Math.max(0, Math.min(1, t.meter)) * 100}%` }"
        ></span>
        <span class="absolute inset-y-0 w-px bg-foreground/70" :style="{ left: `${t.mark * 100}%` }"></span>
      </dd>
    </div>
  </dl>
</template>
