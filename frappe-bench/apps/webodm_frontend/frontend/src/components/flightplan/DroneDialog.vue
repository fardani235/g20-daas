<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Button, Dialog, Input, Label, Select, Textarea } from '@/components/ui'
import NumberField from './NumberField.vue'
import { formatDuration, saveDrone } from '@/lib/flightplan'
import { toast } from '@/lib/toast'

// Create or edit one of the organization's own drone profiles. What the
// aircraft's flight app can be given is asked as one question ("flight app
// family"); the server expands it into the mission formats the profile is
// offered at export. An aircraft is never given a capability by default.
const props = defineProps({
  open: { type: Boolean, required: true },
  drone: { type: Object, default: null },        // an existing profile to edit or copy; null = new
  copy: { type: Boolean, default: false },
  cameras: { type: Array, default: () => [] },
  ecosystems: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:open', 'saved'])

const blank = () => ({
  docname: null, model: '', manufacturer: '', airframe: 'multirotor', rtk: 'none',
  max_flight_time_min: null, endurance_factor: 0.8, max_speed_ms: null, max_mission_speed_ms: null,
  cruise_speed_ms: null, min_speed_ms: null, max_ascent_ms: null, max_descent_ms: null, max_wind_ms: null,
  service_ceiling_m: null, max_height_above_takeoff_m: null, takeoff_mass_g: null, min_turn_radius_m: null,
  cameras: [], default_camera: null, ecosystem: 'mavlink', wpml_drone_enum: null, wpml_drone_sub_enum: 0, notes: '',
})
const form = reactive(blank())
const saving = ref(false)
const cameraFilter = ref('')

// Which family a built-in profile corresponds to, when it is copied.
function ecosystemOf(drone) {
  if (drone.ecosystem) return drone.ecosystem
  const mission = drone.mission || {}
  if (!mission.waypoints) return 'manual'
  const ids = (mission.formats || []).map(f => f.id)
  const wpml = (mission.formats || []).find(f => f.id === 'dji-wpml')
  if (wpml) return wpml.level === 'official' ? 'dji-enterprise' : 'dji-fly'
  if (ids.includes('qgc-plan') || ids.includes('mavlink-wpl')) return 'mavlink'
  if (ids.includes('litchi-csv')) return 'litchi'
  return 'vendor'
}

watch(() => props.open, value => {
  if (!value) return
  Object.assign(form, blank())
  cameraFilter.value = ''
  const d = props.drone
  if (!d) return
  for (const key of Object.keys(blank())) {
    if (key in d && d[key] !== undefined) form[key] = d[key]
  }
  form.docname = props.copy ? null : (d.docname || null)
  form.model = props.copy ? `${d.model} (copy)` : d.model
  form.cameras = (d.cameras || []).filter(c => c !== '*')
  if ((d.cameras || []).includes('*')) form.cameras = d.default_camera ? [d.default_camera] : []
  form.default_camera = d.default_camera || form.cameras[0] || null
  form.ecosystem = ecosystemOf(d)
  form.wpml_drone_enum = d.mission?.wpml?.drone_enum ?? null
  form.wpml_drone_sub_enum = d.mission?.wpml?.drone_sub_enum ?? 0
})

const eco = computed(() => props.ecosystems.find(e => e.id === form.ecosystem) || null)
const fixedWing = computed(() => form.airframe !== 'multirotor')
const shownCameras = computed(() => {
  const q = cameraFilter.value.trim().toLowerCase()
  const list = props.cameras.filter(c => !q || c.label.toLowerCase().includes(q) || form.cameras.includes(c.id))
  return list.slice().sort((a, b) => (form.cameras.includes(b.id) - form.cameras.includes(a.id)) || a.label.localeCompare(b.label))
})
const usable = computed(() => {
  if (!(form.max_flight_time_min > 0)) return ''
  const s = form.max_flight_time_min * 60 * (form.endurance_factor || 0.8) * 0.75
  return `About ${formatDuration(s)} usable per battery with a 25 % reserve.`
})
const problems = computed(() => {
  const out = []
  if (!form.model.trim()) out.push('Enter the model.')
  if (!(form.max_flight_time_min > 0)) out.push('Enter the rated flight time.')
  if (!(form.max_speed_ms > 0)) out.push('Enter the top speed.')
  if (!form.cameras.length) out.push('Choose at least one camera.')
  if (fixedWing.value && !(form.min_turn_radius_m > 0)) out.push('A fixed-wing or VTOL needs its turn radius.')
  if (eco.value?.needs_wpml && !(form.wpml_drone_enum > 0)) out.push('This flight app needs the DJI WPML aircraft id.')
  return out
})

function toggleCamera(id) {
  const i = form.cameras.indexOf(id)
  if (i === -1) form.cameras.push(id)
  else form.cameras.splice(i, 1)
  if (!form.cameras.includes(form.default_camera)) form.default_camera = form.cameras[0] || null
}

async function submit() {
  if (problems.value.length) return
  saving.value = true
  try {
    const payload = { ...form, model: form.model.trim() }
    delete payload.wpml_drone_enum
    delete payload.wpml_drone_sub_enum
    if (eco.value?.needs_wpml) {
      payload.mission = { wpml: { drone_enum: form.wpml_drone_enum, drone_sub_enum: form.wpml_drone_sub_enum || 0 } }
    }
    const saved = await saveDrone(payload)
    toast.success(`Drone "${saved.label}" saved`)
    emit('saved', saved)
    emit('update:open', false)
  } catch (e) {
    toast.error(e.message || 'Could not save the drone')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    :open="open"
    :title="form.docname ? 'Edit drone' : 'Add drone'"
    description="For an aircraft that is not in the built-in list. Leave a figure empty if you do not know it: the planner then skips the check that needs it instead of guessing."
    class="max-w-2xl"
    @update:open="emit('update:open', $event)"
  >
    <form class="space-y-5" data-drone-form @submit.prevent="submit">
      <section class="grid gap-3 sm:grid-cols-2">
        <div class="space-y-1">
          <Label for="drone-model" class="text-xs text-muted-foreground">Model</Label>
          <Input id="drone-model" v-model="form.model" placeholder="e.g. Survey Hexa" required />
        </div>
        <div class="space-y-1">
          <Label for="drone-maker" class="text-xs text-muted-foreground">Manufacturer</Label>
          <Input id="drone-maker" v-model="form.manufacturer" />
        </div>
        <div class="space-y-1">
          <Label for="drone-airframe" class="text-xs text-muted-foreground">Airframe</Label>
          <Select id="drone-airframe" v-model="form.airframe">
            <option value="multirotor">Multirotor</option>
            <option value="fixed-wing">Fixed-wing</option>
            <option value="vtol">VTOL</option>
          </Select>
        </div>
        <div class="space-y-1">
          <Label for="drone-rtk" class="text-xs text-muted-foreground">RTK</Label>
          <Select id="drone-rtk" v-model="form.rtk">
            <option value="none">No</option>
            <option value="optional">Optional</option>
            <option value="standard">Yes</option>
          </Select>
        </div>
      </section>

      <section class="space-y-2">
        <h3 class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Endurance</h3>
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <NumberField id="drone-time" v-model="form.max_flight_time_min" label="Rated flight time" unit="min" :min="0" help="The manufacturer's figure, or your own measurement." />
          <NumberField id="drone-factor" v-model="form.endurance_factor" label="Endurance factor" :min="0.1" :max="1" step="0.05" help="Share of the rated time you really get with your payload (0.1 to 1)." />
          <NumberField id="drone-mass" v-model="form.takeoff_mass_g" label="Takeoff mass" unit="g" :min="0" placeholder="unknown" />
        </div>
        <p v-if="usable" class="text-xs text-muted-foreground">{{ usable }}</p>
      </section>

      <section class="space-y-2">
        <h3 class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Performance</h3>
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <NumberField id="drone-vmax" v-model="form.max_speed_ms" label="Top speed" unit="m/s" :min="0" />
          <NumberField id="drone-vmission" v-model="form.max_mission_speed_ms" label="Speed limit in missions" unit="m/s" :min="0" placeholder="same" help="If automated flight is limited below the top speed." />
          <NumberField id="drone-vcruise" v-model="form.cruise_speed_ms" label="Suggested mapping speed" unit="m/s" :min="0" placeholder="8" />
          <NumberField id="drone-up" v-model="form.max_ascent_ms" label="Climb rate" unit="m/s" :min="0" placeholder="unknown" />
          <NumberField id="drone-down" v-model="form.max_descent_ms" label="Descent rate" unit="m/s" :min="0" placeholder="unknown" />
          <NumberField id="drone-wind" v-model="form.max_wind_ms" label="Wind limit" unit="m/s" :min="0" placeholder="unknown" />
          <NumberField id="drone-ceiling" v-model="form.service_ceiling_m" label="Service ceiling" unit="m" :min="0" placeholder="unknown" help="Above sea level." />
          <NumberField id="drone-hmax" v-model="form.max_height_above_takeoff_m" label="Height limit above takeoff" unit="m" :min="0" placeholder="none" />
          <template v-if="fixedWing">
            <NumberField id="drone-vmin" v-model="form.min_speed_ms" label="Minimum airspeed" unit="m/s" :min="0" />
            <NumberField id="drone-radius" v-model="form.min_turn_radius_m" label="Turn radius" unit="m" :min="0" />
          </template>
        </div>
      </section>

      <section class="space-y-2">
        <h3 class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Cameras it carries</h3>
        <Input v-model="cameraFilter" placeholder="Filter cameras…" aria-label="Filter cameras" class="h-8" />
        <div class="max-h-36 overflow-y-auto rounded-md border border-border p-1" data-drone-cameras>
          <label
            v-for="c in shownCameras"
            :key="c.id"
            class="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-accent"
          >
            <input type="checkbox" :checked="form.cameras.includes(c.id)" class="rounded" @change="toggleCamera(c.id)" />
            <span class="min-w-0 flex-1 truncate text-foreground">{{ c.label }}</span>
            <span v-if="c.source === 'custom'" class="text-[10px] text-primary">yours</span>
          </label>
          <p v-if="!shownCameras.length" class="px-2 py-2 text-xs text-muted-foreground">No camera matches. Add yours on the Cameras tab first.</p>
        </div>
        <div v-if="form.cameras.length > 1" class="space-y-1">
          <Label for="drone-default-camera" class="text-xs text-muted-foreground">Default camera</Label>
          <Select id="drone-default-camera" v-model="form.default_camera">
            <option v-for="id in form.cameras" :key="id" :value="id">{{ cameras.find(c => c.id === id)?.label || id }}</option>
          </Select>
        </div>
      </section>

      <section class="space-y-2">
        <h3 class="text-xs font-medium uppercase tracking-wide text-muted-foreground">How missions reach the aircraft</h3>
        <Select id="drone-ecosystem" v-model="form.ecosystem" aria-label="Flight app family">
          <option v-for="e in ecosystems" :key="e.id" :value="e.id">{{ e.label }}</option>
        </Select>
        <p v-if="eco" class="text-xs text-muted-foreground">{{ eco.help }}</p>
        <div v-if="eco?.needs_wpml" class="grid grid-cols-2 gap-3">
          <NumberField id="drone-wpml" v-model="form.wpml_drone_enum" label="DJI WPML aircraft id" :min="1" step="1" help="droneEnumValue in DJI's WPML reference." />
          <NumberField id="drone-wpml-sub" v-model="form.wpml_drone_sub_enum" label="Sub-id" :min="0" step="1" />
        </div>
      </section>

      <div class="space-y-1">
        <Label for="drone-notes" class="text-xs text-muted-foreground">Notes</Label>
        <Textarea id="drone-notes" v-model="form.notes" :rows="2" />
      </div>

      <ul v-if="problems.length" class="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground" data-drone-problems>
        <li v-for="p in problems" :key="p">{{ p }}</li>
      </ul>
    </form>
    <template #footer>
      <Button variant="ghost" :disabled="saving" @click="emit('update:open', false)">Cancel</Button>
      <Button :disabled="problems.length > 0" :loading="saving" @click="submit">Save drone</Button>
    </template>
  </Dialog>
</template>
