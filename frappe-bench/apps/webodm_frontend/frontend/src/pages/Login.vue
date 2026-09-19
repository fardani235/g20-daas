<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Map as MapIcon } from 'lucide-vue-next'
import { Alert, Button, Input, Label } from '@/components/ui'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref(null)

async function login() {
  loading.value = true
  error.value = null
  try {
    const res = await fetch('/api/method/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ usr: username.value, pwd: password.value }),
    })
    const data = await res.json()
    if (data.message === 'Logged In') {
      try {
        const csrfRes = await fetch('/api/method/webodm_core.api.csrf.get_token')
        if (csrfRes.ok) {
          const { message: token } = await csrfRes.json()
          window.csrf_token = token
        }
      } catch {}
      const redirect = route.query.redirect || '/dashboard'
      router.push(redirect)
    } else {
      error.value = data.message || 'Invalid credentials'
    }
  } catch (e) {
    error.value = 'Connection failed'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-muted p-6">
    <div class="w-full max-w-sm">
      <div class="rounded-lg border border-border bg-card p-8 shadow-sm">
        <div class="mb-8 text-center">
          <MapIcon class="mx-auto mb-3 size-10 text-primary" />
          <h1 class="text-2xl font-semibold tracking-tight text-card-foreground">G20 Tech</h1>
          <p class="mt-1 text-sm text-muted-foreground">Sign in to your account</p>
        </div>
        <form class="space-y-4" @submit.prevent="login">
          <Alert v-if="error" variant="destructive" :title="error" />
          <div class="space-y-1.5">
            <Label for="username">Username</Label>
            <Input id="username" v-model="username" type="text" placeholder="Administrator" required />
          </div>
          <div class="space-y-1.5">
            <Label for="password">Password</Label>
            <Input id="password" v-model="password" type="password" placeholder="••••••••" required />
          </div>
          <Button type="submit" class="w-full" :loading="loading">Sign in</Button>
        </form>
      </div>
    </div>
  </div>
</template>
