<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Button, Dialog, Input, Label, Select, Textarea } from '@/components/ui'
import NumberField from './NumberField.vue'
import { gsdCm, saveCamera } from '@/lib/flightplan'
import { toast } from '@/lib/toast'

// Create or edit one of the organization's own camera profiles. Five numbers
// (sensor size, image size, focal length) are all the planner needs for
// resolution, footprint and spacing; the live read-out lets the user check
// them against a figure they already know.
const props = defineProps({
  open: { type: Boolean, required: true },
  camera: { type: Object, default: null },      // an existing profile to edit or copy; null = new
  copy: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open', 'saved'])

const blank = () => ({
  docname: null, name: '', manufacturer: '', sensor_width_mm: null, sensor_height_mm: null,
  image_width_px: null, image_height_px: null, focal_length_mm: null, min_interval_s: null,
  shutter: 'electronic', notes: '',
})
const form = reactive(blank())
const saving = ref(false)

watch(() => props.open, value => {
  if (!value) return
  Object.assign(form, blank())
  const c = props.camera
  if (c) {
    Object.assign(form, {
      docname: props.copy ? null : (c.docname || null),
      name: props.copy ? `${c.name} (copy)` : c.name,
      manufacturer: c.manufacturer || '',
      sensor_width_mm: c.sensor_width_mm, sensor_height_mm: c.sensor_height_mm,
      image_width_px: c.image_width_px, image_height_px: c.image_height_px,
      focal_length_mm: c.focal_length_mm, min_interval_s: c.min_interval_s ?? null,
      shutter: c.shutter || 'electronic', notes: c.notes || '',
    })
  }
})

const complete = computed(() => form.name.trim() && form.sensor_width_mm > 0 && form.sensor_height_mm > 0
  && form.image_width_px > 0 && form.image_height_px > 0 && form.focal_length_mm > 0)
const check = computed(() => {
  if (!complete.value) return ''
  const gsd = gsdCm(form, 100)
  const across = form.sensor_width_mm * 100 / form.focal_length_mm
  const along = form.sensor_height_mm * 100 / form.focal_length_mm
  return `At 100 m this camera resolves ${gsd.toFixed(2)} cm/px and one photo covers ${Math.round(Math.max(across, along))} × ${Math.round(Math.min(across, along))} m.`
})

async function submit() {
  if (!complete.value) return
  saving.value = true
  try {
    const saved = await saveCamera({ ...form, name: form.name.trim() })
    toast.success(`Camera "${saved.label}" saved`)
    emit('saved', saved)
    emit('update:open', false)
  } catch (e) {
    toast.error(e.message || 'Could not save the camera')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    :open="open"
    :title="form.docname ? 'Edit camera' : 'Add camera'"
    description="For a camera that is not in the built-in list. Use the lens's real focal length, not the 35 mm equivalent."
    class="max-w-lg"
    @update:open="emit('update:open', $event)"
  >
    <form class="space-y-4" data-camera-form @submit.prevent="submit">
      <div class="grid gap-3 sm:grid-cols-2">
        <div class="space-y-1">
          <Label for="cam-name" class="text-xs text-muted-foreground">Name</Label>
          <Input id="cam-name" v-model="form.name" placeholder="e.g. RX0 II" required />
        </div>
        <div class="space-y-1">
          <Label for="cam-maker" class="text-xs text-muted-foreground">Manufacturer</Label>
          <Input id="cam-maker" v-model="form.manufacturer" placeholder="e.g. Sony" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <NumberField id="cam-sw" v-model="form.sensor_width_mm" label="Sensor width" unit="mm" :min="0" help="Width of the active image area." />
        <NumberField id="cam-sh" v-model="form.sensor_height_mm" label="Sensor height" unit="mm" :min="0" />
        <NumberField id="cam-iw" v-model="form.image_width_px" label="Image width" unit="px" :min="1" step="1" />
        <NumberField id="cam-ih" v-model="form.image_height_px" label="Image height" unit="px" :min="1" step="1" />
        <NumberField id="cam-f" v-model="form.focal_length_mm" label="Focal length" unit="mm" :min="0" help="The real focal length of the lens." />
        <NumberField id="cam-int" v-model="form.min_interval_s" label="Shortest photo interval" unit="s" :min="0" placeholder="unknown" help="Leave empty if unknown: 2 s is assumed." />
      </div>
      <div class="space-y-1">
        <Label for="cam-shutter" class="text-xs text-muted-foreground">Shutter</Label>
        <Select id="cam-shutter" v-model="form.shutter">
          <option value="electronic">Electronic (rolling)</option>
          <option value="mechanical">Mechanical</option>
          <option value="global">Global</option>
        </Select>
      </div>
      <div class="space-y-1">
        <Label for="cam-notes" class="text-xs text-muted-foreground">Notes</Label>
        <Textarea id="cam-notes" v-model="form.notes" :rows="2" />
      </div>
      <p v-if="check" class="rounded-md bg-muted px-3 py-2 text-xs text-foreground" data-camera-check>
        {{ check }} Compare with a figure you trust for this camera.
      </p>
    </form>
    <template #footer>
      <Button variant="ghost" :disabled="saving" @click="emit('update:open', false)">Cancel</Button>
      <Button :disabled="!complete" :loading="saving" @click="submit">Save camera</Button>
    </template>
  </Dialog>
</template>
