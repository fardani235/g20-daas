<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-100 dark:bg-gray-900">
    <div class="w-full max-w-sm mx-auto">
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-sm border dark:border-gray-700 p-8">
        <div class="text-center mb-8">
          <FeatherIcon name="map" class="h-10 w-10 text-blue-600 dark:text-blue-400 mx-auto mb-3" />
          <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">WebODM</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">Sign in to your account</p>
        </div>
        <form @submit.prevent="login" class="space-y-4">
          <Alert v-if="error" :title="error" variant="error" class="mb-4" />
          <FormControl
            label="Username"
            type="text"
            v-model="username"
            placeholder="Administrator"
            :required="true"
          />
          <FormControl
            label="Password"
            type="password"
            v-model="password"
            placeholder="admin"
            :required="true"
          />
          <Button
            type="submit"
            variant="solid"
            class="w-full"
            :loading="loading"
          >
            Sign in
          </Button>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Alert, Button, FeatherIcon, FormControl } from 'frappe-ui'

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
      const redirect = route.query.redirect || '/'
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
