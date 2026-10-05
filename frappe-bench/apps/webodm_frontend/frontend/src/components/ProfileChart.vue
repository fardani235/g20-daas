<script setup>
// The elevation profile chart: plain SVG built from lib/profile.js paths
// (no chart library — the viewer already carries three.js and the data is
// a few hundred bins). Draws the min–max envelope, the top line, the ground
// (solid where measured, dashed where interpolated), the space between them,
// one band per classification layer, hatched gap columns, the polyline's
// vertices and a hover crosshair with a readout. Emits `hover` (bin index or
// null) and `select` (bin index) so the 3D view can follow the pointer.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { formatDistance } from '@/lib/format'
import {
  bandPath, binAt, classLayers, fmtM, groundSeries, hoverInfo, linePath, makeScales, ticks,
} from '@/lib/profile'

const props = defineProps({
  profile: { type: Object, required: true },
  // 'auto' fills the plot; 1 keeps the true shape (same scale on both axes).
  exaggeration: { type: [String, Number], default: 'auto' },
  // Classification codes currently hidden in the chart.
  hiddenClasses: { type: Array, default: () => [] },
  // Externally driven hover (e.g. from the 3D view); null = none.
  hoverIndex: { type: Number, default: null },
})
const emit = defineEmits(['hover', 'select'])

const MARGIN = { top: 10, right: 14, bottom: 24, left: 52 }
const COLORS = { ground: '#b45309', top: '#16a34a', fill: 'rgba(56, 189, 248, 0.16)', envelope: 'rgba(148, 163, 184, 0.22)', gap: 'rgba(148, 163, 184, 0.35)' }

const svgRef = ref(null)
const size = ref({ width: 640, height: 240 })
const localHover = ref(null)
let observer = null

onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(measure)
    observer.observe(svgRef.value.parentElement)
  }
})
onBeforeUnmount(() => observer?.disconnect())

function measure() {
  const el = svgRef.value?.parentElement
  if (!el) return
  const w = el.clientWidth, h = el.clientHeight
  if (w > 0 && h > 0) size.value = { width: w, height: h }
}

const scales = computed(() => makeScales(props.profile, { ...size.value, margin: MARGIN, exaggeration: props.exaggeration }))
const dist = computed(() => props.profile.distance || [])
const ground = computed(() => groundSeries(props.profile))
const layers = computed(() => classLayers(props.profile).filter(l => !props.hiddenClasses.includes(l.code)))

const paths = computed(() => {
  const s = scales.value, p = props.profile
  const groundAny = ground.value.measured.map((g, i) => (g ?? ground.value.filled[i]))
  return {
    envelope: bandPath(dist.value, p.min, p.max, s),
    between: bandPath(dist.value, groundAny, p.top, s),
    top: linePath(dist.value, p.top, s),
    groundMeasured: linePath(dist.value, ground.value.measured, s),
    groundFilled: linePath(dist.value, ground.value.filled, s),
    layers: layers.value.map(l => ({ ...l, band: bandPath(dist.value, l.layer.min, l.layer.max, s), line: linePath(dist.value, l.layer.max, s) })),
  }
})

const gapRects = computed(() => (props.profile.gaps || []).map(([a, b]) => {
  const x0 = scales.value.x(a), x1 = scales.value.x(b)
  return { x: x0, width: Math.max(x1 - x0, 1) }
}))

const xTicks = computed(() => ticks(0, props.profile.length, Math.max(2, Math.floor(scales.value.usedW / 90))))
const yTicks = computed(() => ticks(scales.value.zLo, scales.value.zHi, Math.max(2, Math.floor(scales.value.plotH / 40))))
const vertices = computed(() => (props.profile.vertices || []).slice(1, -1).map(v => ({ x: scales.value.x(v.distance), distance: v.distance })))

const activeIndex = computed(() => (localHover.value ?? props.hoverIndex))
const hover = computed(() => (activeIndex.value === null || activeIndex.value === undefined ? null : hoverInfo(props.profile, activeIndex.value)))
const hoverX = computed(() => (hover.value ? scales.value.x(hover.value.distance) : 0))
const tooltipLeft = computed(() => {
  // Keep the readout inside the chart: flip to the left of the cursor near the right edge.
  const w = 190
  return hoverX.value + 12 + w > size.value.width ? hoverX.value - 12 - w : hoverX.value + 12
})

function indexFromEvent(event) {
  const rect = svgRef.value.getBoundingClientRect()
  const px = event.clientX - rect.left
  return binAt(props.profile, scales.value.invertX(px))
}

function onMove(event) {
  const i = indexFromEvent(event)
  const next = i >= 0 ? i : null
  if (next !== localHover.value) {
    localHover.value = next
    emit('hover', next)
  }
}

function onLeave() {
  if (localHover.value !== null) {
    localHover.value = null
    emit('hover', null)
  }
}

function onClick(event) {
  const i = indexFromEvent(event)
  if (i >= 0) emit('select', i)
}

watch(() => props.profile, () => { localHover.value = null })
</script>

<template>
  <div class="relative h-full w-full select-none" data-profile-chart>
    <svg
      ref="svgRef"
      class="block h-full w-full cursor-crosshair"
      :viewBox="`0 0 ${size.width} ${size.height}`"
      role="img"
      :aria-label="`Elevation profile, ${formatDistance(profile.length)} long`"
      @pointermove="onMove"
      @pointerleave="onLeave"
      @click="onClick"
    >
      <defs>
        <pattern id="profile-gap-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" :stroke="COLORS.gap" stroke-width="2" />
        </pattern>
        <clipPath id="profile-plot-clip">
          <rect :x="scales.margin.left" :y="scales.margin.top" :width="scales.plotW" :height="scales.plotH" />
        </clipPath>
      </defs>

      <!-- Grid + axes -->
      <g class="text-[10px] text-muted-foreground" fill="currentColor">
        <g v-for="t in yTicks" :key="`y${t}`">
          <line :x1="scales.margin.left" :x2="scales.margin.left + scales.plotW" :y1="scales.y(t)" :y2="scales.y(t)" stroke="currentColor" stroke-opacity="0.15" />
          <text :x="scales.margin.left - 6" :y="scales.y(t) + 3" text-anchor="end">{{ t }}</text>
        </g>
        <g v-for="t in xTicks" :key="`x${t}`">
          <line :x1="scales.x(t)" :x2="scales.x(t)" :y1="scales.margin.top" :y2="scales.margin.top + scales.plotH" stroke="currentColor" stroke-opacity="0.12" />
          <text :x="scales.x(t)" :y="scales.margin.top + scales.plotH + 14" text-anchor="middle">{{ t }} m</text>
        </g>
      </g>

      <g clip-path="url(#profile-plot-clip)">
        <!-- Gaps: no data, drawn as hatched columns, never as a value -->
        <rect v-for="(g, i) in gapRects" :key="`gap${i}`" :x="g.x" :y="scales.margin.top" :width="g.width" :height="scales.plotH" fill="url(#profile-gap-hatch)" data-gap />

        <!-- All points: min–max envelope -->
        <path :d="paths.envelope" :fill="COLORS.envelope" stroke="none" data-envelope />
        <!-- Space between ground and top -->
        <path :d="paths.between" :fill="COLORS.fill" stroke="none" data-between />

        <!-- Class layers -->
        <g v-for="l in paths.layers" :key="l.code" :data-class-layer="l.code">
          <path :d="l.band" :fill="l.color" fill-opacity="0.35" stroke="none" />
          <path :d="l.line" :stroke="l.color" stroke-width="1.2" fill="none" />
        </g>

        <!-- Top and ground -->
        <path :d="paths.top" :stroke="COLORS.top" stroke-width="1.4" fill="none" data-top />
        <path :d="paths.groundFilled" :stroke="COLORS.ground" stroke-width="1.6" stroke-dasharray="4 3" fill="none" data-ground-fill />
        <path :d="paths.groundMeasured" :stroke="COLORS.ground" stroke-width="1.8" fill="none" data-ground />

        <!-- Polyline vertices -->
        <g v-for="v in vertices" :key="`v${v.distance}`">
          <line :x1="v.x" :x2="v.x" :y1="scales.margin.top" :y2="scales.margin.top + scales.plotH" stroke="currentColor" stroke-opacity="0.45" stroke-dasharray="2 3" />
        </g>

        <!-- Hover crosshair -->
        <g v-if="hover" data-hover>
          <line :x1="hoverX" :x2="hoverX" :y1="scales.margin.top" :y2="scales.margin.top + scales.plotH" stroke="currentColor" stroke-opacity="0.6" />
          <circle v-if="hover.ground !== null" :cx="hoverX" :cy="scales.y(hover.ground)" r="3.5" :fill="COLORS.ground" />
          <circle v-if="hover.top !== null" :cx="hoverX" :cy="scales.y(hover.top)" r="3.5" :fill="COLORS.top" />
        </g>
      </g>

      <text :x="scales.margin.left + scales.plotW" :y="scales.margin.top + 10" text-anchor="end" class="fill-current text-[10px] text-muted-foreground" data-exaggeration>
        {{ scales.exaggeration >= 0.995 && scales.exaggeration <= 1.005 ? '1 : 1' : `${scales.exaggeration.toFixed(1)} × vertical` }}
      </text>
    </svg>

    <!-- Readout -->
    <div
      v-if="hover"
      class="pointer-events-none absolute top-2 z-10 w-[190px] rounded-md border border-border bg-card/95 px-2 py-1.5 font-mono text-[11px] leading-tight text-foreground shadow"
      :style="{ left: `${tooltipLeft}px` }"
      data-profile-readout
    >
      <div class="mb-0.5 flex justify-between text-muted-foreground"><span>distance</span><span>{{ formatDistance(hover.distance) }}</span></div>
      <template v-if="hover.gap">
        <div class="text-muted-foreground">no data</div>
      </template>
      <template v-else>
        <div class="flex justify-between"><span :style="{ color: COLORS.ground }">ground</span><span>{{ fmtM(hover.ground) }}<span v-if="hover.groundInterpolated" class="text-muted-foreground"> ~</span></span></div>
        <div class="flex justify-between"><span :style="{ color: COLORS.top }">top</span><span>{{ fmtM(hover.top) }}</span></div>
        <div class="flex justify-between"><span>height</span><span>{{ fmtM(hover.height) }}</span></div>
        <div v-for="c in hover.classes" :key="c.code" class="flex justify-between">
          <span class="truncate" :style="{ color: c.color }">{{ c.label }}</span>
          <span>{{ fmtM(c.height) }}</span>
        </div>
        <div class="mt-0.5 text-right text-muted-foreground">{{ hover.count }} pts</div>
      </template>
    </div>
  </div>
</template>
