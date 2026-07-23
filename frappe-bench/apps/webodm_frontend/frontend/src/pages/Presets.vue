<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Processing Presets</h2>
      <Button variant="solid" theme="blue" @click="openCreate">
        <template #prefix><FeatherIcon name="plus" class="h-4 w-4" /></template>
        New Preset
      </Button>
    </div>

    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Options</th>
            <th class="px-4 py-3 font-medium">Scope</th>
            <th class="px-4 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in presets" :key="p.name" class="border-b dark:border-gray-700 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-800">
            <td class="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">{{ p.preset_name }}</td>
            <td class="px-4 py-3 text-gray-600 dark:text-gray-400">{{ p.options.length }} option(s)</td>
            <td class="px-4 py-3">
              <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                :class="p.system ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300' : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'">
                {{ p.system ? 'System' : 'User' }}
              </span>
            </td>
            <td class="px-4 py-3 text-right space-x-2">
              <button class="text-gray-400 hover:text-blue-600" @click="openEdit(p)"><FeatherIcon name="edit-2" class="h-4 w-4" /></button>
              <button class="text-gray-400 hover:text-red-600" @click="onDelete(p)"><FeatherIcon name="trash-2" class="h-4 w-4" /></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Create/Edit modal -->
    <div v-if="showModal" class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/50" @click.self="showModal = false">
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg mx-4 p-6 max-h-[85vh] overflow-y-auto">
        <h3 class="text-lg font-semibold mb-4 text-gray-900 dark:text-gray-100">{{ editing ? 'Edit' : 'New' }} Preset</h3>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
        <input v-model="draft.preset_name" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm mb-4" />

        <p v-if="odm.error.value" class="text-sm text-red-600 mb-3">{{ odm.error.value }}</p>
        <p v-else-if="odm.loading.value" class="text-sm text-gray-500 mb-3">Loading options…</p>

        <div v-else class="space-y-2">
          <div v-for="opt in odm.catalog.value" :key="opt.name" class="flex items-center gap-2">
            <label class="text-sm text-gray-700 dark:text-gray-300 w-48 truncate" :title="opt.help">{{ opt.name }}</label>
            <input v-if="odm.fieldType(opt) === 'checkbox'" type="checkbox" v-model="values[opt.name]" class="rounded" />
            <select v-else-if="odm.fieldType(opt) === 'select'" v-model="values[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm">
              <option v-for="d in opt.domain" :key="d" :value="d">{{ d }}</option>
            </select>
            <input v-else-if="odm.fieldType(opt) === 'number'" type="number" v-model.number="values[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm" />
            <input v-else type="text" v-model="values[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm" />
          </div>
        </div>

        <div class="flex justify-end gap-2 mt-6">
          <Button variant="ghost" @click="showModal = false">Cancel</Button>
          <Button variant="solid" theme="blue" :loading="saving" @click="onSave">Save</Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { Button, FeatherIcon, toast } from 'frappe-ui'
import { listPresets, savePreset, deletePreset } from '@/lib/presets'
import { useOdmOptions } from '@/composables/useOdmOptions'

const presets = ref([])
const showModal = ref(false)
const editing = ref(null)
const saving = ref(false)
const draft = reactive({ preset_name: '' })
const values = ref({})
const odm = useOdmOptions()

async function refresh() {
  try {
    presets.value = await listPresets()
  } catch (e) {
    toast.error(e.message || 'Failed to load presets')
  }
}
refresh()

async function openCreate() {
  editing.value = null
  draft.preset_name = ''
  values.value = {}
  showModal.value = true
  await odm.load()
  odm.seedEnumDefaults(values.value)
}

async function openEdit(p) {
  editing.value = p
  draft.preset_name = p.preset_name
  values.value = Object.fromEntries((p.options || []).map(o => [o.name, o.value]))
  showModal.value = true
  await odm.load()
  odm.seedEnumDefaults(values.value)
}

// Serialize only the options the user set to a non-empty / non-false value,
// into NodeODM's [{name, value}] array form.
function toOptionsArray() {
  const out = []
  for (const [name, value] of Object.entries(values.value)) {
    if (value === '' || value === null || value === undefined || value === false) continue
    out.push({ name, value })
  }
  return out
}

async function onSave() {
  if (!draft.preset_name) { toast.error('Name is required'); return }
  saving.value = true
  try {
    await savePreset({
      name: editing.value?.name || null,
      preset_name: draft.preset_name,
      options: toOptionsArray(),
      system: editing.value?.system || 0,
    })
    toast.success('Preset saved')
    showModal.value = false
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to save preset')
  } finally {
    saving.value = false
  }
}

async function onDelete(p) {
  try {
    await deletePreset(p.name)
    toast.success('Preset deleted')
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to delete preset')
  }
}
</script>
