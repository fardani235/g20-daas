<script setup>
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  modelValue: { default: null },
  class: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const classes = computed(() =>
  cn(
    'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring disabled:cursor-not-allowed disabled:opacity-50',
    props.class,
  ),
)
</script>

<template>
  <select
    :value="modelValue"
    :class="classes"
    @change="emit('update:modelValue', $event.target.value)"
  >
    <slot />
  </select>
</template>
