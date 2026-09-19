<script setup>
import { computed } from 'vue'
import { DropdownMenuItem as RadixItem } from 'radix-vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  class: { type: String, default: '' },
})
const emit = defineEmits(['select'])

const classes = computed(() =>
  cn(
    'relative flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
    props.class,
  ),
)
</script>

<template>
  <RadixItem :disabled="disabled" :class="classes" @select="emit('select', $event)">
    <slot />
  </RadixItem>
</template>
