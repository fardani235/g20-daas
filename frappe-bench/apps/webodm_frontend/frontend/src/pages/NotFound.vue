<script setup>
import { useRouter } from 'vue-router'
import { MapPinOff } from 'lucide-vue-next'
import { Button } from '@/components/ui'

const router = useRouter()
</script>

<template>
  <div class="flex h-full flex-col items-center justify-center p-6 text-center">
    <MapPinOff class="mb-4 size-16 text-muted-foreground/40" />
    <h1 class="mb-2 text-2xl font-semibold tracking-tight text-foreground">Page not found</h1>
    <p class="mb-6 text-muted-foreground">The page you're looking for doesn't exist.</p>
    <Button @click="router.push('/')">Back to home</Button>
  </div>
</template>
