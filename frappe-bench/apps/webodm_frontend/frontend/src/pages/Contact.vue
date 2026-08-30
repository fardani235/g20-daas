<script setup>
import { ref } from 'vue'
import { Button, Input, Label, Select, Textarea } from '@/components/ui'
import { Mail, Map as MapIcon, Phone, Clock, CheckCircle } from 'lucide-vue-next'

const form = ref({
  name: '',
  email: '',
  company: '',
  phone: '',
  orgSize: '',
  useCase: '',
  message: '',
})

const submitted = ref(false)
const submitting = ref(false)

const orgSizes = [
  { value: '1-10', label: '1-10 employees' },
  { value: '11-50', label: '11-50 employees' },
  { value: '51-200', label: '51-200 employees' },
  { value: '201-1000', label: '201-1,000 employees' },
  { value: '1000+', label: '1,000+ employees' },
]

const useCases = [
  { value: 'survey', label: 'Survey & Mapping' },
  { value: 'construction', label: 'Construction' },
  { value: 'agriculture', label: 'Agriculture' },
  { value: 'mining', label: 'Mining' },
  { value: 'environmental', label: 'Environmental' },
  { value: 'inspection', label: 'Inspection' },
  { value: 'other', label: 'Other' },
]

async function handleSubmit() {
  submitting.value = true
  // Simulate form submission
  await new Promise((r) => setTimeout(r, 1500))
  submitting.value = false
  submitted.value = true
}
</script>

<template>
  <div class="min-h-screen bg-background">
    <!-- Nav -->
    <nav class="fixed top-0 z-50 w-full border-b border-border bg-background/80 backdrop-blur-md">
      <div class="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <router-link to="/" class="flex items-center gap-2">
          <MapIcon class="size-5 text-primary" />
          <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
        </router-link>
        <div class="hidden items-center gap-6 md:flex">
          <router-link to="/" class="text-sm text-muted-foreground transition-colors hover:text-foreground">Home</router-link>
          <router-link to="/about" class="text-sm text-muted-foreground transition-colors hover:text-foreground">About</router-link>
          <router-link to="/contact" class="text-sm text-foreground transition-colors">Contact</router-link>
          <router-link to="/login">
            <Button size="sm">Sign in</Button>
          </router-link>
        </div>
      </div>
    </nav>

    <!-- Hero -->
    <section class="relative overflow-hidden bg-slate-950 pt-16 text-slate-100">
      <div
        class="pointer-events-none absolute inset-0 opacity-30"
        style="background-image: radial-gradient(circle at 70% 30%, rgb(37 99 235 / 0.5), transparent 55%)"
      />
      <div class="relative mx-auto max-w-4xl px-6 py-20 text-center lg:py-28">
        <h1 class="text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
          Get in <span class="text-primary">Touch</span>
        </h1>
        <p class="mx-auto mt-5 max-w-2xl text-lg text-slate-300">
          Have a question about our platform, need a custom solution, or want
          to discuss a partnership? We'd love to hear from you.
        </p>
      </div>
    </section>

    <!-- Contact Section -->
    <section class="py-20">
      <div class="mx-auto max-w-6xl px-6">
        <div class="grid gap-12 lg:grid-cols-5">
          <!-- Form -->
          <div class="lg:col-span-3">
            <!-- Success state -->
            <div v-if="submitted" class="rounded-lg border border-border bg-card p-8 text-center">
              <CheckCircle class="mx-auto size-12 text-green-500" />
              <h2 class="mt-4 text-xl font-semibold text-card-foreground">Thank you!</h2>
              <p class="mt-2 text-muted-foreground">
                We've received your message and will get back to you within 24 hours.
              </p>
              <router-link to="/" class="mt-6 inline-block">
                <Button variant="secondary">Back to home</Button>
              </router-link>
            </div>

            <!-- Form -->
            <form v-else class="rounded-lg border border-border bg-card p-8" @submit.prevent="handleSubmit">
              <h2 class="text-xl font-semibold text-card-foreground">Send us a message</h2>
              <p class="mt-2 text-sm text-muted-foreground">
                Fill out the form below and our team will get back to you promptly.
              </p>

              <div class="mt-6 grid gap-4 sm:grid-cols-2">
                <div class="space-y-2">
                  <Label for="name">Full name *</Label>
                  <Input id="name" v-model="form.name" placeholder="Your name" required />
                </div>
                <div class="space-y-2">
                  <Label for="email">Email *</Label>
                  <Input id="email" v-model="form.email" type="email" placeholder="you@company.com" required />
                </div>
                <div class="space-y-2">
                  <Label for="company">Company *</Label>
                  <Input id="company" v-model="form.company" placeholder="Your company" required />
                </div>
                <div class="space-y-2">
                  <Label for="phone">Phone</Label>
                  <Input id="phone" v-model="form.phone" type="tel" placeholder="+62 xxx" />
                </div>
                <div class="space-y-2">
                  <Label for="orgSize">Organization size</Label>
                  <Select id="orgSize" v-model="form.orgSize">
                    <option value="" disabled>Select size</option>
                    <option v-for="size in orgSizes" :key="size.value" :value="size.value">
                      {{ size.label }}
                    </option>
                  </Select>
                </div>
                <div class="space-y-2">
                  <Label for="useCase">Use case</Label>
                  <Select id="useCase" v-model="form.useCase">
                    <option value="" disabled>Select use case</option>
                    <option v-for="uc in useCases" :key="uc.value" :value="uc.value">
                      {{ uc.label }}
                    </option>
                  </Select>
                </div>
              </div>

              <div class="mt-4 space-y-2">
                <Label for="message">Message *</Label>
                <Textarea id="message" v-model="form.message" placeholder="Tell us about your project or question..." rows="4" required />
              </div>

              <Button type="submit" class="mt-6 w-full" :disabled="submitting">
                {{ submitting ? 'Sending...' : 'Send message' }}
              </Button>
            </form>
          </div>

          <!-- Info -->
          <div class="lg:col-span-2">
            <div class="rounded-lg border border-border bg-card p-8">
              <h2 class="text-xl font-semibold text-card-foreground">Contact information</h2>
              <p class="mt-2 text-sm text-muted-foreground">
                Reach out to us through any of the following channels.
              </p>

              <div class="mt-8 space-y-6">
                <div class="flex items-start gap-4">
                  <div class="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <MapIcon class="size-5 text-primary" />
                  </div>
                  <div>
                    <h3 class="font-medium text-card-foreground">Office</h3>
                    <p class="mt-1 text-sm text-muted-foreground">
                      Jakarta, Indonesia<br />
                      (Address placeholder)
                    </p>
                  </div>
                </div>

                <div class="flex items-start gap-4">
                  <div class="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <Mail class="size-5 text-primary" />
                  </div>
                  <div>
                    <h3 class="font-medium text-card-foreground">Email</h3>
                    <p class="mt-1 text-sm text-muted-foreground">info@g20tech.com</p>
                  </div>
                </div>

                <div class="flex items-start gap-4">
                  <div class="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <Phone class="size-5 text-primary" />
                  </div>
                  <div>
                    <h3 class="font-medium text-card-foreground">Phone</h3>
                    <p class="mt-1 text-sm text-muted-foreground">+62 xxx-xxxx-xxxx</p>
                  </div>
                </div>

                <div class="flex items-start gap-4">
                  <div class="flex size-10 items-center justify-center rounded-lg bg-primary/10">
                    <Clock class="size-5 text-primary" />
                  </div>
                  <div>
                    <h3 class="font-medium text-card-foreground">Business hours</h3>
                    <p class="mt-1 text-sm text-muted-foreground">
                      Monday - Friday<br />
                      09:00 - 17:00 WIB
                    </p>
                  </div>
                </div>
              </div>

              <div class="mt-8 border-t border-border pt-6">
                <h3 class="text-sm font-medium text-card-foreground">Follow us</h3>
                <div class="mt-3 flex gap-4">
                  <span class="text-sm text-muted-foreground hover:text-foreground cursor-pointer">LinkedIn</span>
                  <span class="text-sm text-muted-foreground hover:text-foreground cursor-pointer">Twitter</span>
                  <span class="text-sm text-muted-foreground hover:text-foreground cursor-pointer">Instagram</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-border py-12">
      <div class="mx-auto max-w-6xl px-6">
        <div class="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <router-link to="/" class="flex items-center gap-2">
              <MapIcon class="size-5 text-primary" />
              <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
            </router-link>
            <p class="mt-3 text-sm text-muted-foreground">
              Drone data processing and GIS solutions for modern surveying teams.
            </p>
          </div>
          <div>
            <h4 class="mb-3 text-sm font-medium text-foreground">Product</h4>
            <ul class="space-y-2">
              <li><a href="/#capabilities" class="text-sm text-muted-foreground hover:text-foreground">Features</a></li>
              <li><a href="/#pricing" class="text-sm text-muted-foreground hover:text-foreground">Pricing</a></li>
            </ul>
          </div>
          <div>
            <h4 class="mb-3 text-sm font-medium text-foreground">Company</h4>
            <ul class="space-y-2">
              <li><router-link to="/about" class="text-sm text-muted-foreground hover:text-foreground">About</router-link></li>
              <li><router-link to="/contact" class="text-sm text-muted-foreground hover:text-foreground">Contact</router-link></li>
            </ul>
          </div>
          <div>
            <h4 class="mb-3 text-sm font-medium text-foreground">Legal</h4>
            <ul class="space-y-2">
              <li><span class="text-sm text-muted-foreground">Privacy Policy</span></li>
              <li><span class="text-sm text-muted-foreground">Terms of Service</span></li>
            </ul>
          </div>
        </div>
        <div class="mt-8 border-t border-border pt-8 text-center text-sm text-muted-foreground">
          &copy; {{ new Date().getFullYear() }} G20 Tech. All rights reserved.
        </div>
      </div>
    </footer>
  </div>
</template>
