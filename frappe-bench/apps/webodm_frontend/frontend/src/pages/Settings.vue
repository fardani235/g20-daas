<template>
  <div class="p-6 space-y-6 max-w-2xl">
    <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Settings</h2>

    <!-- Processing -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Processing</h3>
      <div class="space-y-3">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default Preset</label>
          <select v-model="form.default_preset" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm">
            <option :value="null">None</option>
            <option v-for="p in presets" :key="p.name" :value="p.name">{{ p.preset_name }}</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="auto-process" v-model="form.auto_start_processing" class="rounded border-gray-300 dark:border-gray-600" />
          <label for="auto-process" class="text-sm text-gray-700 dark:text-gray-300">Auto-start processing after upload</label>
        </div>
      </div>
    </div>

    <!-- Limits -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Limits</h3>
      <div class="space-y-3">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Upload Size (MB)</label>
          <input type="number" v-model.number="form.max_file_size_mb" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm" />
        </div>
      </div>
    </div>

    <!-- Notifications -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Notifications</h3>
      <div class="flex items-center gap-2">
        <input type="checkbox" id="email-done" v-model="form.email_notifications" class="rounded border-gray-300 dark:border-gray-600" />
        <label for="email-done" class="text-sm text-gray-700 dark:text-gray-300">Email notifications</label>
      </div>
    </div>

    <div class="flex justify-end">
      <Button variant="solid" theme="blue" :loading="saving" @click="onSave">
        <template #prefix><FeatherIcon name="check" class="h-4 w-4" /></template>
        Save Settings
      </Button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Button, FeatherIcon, toast } from 'frappe-ui'
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
