<script setup>
import { computed } from 'vue'
import { profilePaths } from '@/lib/flightplan'

// Ground and flight height along the route, from the terrain model the plan
// was computed with. An estimate, and labelled as one.
const props = defineProps({
  terrain: { type: Object, default: null },
  width: { type: Number, default: 340 },
  height: { type: Number, default: 110 },
})

const paths = computed(() => profilePaths(props.terrain?.profile, props.width, props.height))
const lowX = computed(() => {
  const p = paths.value
  const profile = props.terrain?.profile
  if (!p || !profile) return null
  let best = null
  for (const row of profile) if (!best || row[2] - row[1] < best[2] - best[1]) best = row
  return best ? { x: p.x(best[0]), yGround: p.y(best[1]), yFlight: p.y(best[2]) } : null
})
</script>

<template>
  <figure v-if="paths" class="space-y-1" data-terrain-profile>
    <svg
      :viewBox="`0 0 ${width} ${height}`"
      class="w-full rounded border border-border bg-muted/30"
      role="img"
      :aria-label="`Estimated terrain profile along the route: ground from ${Math.round(paths.min)} to ${Math.round(paths.max)} metres above sea level over ${Math.round(paths.length)} metres`"
    >
      <path :d="paths.area" class="fill-emerald-600/25" />
      <path :d="paths.ground" fill="none" class="stroke-emerald-700 dark:stroke-emerald-400" stroke-width="1.5" />
      <path :d="paths.flight" fill="none" stroke="#f59e0b" stroke-width="1.75" />
      <line
        v-if="lowX"
        :x1="lowX.x" :x2="lowX.x" :y1="lowX.yFlight" :y2="lowX.yGround"
        stroke="#dc2626" stroke-width="1.5" stroke-dasharray="3 2"
      />
    </svg>
    <figcaption class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
      <span class="inline-flex items-center gap-1"><span class="inline-block h-0.5 w-3 bg-[#f59e0b]"></span>Flight</span>
      <span class="inline-flex items-center gap-1"><span class="inline-block h-0.5 w-3 bg-emerald-700 dark:bg-emerald-400"></span>Ground (estimate)</span>
      <span class="inline-flex items-center gap-1"><span class="inline-block h-0.5 w-3 bg-[#dc2626]"></span>Lowest clearance</span>
      <span class="ml-auto">{{ Math.round(paths.min) }}–{{ Math.round(paths.max) }} m · {{ (paths.length / 1000).toFixed(1) }} km</span>
    </figcaption>
  </figure>
</template>
