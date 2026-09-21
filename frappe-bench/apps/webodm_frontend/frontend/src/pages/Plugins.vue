<template>
  <div class="space-y-6">
    <PageHeader title="Analysis plugins" description="Run geospatial analysis on completed task outputs.">
      <template #actions>
        <Button variant="ghost" :loading="loading" @click="refresh">
          <RefreshCw />
          Refresh
        </Button>
        <template v-if="canManage">
          <input
            ref="fileInput"
            type="file"
            accept=".zip,application/zip"
            class="hidden"
            @change="onFileChosen"
          />
          <Button :loading="uploading" @click="fileInput?.click()">
            <Upload />
            Upload plugin
          </Button>
        </template>
      </template>
    </PageHeader>

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Type</th>
            <th class="px-4 py-3 font-medium">Version</th>
            <th class="px-4 py-3 font-medium">Output</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="plugin in plugins"
            :key="plugin.op_id"
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3">
              <p class="font-medium text-card-foreground">{{ plugin.label }}</p>
              <p v-if="plugin.description" class="text-xs text-muted-foreground">
                {{ plugin.description }}
              </p>
            </td>
            <td class="px-4 py-3">
              <Badge :variant="plugin.plugin_type === 'User' ? 'outline' : 'secondary'">
                {{ pluginTypeLabel(plugin) }}
              </Badge>
            </td>
            <td class="px-4 py-3 text-muted-foreground">{{ plugin.version || '—' }}</td>
            <td class="px-4 py-3 text-muted-foreground capitalize">{{ plugin.output_kind }}</td>
            <td class="px-4 py-3">
              <Badge :variant="statusVariant(plugin)">{{ statusLabel(plugin) }}</Badge>
            </td>
            <td class="px-4 py-3 text-right">
              <template v-if="canManage">
                <Button
                  v-if="plugin.available"
                  variant="ghost"
                  size="sm"
                  :disabled="!plugin.platform_enabled"
                  @click="toggle(plugin)"
                >
                  {{ plugin.enabled ? 'Disable' : 'Enable' }}
                </Button>
                <Button
                  v-if="plugin.available && hasParams(plugin)"
                  variant="ghost"
                  size="icon"
                  class="size-8"
                  :title="`Configure ${plugin.label}`"
                  @click="openConfig(plugin)"
                >
                  <Settings2 />
                  <span class="sr-only">Configure {{ plugin.label }}</span>
                </Button>
                <Button
                  v-if="plugin.plugin_type === 'User'"
                  variant="ghost"
                  size="icon"
                  class="size-8 text-destructive"
                  :title="`Remove ${plugin.label}`"
                  @click="removeTarget = plugin"
                >
                  <Trash2 />
                  <span class="sr-only">Remove {{ plugin.label }}</span>
                </Button>
              </template>
              <span v-else class="text-xs text-muted-foreground">Admin only</span>
            </td>
          </tr>
          <tr v-if="!plugins.length && !loading">
            <td colspan="6" class="px-4 py-10 text-center text-muted-foreground">
              No plugins available.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Dialog v-model:open="showModal" :title="`Configure ${editing?.label || ''}`" class="sm:max-w-lg">
      <div v-if="editing?.models?.length" class="mb-3 space-y-1.5">
        <Label for="plugin-model">Model</Label>
        <Select id="plugin-model" :model-value="selectedModelId" @update:model-value="chooseModel">
          <option v-for="m in editing.models" :key="m.id" :value="m.id">{{ m.label }}</option>
          <option value="">Custom (type a model below)</option>
        </Select>
      </div>
      <PluginParamsForm v-if="editing" :schema="editing.params_schema" v-model="draftSettings" />
      <template #footer>
        <Button variant="ghost" @click="showModal = false">Cancel</Button>
        <Button :loading="saving" @click="saveConfig">Save</Button>
      </template>
    </Dialog>

    <Dialog
      :open="!!removeTarget"
      title="Remove plugin"
      class="sm:max-w-sm"
      @update:open="value => { if (!value) removeTarget = null }"
    >
      <p class="text-sm text-muted-foreground">
        Remove <strong class="text-foreground">{{ removeTarget?.label }}</strong> from your
        organization? Its previous runs and their outputs are deleted too. This cannot be undone.
      </p>
      <template #footer>
        <Button variant="ghost" @click="removeTarget = null">Cancel</Button>
        <Button variant="destructive" :loading="removing" @click="remove">Remove</Button>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RefreshCw, Settings2, Trash2, Upload } from 'lucide-vue-next'
import { Badge, Button, Dialog, Label, Select } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import PluginParamsForm from '@/components/PluginParamsForm.vue'
import { toast } from '@/lib/toast'
import {
  canManagePlugins,
  listPlugins,
  pluginTypeLabel,
  removePlugin,
  savePluginSetting,
  schemaDefaults,
  uploadPlugin,
} from '@/lib/plugins'
import { applyModelChoice, matchingModel } from '@/lib/knownModels'
import { whoami } from '@/lib/presets'

const plugins = ref([])
const loading = ref(false)
const canManage = ref(false)
const showModal = ref(false)
const saving = ref(false)
const editing = ref(null)
const draftSettings = ref({})
const fileInput = ref(null)
const uploading = ref(false)
const removeTarget = ref(null)
const removing = ref(false)

async function refresh() {
  loading.value = true
  try {
    plugins.value = await listPlugins()
  } catch (e) {
    toast.error(e.message || 'Failed to load plugins')
  } finally {
    loading.value = false
  }
}

async function loadAdmin() {
  try {
    canManage.value = canManagePlugins(await whoami())
  } catch {
    canManage.value = false
  }
}

function hasParams(plugin) {
  return Object.keys(plugin.params_schema?.properties || {}).length > 0
}

function statusVariant(plugin) {
  if (!plugin.available || !plugin.platform_enabled) return 'secondary'
  return plugin.enabled ? 'success' : 'secondary'
}

function statusLabel(plugin) {
  if (!plugin.available) return 'Unavailable'
  if (!plugin.platform_enabled) return 'Disabled by platform'
  return plugin.enabled ? 'Enabled' : 'Disabled'
}

async function toggle(plugin) {
  try {
    await savePluginSetting({ plugin: plugin.op_id, enabled: !plugin.enabled })
    toast.success(plugin.enabled ? 'Plugin disabled' : 'Plugin enabled')
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to update plugin')
  }
}

async function onFileChosen(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  uploading.value = true
  try {
    const result = await uploadPlugin(file)
    toast.success(result.created ? `Installed ${result.label}` : `Updated ${result.label} to ${result.version}`)
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to upload plugin')
  } finally {
    uploading.value = false
  }
}

async function remove() {
  if (!removeTarget.value) return
  removing.value = true
  try {
    await removePlugin(removeTarget.value.op_id)
    toast.success('Plugin removed')
    removeTarget.value = null
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to remove plugin')
  } finally {
    removing.value = false
  }
}

function openConfig(plugin) {
  editing.value = plugin
  // Schema defaults first, then any saved organization overrides.
  draftSettings.value = { ...schemaDefaults(plugin.params_schema), ...(plugin.settings || {}) }
  showModal.value = true
}

const selectedModelId = computed(() => {
  const model = matchingModel(editing.value?.models, draftSettings.value)
  return model ? model.id : ''
})

function chooseModel(id) {
  const model = (editing.value?.models || []).find(m => m.id === id)
  if (model) draftSettings.value = applyModelChoice(draftSettings.value, model)
}

async function saveConfig() {
  if (!editing.value) return
  saving.value = true
  try {
    await savePluginSetting({
      plugin: editing.value.op_id,
      settings: draftSettings.value,
    })
    toast.success('Plugin settings saved')
    showModal.value = false
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to save settings')
  } finally {
    saving.value = false
  }
}

refresh()
loadAdmin()
</script>
