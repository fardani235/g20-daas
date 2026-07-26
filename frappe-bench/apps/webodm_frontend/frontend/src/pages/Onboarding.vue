<!-- webodm_frontend/frontend/src/pages/Onboarding.vue -->
<template>
  <div class="onboarding">
    <h1>Set up your organization</h1>
    <section>
      <h2>Create an organization</h2>
      <input v-model="name" placeholder="Organization name" />
      <button :disabled="!name || busy" @click="doCreate">Create</button>
    </section>
    <section>
      <h2>Join with an invitation</h2>
      <input v-model="token" placeholder="Invitation token" />
      <button :disabled="!token || busy" @click="doAccept">Join</button>
    </section>
    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { createOrganization, acceptInvitation } from '../lib/organization.js'

const router = useRouter()
const name = ref('')
const token = ref('')
const busy = ref(false)
const error = ref('')

async function doCreate() {
  busy.value = true; error.value = ''
  try { await createOrganization(name.value); router.push({ name: 'Dashboard' }) }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function doAccept() {
  busy.value = true; error.value = ''
  try { await acceptInvitation(token.value); router.push({ name: 'Dashboard' }) }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>
