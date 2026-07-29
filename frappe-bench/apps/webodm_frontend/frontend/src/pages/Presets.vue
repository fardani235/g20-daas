<template>
  <div class="space-y-6">
    <PageHeader
      title="Processing presets"
      description="Reusable OpenDroneMap option sets for your organization."
    >
      <template #actions>
        <Button @click="openCreate">
          <Plus />
          New preset
        </Button>
      </template>
    </PageHeader>

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Options</th>
            <th class="px-4 py-3 font-medium">Scope</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in presets"
            :key="p.name"
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3 font-medium text-card-foreground">{{ p.preset_name }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ p.options.length }} option(s)</td>
            <td class="px-4 py-3">
              <Badge :variant="p.system ? 'default' : 'secondary'">
                {{ p.system ? 'System' : 'Organization' }}
              </Badge>
            </td>
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" class="size-8" title="Edit preset" @click="openEdit(p)">
                <Pencil />
                <span class="sr-only">Edit {{ p.preset_name }}</span>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                class="size-8 text-muted-foreground hover:text-destructive"
                title="Delete preset"
                @click="onDelete(p)"
              >
                <Trash2 />
                <span class="sr-only">Delete {{ p.preset_name }}</span>
              </Button>
            </td>
          </tr>
          <tr v-if="!presets.length">
            <td colspan="4" class="px-4 py-10 text-center text-muted-foreground">
              No presets yet.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Dialog
      v-model:open="showModal"
      :title="`${editing ? 'Edit' : 'New'} preset`"
      class="sm:max-w-lg"
    >
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="preset-name">Name</Label>
          <Input id="preset-name" v-model="draft.preset_name" />
        </div>

        <p v-if="odm.error.value" class="text-sm text-destructive">{{ odm.error.value }}</p>
        <p v-else-if="odm.loading.value" class="text-sm text-muted-foreground">
          Loading options…
        </p>
        <OdmOptionsForm
          v-else
          :catalog="odm.catalog.value"
          v-model="values"
          :field-type="odm.fieldType"
        />
      </div>
      <template #footer>
        <Button variant="ghost" @click="showModal = false">Cancel</Button>
        <Button :loading="saving" @click="onSave">Save</Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { Badge, Button, Dialog, Input, Label } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { toast } from '@/lib/toast'
import { listPresets, savePreset, deletePreset } from '@/lib/presets'
import { useOdmOptions } from '@/composables/useOdmOptions'
import OdmOptionsForm from '@/components/OdmOptionsForm.vue'

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
