<script setup>
/**
 * Chrome for public pages that are also reachable signed-in (`layout: 'auto'`
 * routes). Rendered only when the visitor is signed out — App.vue puts
 * signed-in users in AppLayout instead — so it links to sign-in, never to
 * the dashboard. Same themed style as About/Contact.
 */
import { Map as MapIcon } from 'lucide-vue-next'
import { Button } from '@/components/ui'

const year = new Date().getFullYear()
</script>

<template>
  <div class="min-h-screen bg-background">
    <nav class="sticky top-0 z-40 w-full border-b border-border bg-background/80 backdrop-blur-md">
      <div class="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <router-link to="/" class="flex items-center gap-2">
          <MapIcon class="size-5 text-primary" />
          <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
        </router-link>
        <div class="flex items-center gap-4 sm:gap-6">
          <router-link to="/marketplace" class="text-sm text-foreground">Marketplace</router-link>
          <router-link to="/about" class="hidden text-sm text-muted-foreground transition-colors hover:text-foreground sm:inline">About</router-link>
          <router-link :to="{ path: '/login', query: { redirect: $route.fullPath } }">
            <Button size="sm">Sign in</Button>
          </router-link>
        </div>
      </div>
    </nav>

    <main class="mx-auto w-full max-w-6xl px-6 py-8">
      <slot />
    </main>

    <footer class="border-t border-border py-8">
      <div class="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 text-sm text-muted-foreground">
        <span>&copy; {{ year }} G20 Tech. All rights reserved.</span>
        <div class="flex gap-4">
          <router-link to="/about" class="hover:text-foreground">About</router-link>
          <router-link to="/contact" class="hover:text-foreground">Contact</router-link>
        </div>
      </div>
    </footer>
  </div>
</template>
