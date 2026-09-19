<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Building2, Ticket } from 'lucide-vue-next'
import { Alert, Button, Input, Label } from '@/components/ui'
import { createOrganization, acceptInvitation } from '../lib/organization.js'

const router = useRouter()
const name = ref('')
const token = ref('')
const busy = ref(false)
const error = ref('')

async function doCreate() {
  busy.value = true
  error.value = ''
  try {
    await createOrganization(name.value)
    router.push({ name: 'Dashboard' })
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function doAccept() {
  busy.value = true
  error.value = ''
  try {
    await acceptInvitation(token.value)
    router.push({ name: 'Dashboard' })
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-muted p-6">
    <div class="w-full max-w-xl space-y-6">
      <div class="text-center">
        <h1 class="text-2xl font-semibold tracking-tight text-foreground">
          Set up your organization
        </h1>
        <p class="mt-2 text-sm text-muted-foreground">
          Projects, presets, and processing all live inside an organization.
          Create one, or join an existing team with an invitation.
        </p>
      </div>

      <Alert v-if="error" variant="destructive" :title="error" />

      <div class="grid gap-4 sm:grid-cols-2">
        <section class="rounded-lg border border-border bg-card p-6">
          <Building2 class="mb-3 size-6 text-primary" />
          <h2 class="font-medium text-card-foreground">Create an organization</h2>
          <p class="mt-1 text-sm text-muted-foreground">
            You'll be its owner and can invite teammates later.
          </p>
          <div class="mt-4 space-y-1.5">
            <Label for="org-name">Organization name</Label>
            <Input id="org-name" v-model="name" placeholder="Acme Surveying" />
          </div>
          <Button class="mt-4 w-full" :disabled="!name || busy" :loading="busy" @click="doCreate">
            Create organization
          </Button>
        </section>

        <section class="rounded-lg border border-border bg-card p-6">
          <Ticket class="mb-3 size-6 text-muted-foreground" />
          <h2 class="font-medium text-card-foreground">Join with an invitation</h2>
          <p class="mt-1 text-sm text-muted-foreground">
            Paste the token from your invitation email.
          </p>
          <div class="mt-4 space-y-1.5">
            <Label for="invite-token">Invitation token</Label>
            <Input id="invite-token" v-model="token" placeholder="Invitation token" />
          </div>
          <Button
            variant="outline"
            class="mt-4 w-full"
            :disabled="!token || busy"
            :loading="busy"
            @click="doAccept"
          >
            Join organization
          </Button>
        </section>
      </div>
    </div>
  </div>
</template>
