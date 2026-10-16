<script setup>
import { computed } from 'vue'
import { Input, Label } from '@/components/ui'

// A labelled number input with its unit and one line of help. Emits a number,
// or null when the field is emptied (which the planner reads as "default").
const props = defineProps({
  modelValue: { type: [Number, String], default: null },
  id: { type: String, required: true },
  label: { type: String, required: true },
  unit: { type: String, default: '' },
  help: { type: String, default: '' },
  min: { type: Number, default: undefined },
  max: { type: Number, default: undefined },
  step: { type: [Number, String], default: 'any' },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  invalid: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const shown = computed(() => (props.modelValue === null || props.modelValue === undefined ? '' : props.modelValue))

function onInput(value) {
  if (value === '' || value === null) { emit('update:modelValue', null); return }
  const n = Number(value)
  emit('update:modelValue', Number.isFinite(n) ? n : null)
}
</script>

<template>
  <div class="space-y-1">
    <Label :for="id" class="text-xs text-muted-foreground" :title="help || undefined">{{ label }}</Label>
    <div class="relative">
      <Input
        :id="id"
        type="number"
        inputmode="decimal"
        :model-value="shown"
        :min="min"
        :max="max"
        :step="step"
        :placeholder="placeholder"
        :disabled="disabled"
        :aria-invalid="invalid ? 'true' : undefined"
        :aria-describedby="help ? `${id}-help` : undefined"
        :class="[unit ? 'pr-12' : '', invalid ? 'border-destructive focus-visible:ring-destructive' : '']"
        @update:model-value="onInput"
      />
      <span
        v-if="unit"
        class="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs text-muted-foreground"
      >{{ unit }}</span>
    </div>
    <p v-if="help" :id="`${id}-help`" class="sr-only">{{ help }}</p>
  </div>
</template>
