<script setup>
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// The flight simulator, wrapped around the planner's map.
//
// The planner page keeps its map exactly as it is and puts it in this
// component's slot. While `active` is false the component is only that
// slot. When a simulation is opened it makes room around the same map - a
// pane beside it for the 3D view, a dock below - and mounts `SimSession`,
// which fills them: the simulated aircraft on the map, the 3D view, the
// transport controls, the dashboard and the elevation profile, all fed by
// one controller so the views cannot disagree.
//
// This shell is all the planner page loads up front; the session, and with
// it the simulator, arrives when a simulation is first opened. The
// simulation reads the mission and never changes it; closing it leaves the
// planner as it was.
const props = defineProps({
  active: { type: Boolean, default: false },
  mission: { type: Object, default: null },
  definition: { type: Object, default: null },         // what the mission was computed from (for the elevation grid)
  project: { type: String, default: null },
  getMap: { type: Function, required: true },          // () => the planner's Leaflet map
  basemap: { type: String, default: 'esri-satellite' },
  ortho: { type: Object, default: null },              // { task, title } of the orthophoto, when it is shown on the map
})
const emit = defineEmits(['update:active'])

const SimSession = defineAsyncComponent(() => import('@/components/flightsim/SimSession.vue'))

// Side by side where there is room for two views; on a phone, the 3D view alone.
const layout = ref(typeof window !== 'undefined' && window.innerWidth < 768 ? '3d' : 'split')
const status = ref('loading')                           // of the open simulation: 'loading' | 'ready' | 'error' | 'idle'
const root = ref(null)
const mapPane = ref(null)
const pane3d = ref(null)
let observer = null

// Leaflet has to be told when its container changes size: the dock and the
// 3D pane take room from the map, and hiding the map pane gives it none.
function fitMap() {
  const map = props.getMap()
  if (map?.invalidateSize) map.invalidateSize({ pan: false })
}
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined' && mapPane.value) {
    observer = new ResizeObserver(fitMap)
    observer.observe(mapPane.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())
watch(() => [props.active, layout.value], fitMap, { flush: 'post' })
watch(() => props.active, on => { if (!on) status.value = 'loading' })
</script>

<template>
  <div
    ref="root"
    class="relative z-0 flex min-h-0 min-w-0 flex-1 flex-col bg-background"
    data-flight-sim
    :data-sim-status="active ? status : 'off'"
  >
    <div class="flex min-h-0 flex-1 flex-col lg:flex-row">
      <!-- The planner's own map, untouched. -->
      <div v-show="!active || layout !== '3d'" ref="mapPane" class="relative min-h-0 min-w-0 flex-1" data-sim-map-pane>
        <slot />
      </div>
      <div
        v-if="active && layout !== '2d'"
        ref="pane3d"
        class="relative min-h-0 min-w-0 flex-1 border-t border-border lg:border-l lg:border-t-0"
        data-sim-3d-pane
      ></div>
    </div>

    <SimSession
      v-if="active"
      v-model:layout="layout"
      :host="root"
      :pane="pane3d"
      :mission="mission"
      :definition="definition"
      :project="project"
      :get-map="getMap"
      :basemap="basemap"
      :ortho="ortho"
      @status="status = $event"
      @close="emit('update:active', false)"
    />
  </div>
</template>
