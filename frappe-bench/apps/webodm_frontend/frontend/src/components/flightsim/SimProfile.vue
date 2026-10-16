<script setup>
import { computed, ref } from 'vue'
import { formatDistance } from '@/lib/flightsim/describe'
import { profileAtDistance, profileGeometry } from '@/lib/flightsim/profile'

// Ground and flight height against the distance flown, with a cursor that
// follows the playback. Clicking or dragging on it moves the simulation to
// that point of the flight.
const props = defineProps({
  profile: { type: Object, default: null },
  distance: { type: Number, default: 0 },          // ground distance flown at the current time
  // The aircraft's and the ground's height at the current time, in the profile's unit. Distance alone
  // does not say where the aircraft is while it climbs or lands vertically; without them the
  // cursor reads the profile at `distance`.
  flight: { type: Number, default: null },
  ground: { type: Number, default: null },
  width: { type: Number, default: 420 },
  height: { type: Number, default: 120 },
})
const emit = defineEmits(['seek'])

const svg = ref(null)
const geometry = computed(() => profileGeometry(props.profile, props.width, props.height))
const cursor = computed(() => {
  const g = geometry.value
  if (!g) return null
  const at = profileAtDistance(props.profile, props.distance)
  const flight = Number.isFinite(props.flight) ? props.flight : at.flight
  const ground = Number.isFinite(props.ground) ? props.ground : at.ground
  return {
    x: g.x(Math.min(Math.max(props.distance, 0), g.length)),
    flightY: Number.isFinite(flight) ? g.y(flight) : null,
    groundY: Number.isFinite(ground) ? g.y(ground) : null,
    flight,
    ground,
  }
})
const lowest = computed(() => {
  const g = geometry.value
  const low = props.profile?.lowest
  if (!g || !low) return null
  const at = profileAtDistance(props.profile, low.distance)
  return { x: g.x(low.distance), y1: g.y(at.flight), y2: g.y(at.ground), clearance: low.clearance }
})
const unit = computed(() => (props.profile?.absolute ? 'm elevation' : 'm above takeoff'))
const readout = computed(() => {
  const c = cursor.value
  if (!c) return ''
  const parts = [`${formatDistance(props.distance)} flown`, `aircraft ${Math.round(c.flight)} ${unit.value}`]
  if (c.groundY !== null) parts.push(`ground ${Math.round(c.ground)} m`)
  return parts.join(' · ')
})

let dragging = false
function seekFrom(e) {
  const g = geometry.value
  const el = svg.value
  if (!g || !el) return
  const box = el.getBoundingClientRect()
  if (!(box.width > 0)) return
  emit('seek', g.distanceAt(((e.clientX - box.left) / box.width) * props.width))
}
function onDown(e) {
  dragging = true
  e.currentTarget.setPointerCapture?.(e.pointerId)
  seekFrom(e)
}
function onMove(e) { if (dragging) seekFrom(e) }
function onUp() { dragging = false }
function onKey(e) {
  const g = geometry.value
  if (!g) return
  const step = g.length / 100
  if (e.key === 'ArrowRight') { e.preventDefault(); emit('seek', Math.min(g.length, props.distance + step)) }
  if (e.key === 'ArrowLeft') { e.preventDefault(); emit('seek', Math.max(0, props.distance - step)) }
}
</script>

<template>
  <figure v-if="geometry" class="min-w-0 space-y-1" data-sim-profile>
    <svg
      ref="svg"
      :viewBox="`0 0 ${width} ${height}`"
      preserveAspectRatio="none"
      class="h-[104px] w-full cursor-crosshair touch-none rounded border border-border bg-muted/30"
      role="slider"
      tabindex="0"
      aria-label="Elevation profile of the simulated flight. Click or drag to move the simulation."
      :aria-valuemin="0"
      :aria-valuemax="Math.round(geometry.length)"
      :aria-valuenow="Math.round(distance)"
      :aria-valuetext="readout"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointercancel="onUp"
      @keydown="onKey"
    >
      <!-- Battery flights as alternating bands. -->
      <rect
        v-for="(b, i) in geometry.bands"
        v-show="geometry.bands.length > 1 && i % 2 === 1"
        :key="b.index"
        :x="b.x0" :width="Math.max(0, b.x1 - b.x0)" y="0" :height="height"
        class="fill-foreground/5"
      />
      <path v-if="geometry.area" :d="geometry.area" class="fill-emerald-600/25" />
      <path v-if="geometry.ground" :d="geometry.ground" fill="none" class="stroke-emerald-700 dark:stroke-emerald-400" stroke-width="1.5" vector-effect="non-scaling-stroke" />
      <path :d="geometry.flight" fill="none" stroke="#f59e0b" stroke-width="1.75" vector-effect="non-scaling-stroke" />
      <line
        v-if="lowest"
        :x1="lowest.x" :x2="lowest.x" :y1="lowest.y1" :y2="lowest.y2"
        stroke="#dc2626" stroke-width="1.5" stroke-dasharray="3 2" vector-effect="non-scaling-stroke"
      />
      <g v-if="cursor" data-sim-profile-cursor :data-x="cursor.x.toFixed(1)">
        <line :x1="cursor.x" :x2="cursor.x" y1="0" :y2="height" class="stroke-foreground" stroke-width="1" vector-effect="non-scaling-stroke" />
        <line
          v-if="cursor.flightY !== null && cursor.groundY !== null"
          :x1="cursor.x" :x2="cursor.x" :y1="cursor.flightY" :y2="cursor.groundY"
          stroke="#22d3ee" stroke-width="2.5" vector-effect="non-scaling-stroke"
        />
      </g>
    </svg>
    <figcaption class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
      <span class="inline-flex items-center gap-1"><span class="inline-block h-0.5 w-3 bg-[#f59e0b]"></span>Aircraft</span>
      <span v-if="profile.hasGround" class="inline-flex items-center gap-1"><span class="inline-block h-0.5 w-3 bg-emerald-700 dark:bg-emerald-400"></span>Ground (estimate)</span>
      <span v-else>No elevation data: heights above the takeoff point</span>
      <span v-if="lowest" class="inline-flex items-center gap-1" data-sim-profile-lowest>
        <span class="inline-block h-0.5 w-3 bg-[#dc2626]"></span>Lowest clearance {{ Math.round(lowest.clearance) }} m
      </span>
      <span class="ml-auto tabular-nums" data-sim-profile-readout>{{ readout }}</span>
    </figcaption>
  </figure>
</template>
