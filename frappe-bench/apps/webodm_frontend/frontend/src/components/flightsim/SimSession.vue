<script setup>
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import L from 'leaflet'
import { LoaderCircle, X } from 'lucide-vue-next'
import { Alert, Badge, Button, Select } from '@/components/ui'
import SimControls from '@/components/flightsim/SimControls.vue'
import SimDashboard from '@/components/flightsim/SimDashboard.vue'
import SimProfile from '@/components/flightsim/SimProfile.vue'
import { useMissionSim } from '@/composables/useMissionSim'
import { SIMULATION_NOTICE } from '@/lib/flightsim'
import { OVERLAP_CLASSES } from '@/lib/flightsim/coverage'
import { comparisonRows, coverageRows } from '@/lib/flightsim/describe'
import { createSimMapLayer } from '@/lib/flightsim/mapLayer'
import { terrainLabel } from '@/lib/flightsim/terrain'
import { getBasemapDef } from '@/lib/mapLayers'

// An open simulation: the controller, the layer on the planner's map, the
// dock, and - teleported into the pane the shell provides beside the map -
// the 3D view. Mounted by `FlightSimulator` only while a simulation is open,
// and loaded on demand, so the planner page carries none of this until
// "Simulate flight" is pressed. The 3D view (three.js) is a further chunk.
const props = defineProps({
  host: { type: Object, default: null },               // the shell's root element: carries the state for the browser checks
  pane: { type: Object, default: null },               // where the 3D view goes (null while the layout is 2D)
  layout: { type: String, default: 'split' },          // '2d' | 'split' | '3d'
  mission: { type: Object, default: null },
  definition: { type: Object, default: null },
  project: { type: String, default: null },
  getMap: { type: Function, required: true },
  basemap: { type: String, default: 'esri-satellite' },
  ortho: { type: Object, default: null },
})
const emit = defineEmits(['update:layout', 'status', 'close'])

const SimView3D = defineAsyncComponent(() => import('@/components/flightsim/SimView3D.vue'))
const LAYOUTS = [
  { id: '2d', label: '2D', hint: 'The map only' },
  { id: 'split', label: 'Split', hint: 'Map and 3D side by side' },
  { id: '3d', label: '3D', hint: 'The 3D view only' },
]
const COVERAGE = [
  { id: 'off', label: 'Coverage off' },
  { id: 'covered', label: 'Covered so far' },
  { id: 'overlap', label: 'Photos per point' },
]

const sim = useMissionSim()
const followMap = ref(false)
let mapLayer = null
let stopFrames = null

const simulation = computed(() => sim.simulation.value)
const ready = computed(() => sim.status.value === 'ready' && !!simulation.value)
const basemapDef = computed(() => getBasemapDef(props.basemap))
const compare = computed(() => (simulation.value ? comparisonRows(simulation.value) : []))
const cover = computed(() => (simulation.value ? coverageRows(simulation.value) : []))
const groundText = computed(() => (simulation.value ? terrainLabel(simulation.value.terrain) : ''))
const flown = computed(() => sim.state.value?.distance || 0)
// The aircraft and the ground under it, in the unit the profile is drawn in.
const here = computed(() => {
  const state = sim.state.value
  if (!state || !simulation.value) return { flight: null, ground: null }
  return simulation.value.profile.absolute
    ? { flight: state.amsl, ground: state.ground }
    : { flight: state.alt, ground: null }
})

function detachMap() {
  stopFrames?.()
  stopFrames = null
  mapLayer?.destroy()
  mapLayer = null
}

function attachMap() {
  detachMap()
  const map = props.getMap()
  if (!map || !simulation.value) return
  mapLayer = createSimMapLayer(map, simulation.value, {
    canvas: sim.canvas.value,
    onFollowEnd: () => { followMap.value = false },
  })
  mapLayer.setCoverageVisible(sim.coverageMode.value !== 'off')
  mapLayer.setFollow(followMap.value)
  stopFrames = sim.onFrame(state => mapLayer?.update(state))
}

// The map has just lost room to the 3D view and the dock: bring the whole
// flight, takeoff point included, back into what is left of it.
function showWholeFlight() {
  const map = props.getMap()
  const mission = props.mission
  if (!map?.fitBounds || !mission?.waypoints?.length) return
  if (map.invalidateSize) map.invalidateSize({ pan: false })
  const bounds = L.latLngBounds(mission.waypoints.map(w => [w.lat, w.lng]))
  if (mission.takeoff) bounds.extend([mission.takeoff.lat, mission.takeoff.lng])
  if (bounds.isValid()) map.fitBounds(bounds, { paddingTopLeft: [48, 84], paddingBottomRight: [48, 48], maxZoom: 19, animate: false })
}

// Built when the simulation opens, and again whenever the plan underneath it changes.
let alive = true
watch(
  () => props.mission,
  async mission => {
    detachMap()
    if (!mission) { sim.close(); return }
    const ok = await sim.open(mission, { definition: props.definition, project: props.project })
    if (!ok || !alive) return
    attachMap()
    await nextTick()
    showWholeFlight()
  },
  { immediate: true },
)

watch(sim.status, status => emit('status', status), { immediate: true })
watch(sim.coverageMode, mode => mapLayer?.setCoverageVisible(mode !== 'off'))
watch(followMap, on => { mapLayer?.setFollow(on); if (on) sim.refresh() })

// For the browser checks: the state being drawn and where the map has put the aircraft.
onMounted(() => {
  if (!props.host) return
  props.host.simSnapshot = () => ({
    status: sim.status.value,
    playback: { ...sim.playback },
    state: sim.state.value ? { ...sim.state.value } : null,
    live: simulation.value ? { ...simulation.value.sample(sim.playback.time) } : null,
    map: mapLayer?.snapshot?.() || null,
    summary: simulation.value?.summary || null,
    notes: simulation.value?.notes || [],
  })
})
watchEffect(() => {
  const host = props.host
  if (!host) return
  if (ready.value) {
    host.dataset.simTime = sim.playback.time.toFixed(2)
    host.dataset.simPlaying = String(sim.playback.playing)
  } else {
    delete host.dataset.simTime
    delete host.dataset.simPlaying
  }
})

onBeforeUnmount(() => {
  alive = false
  detachMap()
  sim.close()
  if (props.host) {
    delete props.host.simSnapshot
    delete props.host.dataset.simTime
    delete props.host.dataset.simPlaying
  }
})
</script>

<template>
  <!-- The 3D view lives beside the map, in the pane the shell lays out. -->
  <Teleport v-if="pane" :to="pane">
    <SimView3D
      v-if="ready"
      :key="sim.build.value"
      :simulation="simulation"
      :on-frame="sim.onFrame"
      :on-coverage="sim.onCoverage"
      :coverage-canvas="sim.canvas.value"
      :coverage-mode="sim.coverageMode.value"
      :basemap="basemapDef"
      :ortho="ortho"
    />
    <div v-else class="flex h-full items-center justify-center gap-2 bg-muted/40 text-sm text-muted-foreground">
      <LoaderCircle v-if="sim.status.value === 'loading'" class="size-4 animate-spin" />
      {{ sim.status.value === 'error' ? 'No simulation to show' : 'Preparing the 3D view…' }}
    </div>
  </Teleport>

  <section
    class="max-h-[52vh] flex-shrink-0 space-y-2 overflow-y-auto border-t border-border bg-card p-3"
    aria-label="Flight simulation"
    data-sim-dock
  >
    <header class="flex flex-wrap items-center gap-2">
      <h2 class="text-sm font-semibold text-card-foreground">Flight simulation</h2>
      <Badge variant="warning" :title="SIMULATION_NOTICE">Estimate, not telemetry</Badge>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <label v-if="ready && layout !== '3d'" class="flex cursor-pointer items-center gap-1.5 text-xs text-foreground" title="Keep the map centred on the aircraft">
          <input v-model="followMap" type="checkbox" class="rounded" data-sim-follow-map /> Follow on map
        </label>
        <Select
          v-if="ready && simulation.coverage"
          class="h-8 w-[150px] px-2 text-xs"
          :model-value="sim.coverageMode.value"
          aria-label="Coverage overlay"
          data-sim-coverage
          @update:model-value="sim.setCoverageMode($event)"
        >
          <option v-for="c in COVERAGE" :key="c.id" :value="c.id">{{ c.label }}</option>
        </Select>
        <div class="grid grid-cols-3 gap-0.5 rounded-md bg-muted p-0.5 text-xs" role="group" aria-label="Views" data-sim-layout>
          <button
            v-for="l in LAYOUTS"
            :key="l.id"
            type="button"
            class="rounded px-2.5 py-1 transition-colors"
            :class="layout === l.id ? 'bg-card text-card-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
            :title="l.hint"
            :aria-pressed="layout === l.id"
            :data-layout="l.id"
            @click="emit('update:layout', l.id)"
          >{{ l.label }}</button>
        </div>
        <Button variant="outline" size="sm" title="Close the simulation and return to planning" data-sim-exit @click="emit('close')">
          <X />
          Exit
        </Button>
      </div>
    </header>

    <ul
      v-if="ready && sim.coverageMode.value === 'overlap'"
      class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground"
      aria-label="Photos that will see each point (estimate)"
      data-sim-overlap-legend
    >
      <li class="text-foreground">Photos per point (estimate):</li>
      <li v-for="c in OVERLAP_CLASSES" :key="c.min" class="flex items-center gap-1">
        <span class="inline-block size-2.5 rounded-sm" :style="{ background: `rgb(${c.rgba[0]},${c.rgba[1]},${c.rgba[2]})` }"></span>{{ c.label }}
      </li>
    </ul>

    <p v-if="sim.status.value === 'loading'" class="flex items-center gap-2 text-sm text-muted-foreground" role="status" data-sim-loading>
      <LoaderCircle class="size-4 animate-spin" /> Preparing the simulation…
    </p>
    <Alert v-else-if="sim.status.value === 'error'" variant="destructive" data-sim-error>{{ sim.error.value }}</Alert>

    <template v-else-if="ready">
      <SimControls
        :playback="sim.playback"
        :trajectory="simulation.trajectory"
        @play="sim.play"
        @pause="sim.pause"
        @stop="sim.stop"
        @restart="sim.restart"
        @seek="sim.seek"
        @speed="sim.setSpeed"
        @flight="sim.seekFlight"
      />

      <div class="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(280px,36%)]">
        <SimDashboard :state="sim.state.value" :simulation="simulation" />
        <SimProfile
          :profile="simulation.profile"
          :distance="flown"
          :flight="here.flight"
          :ground="here.ground"
          @seek="sim.seekDistance"
        />
      </div>

      <details class="text-xs" data-sim-details>
        <summary class="cursor-pointer text-muted-foreground hover:text-foreground">Simulation and plan compared, coverage, assumptions</summary>
        <div class="mt-2 grid gap-4 lg:grid-cols-3">
          <table class="w-full text-left" data-sim-compare>
            <caption class="mb-1 text-left font-medium text-foreground">Simulated flight and the plan's estimate</caption>
            <thead>
              <tr class="text-muted-foreground"><th class="font-normal"></th><th class="font-normal">Simulated</th><th class="font-normal">Plan</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in compare" :key="r.label" class="text-foreground" :title="r.note || null">
                <td class="text-muted-foreground">{{ r.label }}</td><td class="tabular-nums">{{ r.simulated }}</td><td class="tabular-nums">{{ r.planned }}</td>
              </tr>
            </tbody>
          </table>
          <dl v-if="cover.length" class="grid grid-cols-[auto_1fr] content-start gap-x-3 gap-y-0.5" data-sim-coverage-figures>
            <dt class="col-span-2 mb-0.5 font-medium text-foreground">Photo coverage (estimate)</dt>
            <template v-for="r in cover" :key="r.label">
              <dt class="text-muted-foreground">{{ r.label }}</dt>
              <dd class="text-foreground">{{ r.value }} <span class="text-muted-foreground">· {{ r.detail }}</span></dd>
            </template>
          </dl>
          <div class="space-y-1" data-sim-notes>
            <p class="font-medium text-foreground">What this simulation assumes</p>
            <p class="text-muted-foreground">Ground: {{ groundText }}.</p>
            <p v-if="sim.terrainMessage.value" class="text-warning">{{ sim.terrainMessage.value }}</p>
            <p v-for="(n, i) in simulation.notes" :key="i" class="text-muted-foreground">{{ n }}</p>
            <p class="text-muted-foreground">
              Battery is time in the air over the aircraft's rated endurance, as in the plan. A battery change is shown as a
              minute on the ground and is not counted as flight time.
            </p>
          </div>
        </div>
      </details>
    </template>

    <p class="text-[11px] leading-snug text-muted-foreground" data-sim-notice>{{ SIMULATION_NOTICE }}</p>
  </section>
</template>
