<script setup>
import { computed } from 'vue'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const props = defineProps({
  variant: { type: String, default: 'default' },
  title: { type: String, default: '' },
  class: { type: String, default: '' },
})

const alert = cva('w-full rounded-lg border px-4 py-3 text-sm', {
  variants: {
    variant: {
      default: 'border-border bg-muted text-foreground',
      destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
      success: 'border-success/30 bg-success/10 text-success',
      warning: 'border-warning/30 bg-warning/10 text-warning',
    },
  },
  defaultVariants: { variant: 'default' },
})

const classes = computed(() => cn(alert({ variant: props.variant }), props.class))
</script>

<template>
  <div role="alert" :class="classes">
    <p v-if="title" class="font-medium">{{ title }}</p>
    <slot />
  </div>
</template>
