<template>
  <div class="space-y-3">
    <div v-for="field in fields" :key="field.name" class="space-y-1.5">
      <Label :for="`pp-${field.name}`">
        {{ field.label }}
        <span v-if="field.required" class="text-destructive">*</span>
      </Label>

      <div v-if="field.type === 'boolean'" class="flex items-center gap-2">
        <input
          :id="`pp-${field.name}`"
          type="checkbox"
          class="rounded"
          :checked="modelValue[field.name] === true"
          @change="set(field.name, $event.target.checked)"
        />
      </div>

      <Select
        v-else-if="field.enum"
        :id="`pp-${field.name}`"
        :model-value="modelValue[field.name] ?? ''"
        @update:model-value="v => set(field.name, v)"
      >
        <option value="">Default</option>
        <option v-for="opt in field.enum" :key="opt" :value="opt">{{ opt }}</option>
      </Select>

      <Input
        v-else-if="field.type === 'number' || field.type === 'integer'"
        :id="`pp-${field.name}`"
        type="number"
        :step="field.type === 'integer' ? 1 : 'any'"
        :model-value="modelValue[field.name] ?? ''"
        @update:model-value="v => set(field.name, v === '' ? undefined : Number(v))"
      />

      <Textarea
        v-else-if="field.list"
        :id="`pp-${field.name}`"
        :rows="3"
        placeholder="One per line or comma-separated"
        :model-value="listText(field.name)"
        @update:model-value="v => setList(field.name, v)"
      />

      <Textarea
        v-else-if="field.type === 'array' || field.type === 'object'"
        :id="`pp-${field.name}`"
        :model-value="jsonFor(field.name)"
        @update:model-value="v => setJson(field.name, v)"
      />

      <Input
        v-else
        :id="`pp-${field.name}`"
        :model-value="modelValue[field.name] ?? ''"
        @update:model-value="v => set(field.name, v === '' ? undefined : v)"
      />

      <p v-if="field.description" class="text-xs text-muted-foreground">
        {{ field.description }}
      </p>
    </div>

    <p v-if="!fields.length" class="text-sm text-muted-foreground">
      This plugin has no parameters.
    </p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Input, Label, Select, Textarea } from '@/components/ui'
import { parseList, formatList } from '@/lib/paramLists'

const props = defineProps({
  schema: { type: Object, default: () => ({}) },
  modelValue: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

// Field list derived from the plugin's JSON parameter schema. Only the flat
// scalar shapes our operations emit are specialised; anything else falls back
// to a JSON textarea.
const fields = computed(() => {
  const properties = props.schema?.properties || {}
  const required = new Set(props.schema?.required || [])
  return Object.entries(properties).map(([name, spec]) => ({
    name,
    label: spec.title || name,
    description: spec.description || '',
    type: spec.type || 'string',
    enum: Array.isArray(spec.enum) ? spec.enum : null,
    // Arrays of strings get a plain comma/newline textarea instead of JSON.
    list: spec.type === 'array' && spec.items?.type === 'string',
    required: required.has(name),
  }))
})

function set(name, value) {
  const next = { ...props.modelValue }
  if (value === undefined) delete next[name]
  else next[name] = value
  emit('update:modelValue', next)
}

function jsonFor(name) {
  const value = props.modelValue[name]
  return value === undefined ? '' : JSON.stringify(value, null, 2)
}

function setJson(name, text) {
  try {
    set(name, text ? JSON.parse(text) : undefined)
  } catch {
    // Keep the raw text while the user is mid-edit; only valid JSON is emitted.
  }
}

function listText(name) {
  return formatList(props.modelValue[name])
}

function setList(name, text) {
  const items = parseList(text)
  set(name, items.length ? items : undefined)
}
</script>
