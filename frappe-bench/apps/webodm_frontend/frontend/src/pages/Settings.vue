<template>
  <div class="max-w-2xl space-y-6">
    <PageHeader
      title="Settings"
      description="Defaults and limits for your organization's processing."
    />

    <section class="space-y-4 rounded-lg border border-border bg-card p-6">
      <h2 class="font-medium text-card-foreground">Processing</h2>
      <div class="space-y-1.5">
        <Label for="default-preset">Default preset</Label>
        <Select id="default-preset" v-model="form.default_preset">
          <option :value="null">None</option>
          <option v-for="p in presets" :key="p.name" :value="p.name">
            {{ p.preset_name }}
          </option>
        </Select>
        <p class="text-xs text-muted-foreground">
          Applied to new tasks when no preset is chosen at upload.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <input
          id="auto-process"
          v-model="form.auto_start_processing"
          type="checkbox"
          class="size-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Label for="auto-process" class="font-normal">
          Auto-start processing after upload
        </Label>
      </div>
    </section>

    <section class="space-y-4 rounded-lg border border-border bg-card p-6">
      <h2 class="font-medium text-card-foreground">Limits</h2>
      <div class="space-y-1.5">
        <Label for="max-size">Max upload size (MB)</Label>
        <Input id="max-size" v-model.number="form.max_file_size_mb" type="number" />
        <p class="text-xs text-muted-foreground">
          Capped by the platform limit set by the operator.
        </p>
      </div>
    </section>

    <section class="space-y-4 rounded-lg border border-border bg-card p-6">
      <h2 class="font-medium text-card-foreground">Notifications</h2>
      <div class="flex items-center gap-2">
        <input
          id="email-done"
          v-model="form.email_notifications"
          type="checkbox"
          class="size-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Label for="email-done" class="font-normal">Email notifications</Label>
      </div>
    </section>

    <div class="flex justify-end">
      <Button :loading="saving" @click="onSave">
        <Check />
        Save settings
      </Button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Check } from 'lucide-vue-next'
import { Button, Input, Label, Select } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { toast } from '@/lib/toast'
import { getSettings, saveSettings, listPresets } from '@/lib/presets'

const presets = ref([])
const saving = ref(false)
const form = ref({
  default_preset: null,
  auto_start_processing: false,
  max_file_size_mb: 200,
  email_notifications: true,
})

onMounted(async () => {
  try {
    presets.value = await listPresets()
  } catch (e) {
    presets.value = []
  }
  try {
    const s = await getSettings()
    form.value = {
      default_preset: s.default_preset || null,
      auto_start_processing: !!s.auto_start_processing,
      max_file_size_mb: s.max_file_size_mb ?? 200,
      email_notifications: !!s.email_notifications,
    }
  } catch (e) {
    toast.error(e.message || 'Failed to load settings')
  }
})

async function onSave() {
  saving.value = true
  try {
    await saveSettings({
      default_preset: form.value.default_preset,
      auto_start_processing: form.value.auto_start_processing ? 1 : 0,
      max_file_size_mb: form.value.max_file_size_mb,
      email_notifications: form.value.email_notifications ? 1 : 0,
    })
    toast.success('Settings saved')
  } catch (e) {
    toast.error(e.message || 'Failed to save settings')
  } finally {
    saving.value = false
  }
}
</script>
