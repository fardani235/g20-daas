<script setup>
import { ref } from 'vue'
import HeroFlightSim from '@/components/HeroFlightSim.vue'
import {
  ArrowRight,
  Box,
  Camera,
  Check,
  Cpu,
  Layers,
  Map as MapIcon,
  Menu,
  Mountain,
  Radio,
  Ruler,
  Share2,
  ShieldCheck,
  Star,
  Users,
  X,
} from 'lucide-vue-next'

const mobileMenuOpen = ref(false)

const pipeline = [
  {
    icon: Camera,
    step: 'Capture',
    body: 'Fly your mission and upload raw images. GPS EXIF is preserved end to end, so outputs land in real-world coordinates.',
  },
  {
    icon: Cpu,
    step: 'Process',
    body: 'OpenDroneMap reconstructs the scene — structure from motion, dense point cloud, mesh, texture. Fully automated.',
  },
  {
    icon: Ruler,
    step: 'Measure',
    body: 'Draw on the map to get distance, area, and DSM-backed volume. Answers, not just pictures.',
  },
  {
    icon: Share2,
    step: 'Share',
    body: 'Give your team one workspace with per-organization projects, presets, and access controls.',
  },
]

const capabilities = [
  {
    icon: MapIcon,
    title: 'Georeferenced orthophotos',
    body: 'Cloud-optimized GeoTIFFs served as map tiles, aligned to real-world coordinates.',
  },
  {
    icon: Mountain,
    title: 'DSM & DTM',
    body: 'Digital surface and terrain models — the elevation basis for volume calculations.',
  },
  {
    icon: Box,
    title: 'Textured 3D models',
    body: 'Browser-viewable reconstructions. No plugin, no desktop install required.',
  },
  {
    icon: Layers,
    title: 'Dense point clouds',
    body: 'Export the full reconstruction for downstream CAD and GIS workflows.',
  },
  {
    icon: Ruler,
    title: 'Volume, area, distance',
    body: 'Stockpile and cut/fill volumes computed against the DSM, not estimated.',
  },
  {
    icon: Users,
    title: 'Team workspaces',
    body: 'Organizations, invitations, and shared presets — isolated per tenant.',
  },
]

const testimonials = [
  {
    name: 'Budi Santoso',
    role: 'Survey Manager',
    company: 'PT Geospasial Nusantara',
    quote: 'G20 Tech transformed our drone mapping workflow. What used to take days now takes hours.',
    initials: 'BS',
  },
  {
    name: 'Rina Wijaya',
    role: 'Project Engineer',
    company: 'Konstruksi Maju',
    quote: 'The volume calculations are incredibly accurate. We trust the DSM-backed results for our cut/fill estimates.',
    initials: 'RW',
  },
  {
    name: 'Ahmad Fauzi',
    role: 'GIS Analyst',
    company: 'DataBumi Solutions',
    quote: 'Multi-tenant workspaces mean our clients each get their own isolated environment. Game changer.',
    initials: 'AF',
  },
]

const plans = [
  {
    name: 'Starter',
    description: 'For individual pilots and small projects.',
    price: 'Rp 1.700.000',
    period: 'month',
    featured: false,
    features: [
      '10 GB storage',
      '100 processing credits/month',
      '~2 Gigapixels capacity',
      'Orthophoto & DSM export',
      'Email support (5 business days)',
    ],
  },
  {
    name: 'Standard',
    description: 'For teams and regular mapping operations.',
    price: 'Rp 8.500.000',
    period: 'month',
    featured: true,
    features: [
      '100 GB storage',
      '600 processing credits/month',
      '~12 Gigapixels capacity',
      'Change detection analytics',
      'Map sharing & collaboration',
      'Email support (2 business days)',
    ],
  },
  {
    name: 'Enterprise',
    description: 'For organizations with custom requirements.',
    price: 'Custom',
    period: '',
    featured: false,
    cta: 'Contact us',
    features: [
      'Unlimited storage',
      'Unlimited processing',
      'On-premise deployment',
      'Custom integrations & API',
      'Dedicated GIS expert support',
      'SLA guarantee',
      'User access control',
    ],
  },
]

const year = new Date().getFullYear()
</script>

<template>
  <div class="min-h-screen bg-slate-950 font-sans text-slate-100 selection:bg-cyan-500 selection:text-slate-950">
    <!-- Nav -->
    <nav class="fixed top-0 z-50 w-full border-b border-slate-800/80 bg-slate-950/85 backdrop-blur-xl">
      <div class="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <router-link to="/" class="group flex items-center gap-3">
          <span
            class="flex size-9 items-center justify-center rounded-xl border border-cyan-400/40 bg-gradient-to-tr from-cyan-600 to-blue-600 text-slate-950 shadow-md shadow-cyan-500/20 transition-transform group-hover:scale-105"
          >
            <MapIcon class="size-5" />
          </span>
          <span class="flex flex-col leading-none">
            <span class="font-display text-lg font-extrabold tracking-tight text-white">
              G20 <span class="text-cyan-400">Tech</span>
            </span>
            <span class="font-mono text-[10px] tracking-wider text-slate-400">
              DRONE DATA AS A SERVICE
            </span>
          </span>
        </router-link>

        <!-- Desktop nav -->
        <div class="hidden items-center gap-1 rounded-full border border-slate-800/80 bg-slate-900/60 p-1.5 backdrop-blur-md md:flex">
          <a href="#pipeline" class="rounded-full px-3.5 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800/60 hover:text-white">How it works</a>
          <a href="#capabilities" class="rounded-full px-3.5 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800/60 hover:text-white">Capabilities</a>
          <a href="#pricing" class="rounded-full px-3.5 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800/60 hover:text-white">Pricing</a>
          <router-link to="/about" class="rounded-full px-3.5 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800/60 hover:text-white">About</router-link>
        </div>

        <div class="hidden items-center gap-3 md:flex">
          <router-link
            to="/login"
            class="rounded-xl border border-slate-700/80 px-4 py-2 text-xs font-semibold text-slate-200 transition-colors hover:border-slate-600 hover:text-white"
          >
            Sign in
          </router-link>
          <router-link
            to="/login"
            class="rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 px-5 py-2.5 text-xs font-semibold tracking-wide text-slate-950 shadow-lg shadow-cyan-500/25 transition-all hover:-translate-y-0.5 hover:from-cyan-400 hover:to-blue-500"
          >
            Get started
          </router-link>
        </div>

        <!-- Mobile menu button -->
        <button
          type="button"
          class="rounded-lg border border-slate-800 bg-slate-900 p-2 text-slate-300 hover:text-white md:hidden"
          :aria-expanded="mobileMenuOpen"
          aria-label="Toggle navigation menu"
          @click="mobileMenuOpen = !mobileMenuOpen"
        >
          <Menu v-if="!mobileMenuOpen" class="size-5" />
          <X v-else class="size-5" />
        </button>
      </div>

      <!-- Mobile menu -->
      <div v-if="mobileMenuOpen" class="border-t border-slate-800 bg-slate-950/95 px-6 py-4 backdrop-blur-2xl md:hidden">
        <div class="flex flex-col gap-3">
          <a href="#pipeline" class="rounded-xl p-3 text-sm font-medium text-slate-200 hover:bg-slate-900" @click="mobileMenuOpen = false">How it works</a>
          <a href="#capabilities" class="rounded-xl p-3 text-sm font-medium text-slate-200 hover:bg-slate-900" @click="mobileMenuOpen = false">Capabilities</a>
          <a href="#pricing" class="rounded-xl p-3 text-sm font-medium text-slate-200 hover:bg-slate-900" @click="mobileMenuOpen = false">Pricing</a>
          <router-link to="/about" class="rounded-xl p-3 text-sm font-medium text-slate-200 hover:bg-slate-900" @click="mobileMenuOpen = false">About</router-link>
          <router-link
            to="/login"
            class="rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 py-3 text-center text-sm font-bold text-slate-950"
            @click="mobileMenuOpen = false"
          >
            Get started
          </router-link>
        </div>
      </div>
    </nav>

    <!-- Hero -->
    <section class="relative overflow-hidden pb-16 pt-28 md:pb-24 md:pt-36">
      <div class="pointer-events-none absolute inset-0">
        <div class="absolute left-1/2 top-1/4 h-[350px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-cyan-600/10 blur-[140px]" />
        <div class="absolute right-10 top-1/3 h-[300px] w-[400px] rounded-full bg-blue-600/10 blur-[120px]" />
        <div class="absolute bottom-10 left-4 hidden size-44 rounded-full border border-cyan-500/20 lg:block">
          <div
            class="absolute inset-0 animate-radar-sweep rounded-full"
            style="background: conic-gradient(from 0deg, rgba(34, 211, 238, 0.28), transparent 38%)"
          />
        </div>
        <div
          class="absolute right-1/4 top-1/2 hidden size-72 -translate-y-1/2 rounded-full border border-cyan-500/10 animate-pulse-ring lg:block"
        />
      </div>

      <div class="relative z-10 mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="grid grid-cols-1 items-center gap-12 lg:grid-cols-12 lg:gap-8">
          <!-- Copy -->
          <div class="space-y-6 lg:col-span-6">
            <div class="inline-flex items-center gap-2 rounded-full border border-cyan-700/60 bg-cyan-950/80 px-3 py-1.5 shadow-inner">
              <span class="relative flex h-2 w-2">
                <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-75" />
                <span class="relative inline-flex h-2 w-2 rounded-full bg-cyan-400" />
              </span>
              <span class="font-mono text-xs font-semibold uppercase tracking-wide text-cyan-300">
                Drone data as a service
              </span>
            </div>

            <h1 class="font-display text-4xl font-extrabold leading-[1.12] tracking-tight text-white sm:text-5xl lg:text-6xl">
              Turn Drone Imagery Into
              <span class="bg-gradient-to-r from-cyan-400 via-sky-300 to-blue-500 bg-clip-text text-transparent">
                Actionable Intelligence
              </span>
            </h1>

            <p class="max-w-xl text-base leading-relaxed text-slate-300 sm:text-lg">
              Upload raw drone imagery and get georeferenced orthophotos, elevation models,
              point clouds, and 3D reconstructions — then measure volumes and areas directly
              on the result.
            </p>

            <div class="grid grid-cols-1 gap-2.5 pt-1 text-xs text-slate-300 sm:grid-cols-2">
              <div class="flex items-center gap-2">
                <Check class="size-4 shrink-0 text-cyan-400" />
                <span>Georeferenced outputs in real coordinates</span>
              </div>
              <div class="flex items-center gap-2">
                <Check class="size-4 shrink-0 text-cyan-400" />
                <span>DSM-backed volume measurement</span>
              </div>
              <div class="flex items-center gap-2">
                <Check class="size-4 shrink-0 text-cyan-400" />
                <span>Per-organization workspaces</span>
              </div>
              <div class="flex items-center gap-2">
                <Check class="size-4 shrink-0 text-cyan-400" />
                <span>Browser 3D, no plugin required</span>
              </div>
            </div>

            <div class="flex flex-col items-stretch gap-3.5 pt-4 sm:flex-row sm:items-center">
              <router-link
                to="/login"
                class="group inline-flex items-center justify-center gap-3 rounded-xl bg-gradient-to-r from-cyan-400 via-cyan-500 to-blue-600 px-7 py-4 text-sm font-bold tracking-wide text-slate-950 shadow-xl shadow-cyan-500/25 transition-all hover:-translate-y-0.5 hover:from-cyan-300 hover:to-blue-500 hover:shadow-cyan-500/40"
              >
                <span>Get started free</span>
                <ArrowRight class="size-4 transition-transform group-hover:translate-x-1" />
              </router-link>
              <a
                href="#pipeline"
                class="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-700/80 bg-slate-900/80 px-6 py-4 text-sm font-semibold text-slate-200 transition-all hover:border-slate-600 hover:bg-slate-800 hover:text-white"
              >
                See how it works
              </a>
            </div>

            <div class="flex items-center gap-4 pt-2 font-mono text-xs text-slate-400">
              <div class="flex items-center gap-1.5">
                <ShieldCheck class="size-4 text-emerald-400" />
                <span>Multi-tenant isolation</span>
              </div>
              <span class="text-slate-700">•</span>
              <div class="flex items-center gap-1.5">
                <Radio class="size-4 text-cyan-400" />
                <span>Fully automated pipeline</span>
              </div>
            </div>
          </div>

          <!-- Simulation -->
          <div class="w-full lg:col-span-6">
            <div class="relative">
              <div class="absolute -inset-1 rounded-3xl bg-gradient-to-r from-cyan-500/30 to-blue-500/20 opacity-70 blur-xl" />
              <div class="relative">
                <HeroFlightSim />
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Stats -->
    <section class="border-y border-slate-800/80 bg-slate-900/40">
      <div class="mx-auto grid max-w-5xl gap-8 px-4 py-10 text-center sm:grid-cols-3 sm:px-6">
        <div>
          <p class="font-display text-2xl font-extrabold text-transparent sm:text-3xl bg-gradient-to-r from-cyan-400 to-sky-200 bg-clip-text">2,539 × 2,444</p>
          <p class="mt-1 text-sm text-slate-400">px georeferenced resolution</p>
        </div>
        <div>
          <p class="font-display text-2xl font-extrabold text-transparent sm:text-3xl bg-gradient-to-r from-cyan-400 to-sky-200 bg-clip-text">4-step</p>
          <p class="mt-1 text-sm text-slate-400">fully automated pipeline</p>
        </div>
        <div>
          <p class="font-display text-2xl font-extrabold text-transparent sm:text-3xl bg-gradient-to-r from-cyan-400 to-sky-200 bg-clip-text">Multi-tenant</p>
          <p class="mt-1 text-sm text-slate-400">per-organization isolation</p>
        </div>
      </div>
    </section>

    <!-- Pipeline -->
    <section id="pipeline" class="scroll-mt-24 border-b border-slate-800/80 py-24">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="mx-auto mb-16 max-w-3xl space-y-3 text-center">
          <div class="inline-flex items-center gap-2 rounded-full border border-cyan-800/60 bg-cyan-950/60 px-3 py-1 font-mono text-xs uppercase tracking-wider text-cyan-400">
            How it works
          </div>
          <h2 class="font-display text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
            From flight to insight
          </h2>
          <p class="text-base leading-relaxed text-slate-300">
            Four steps, fully automated between them. You upload; the pipeline does the
            reconstruction; you measure the result.
          </p>
        </div>

        <div class="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
          <div
            v-for="(stage, i) in pipeline"
            :key="stage.step"
            class="flex flex-col rounded-2xl border border-slate-800/90 bg-slate-900/60 p-6 transition-all duration-300 hover:-translate-y-1 hover:border-cyan-500/50 hover:bg-slate-900 hover:shadow-xl hover:shadow-cyan-500/10"
          >
            <div class="mb-4 flex items-center justify-between">
              <span class="flex size-12 items-center justify-center rounded-2xl border border-cyan-500/30 bg-cyan-500/10 text-cyan-400">
                <component :is="stage.icon" class="size-5" />
              </span>
              <span class="font-mono text-2xl font-bold text-cyan-400/80">0{{ i + 1 }}</span>
            </div>
            <h3 class="font-display text-lg font-bold text-white">{{ stage.step }}</h3>
            <p class="mt-2 text-sm leading-relaxed text-slate-300">{{ stage.body }}</p>
          </div>
        </div>

        <div class="mt-10 text-center">
          <a href="#capabilities" class="font-mono text-sm text-cyan-400 hover:underline">
            See it in action →
          </a>
        </div>
      </div>
    </section>

    <!-- Capabilities -->
    <section id="capabilities" class="scroll-mt-24 bg-slate-900/40 py-24">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="mx-auto mb-16 max-w-3xl space-y-3 text-center">
          <div class="inline-flex items-center gap-2 rounded-full border border-cyan-800/60 bg-cyan-950/60 px-3 py-1 font-mono text-xs uppercase tracking-wider text-cyan-400">
            Outputs
          </div>
          <h2 class="font-display text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
            Every output the pipeline produces
          </h2>
        </div>

        <div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <div
            v-for="cap in capabilities"
            :key="cap.title"
            class="rounded-3xl border border-slate-800 bg-slate-950/80 p-6 transition-all duration-300 hover:border-cyan-500/40"
          >
            <component :is="cap.icon" class="size-6 text-cyan-400" />
            <h3 class="mt-4 font-display font-bold text-white">{{ cap.title }}</h3>
            <p class="mt-2 text-sm leading-relaxed text-slate-300">{{ cap.body }}</p>
          </div>
        </div>

        <div class="mt-10 text-center">
          <a href="#pricing" class="font-mono text-sm text-cyan-400 hover:underline">
            View pricing →
          </a>
        </div>
      </div>
    </section>

    <!-- Social proof -->
    <section class="border-y border-slate-800/80 py-24">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="mx-auto mb-16 max-w-3xl space-y-3 text-center">
          <div class="inline-flex items-center gap-2 rounded-full border border-cyan-800/60 bg-cyan-950/60 px-3 py-1 font-mono text-xs uppercase tracking-wider text-cyan-400">
            Verified proof
          </div>
          <h2 class="font-display text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
            Trusted by surveyors, engineers, and mapping teams
          </h2>
        </div>

        <div class="grid grid-cols-1 gap-6 md:grid-cols-3">
          <div
            v-for="testimonial in testimonials"
            :key="testimonial.name"
            class="flex flex-col justify-between rounded-3xl border border-slate-800 bg-slate-900/60 p-6 transition-all duration-300 hover:border-cyan-500/40 sm:p-8"
          >
            <div>
              <div class="mb-6 flex items-center gap-1 text-amber-400">
                <Star v-for="i in 5" :key="i" class="size-4 fill-amber-400 text-amber-400" />
              </div>
              <blockquote class="text-sm italic leading-relaxed text-slate-300">
                "{{ testimonial.quote }}"
              </blockquote>
            </div>
            <div class="mt-6 flex items-center gap-3 border-t border-slate-800/80 pt-4">
              <div class="flex size-10 items-center justify-center rounded-full border border-cyan-500/40 bg-cyan-500/10 text-sm font-semibold text-cyan-300">
                {{ testimonial.initials }}
              </div>
              <div>
                <p class="font-display text-sm font-bold text-white">{{ testimonial.name }}</p>
                <p class="text-xs leading-tight text-slate-400">
                  {{ testimonial.role }},
                  <span class="font-mono text-cyan-400">{{ testimonial.company }}</span>
                </p>
              </div>
            </div>
          </div>
        </div>

        <!-- Logo strip -->
        <div class="mt-14 flex flex-wrap items-center justify-center gap-8 opacity-60">
          <span class="font-mono text-sm text-slate-400">PT Geospasial Nusantara</span>
          <span class="font-mono text-sm text-slate-400">Konstruksi Maju</span>
          <span class="font-mono text-sm text-slate-400">DataBumi Solutions</span>
          <span class="font-mono text-sm text-slate-400">Survey Mandiri</span>
        </div>
      </div>
    </section>

    <!-- Pricing -->
    <section id="pricing" class="scroll-mt-24 py-24">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="mx-auto mb-16 max-w-3xl space-y-3 text-center">
          <div class="inline-flex items-center gap-2 rounded-full border border-cyan-800/60 bg-cyan-950/60 px-3 py-1 font-mono text-xs uppercase tracking-wider text-cyan-400">
            Pricing
          </div>
          <h2 class="font-display text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
            Simple, transparent pricing
          </h2>
          <p class="text-base leading-relaxed text-slate-300">
            Every plan includes the full processing pipeline. Scale storage and credits as
            your operation grows.
          </p>
        </div>

        <div class="mx-auto grid max-w-5xl gap-6 lg:grid-cols-3">
          <div
            v-for="plan in plans"
            :key="plan.name"
            class="relative flex flex-col rounded-3xl border bg-slate-900/60 p-8"
            :class="plan.featured ? 'border-cyan-500/60 shadow-xl shadow-cyan-500/10' : 'border-slate-800'"
          >
            <span
              v-if="plan.featured"
              class="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full border border-cyan-400/50 bg-cyan-500 px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-wider text-slate-950"
            >
              Most popular
            </span>
            <h3 class="font-display text-lg font-bold text-white">{{ plan.name }}</h3>
            <p class="mt-2 text-sm text-slate-400">{{ plan.description }}</p>
            <div class="mb-8 mt-6">
              <span class="font-display text-3xl font-extrabold tracking-tight text-white">
                {{ plan.price }}
              </span>
              <span v-if="plan.period" class="text-sm text-slate-400">/{{ plan.period }}</span>
            </div>
            <ul class="mb-8 flex-1 space-y-3">
              <li
                v-for="feature in plan.features"
                :key="feature"
                class="flex items-start gap-2 text-sm text-slate-300"
              >
                <Check class="mt-0.5 size-4 shrink-0 text-cyan-400" />
                {{ feature }}
              </li>
            </ul>
            <router-link v-if="plan.cta" to="/contact" class="block">
              <span class="block w-full rounded-xl border border-slate-700/80 bg-slate-800 py-3 text-center text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-700">
                {{ plan.cta }}
              </span>
            </router-link>
            <router-link v-else to="/login" class="block">
              <span
                class="block w-full rounded-xl py-3 text-center text-sm font-semibold transition-all"
                :class="
                  plan.featured
                    ? 'bg-gradient-to-r from-cyan-400 to-blue-600 text-slate-950 shadow-lg shadow-cyan-500/25 hover:-translate-y-0.5'
                    : 'border border-slate-700/80 bg-slate-800 text-slate-200 hover:bg-slate-700'
                "
              >
                Get started
              </span>
            </router-link>
          </div>
        </div>

        <p class="mt-8 text-center text-sm text-slate-400">
          Have questions?
          <router-link to="/contact" class="text-cyan-400 hover:underline">Contact us</router-link>
        </p>
      </div>
    </section>

    <!-- Final CTA -->
    <section class="py-24">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div
          class="relative overflow-hidden rounded-3xl border border-cyan-500/40 bg-gradient-to-r from-slate-900 via-cyan-950/40 to-slate-900 p-8 shadow-2xl sm:p-14"
        >
          <div class="pointer-events-none absolute -right-10 -top-10 h-80 w-80 rounded-full bg-cyan-500/10 blur-3xl" />
          <div class="pointer-events-none absolute -bottom-10 -left-10 h-80 w-80 rounded-full bg-blue-600/10 blur-3xl" />
          <div class="relative z-10 mx-auto max-w-3xl space-y-5 text-center">
            <h2 class="font-display text-3xl font-extrabold tracking-tight text-white sm:text-4xl">
              Ready to transform your drone data?
            </h2>
            <p class="text-slate-300">
              Create an organization, upload a mission, and measure the result.
            </p>
            <div class="flex flex-col items-center justify-center gap-4 pt-2 sm:flex-row">
              <router-link
                to="/login"
                class="inline-flex w-full items-center justify-center gap-3 rounded-xl bg-gradient-to-r from-cyan-400 via-cyan-500 to-blue-600 px-8 py-4 text-sm font-bold tracking-wide text-slate-950 shadow-xl shadow-cyan-500/30 transition-all hover:-translate-y-0.5 hover:from-cyan-300 hover:to-blue-500 sm:w-auto"
              >
                <span>Get started free</span>
                <ArrowRight class="size-4" />
              </router-link>
              <router-link
                to="/contact"
                class="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-slate-700/80 bg-slate-900/90 px-7 py-4 text-sm font-semibold text-slate-200 transition-all hover:bg-slate-800 hover:text-white sm:w-auto"
              >
                Contact sales
              </router-link>
            </div>
            <div class="flex flex-wrap items-center justify-center gap-4 pt-2 font-mono text-xs text-slate-400">
              <span class="flex items-center gap-1.5">
                <Check class="size-4 text-cyan-400" />
                No credit card required
              </span>
              <span class="flex items-center gap-1.5">
                <Check class="size-4 text-cyan-400" />
                Free trial
              </span>
              <span class="flex items-center gap-1.5">
                <Check class="size-4 text-cyan-400" />
                Cancel anytime
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-slate-800/80 bg-slate-950 py-12 text-xs text-slate-400">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          <!-- Brand -->
          <div>
            <router-link to="/" class="flex items-center gap-3">
              <span class="flex size-8 items-center justify-center rounded-xl border border-cyan-400/40 bg-gradient-to-tr from-cyan-600 to-blue-600 text-slate-950">
                <MapIcon class="size-4" />
              </span>
              <span class="font-display text-lg font-extrabold tracking-tight text-white">
                G20 <span class="text-cyan-400">Tech</span>
              </span>
            </router-link>
            <p class="mt-3 max-w-xs leading-relaxed text-slate-400">
              Drone data processing and GIS solutions for modern surveying teams.
            </p>
          </div>

          <!-- Product -->
          <div>
            <h4 class="mb-3 font-mono text-xs uppercase tracking-wider text-slate-200">Product</h4>
            <ul class="space-y-2">
              <li><a href="#capabilities" class="transition-colors hover:text-cyan-400">Features</a></li>
              <li><a href="#pricing" class="transition-colors hover:text-cyan-400">Pricing</a></li>
              <li><span class="text-slate-500">API Docs</span></li>
              <li><span class="text-slate-500">Changelog</span></li>
            </ul>
          </div>

          <!-- Company -->
          <div>
            <h4 class="mb-3 font-mono text-xs uppercase tracking-wider text-slate-200">Company</h4>
            <ul class="space-y-2">
              <li><router-link to="/about" class="transition-colors hover:text-cyan-400">About</router-link></li>
              <li><span class="text-slate-500">Careers</span></li>
              <li><span class="text-slate-500">Blog</span></li>
              <li><router-link to="/contact" class="transition-colors hover:text-cyan-400">Contact</router-link></li>
            </ul>
          </div>

          <!-- Legal -->
          <div>
            <h4 class="mb-3 font-mono text-xs uppercase tracking-wider text-slate-200">Legal</h4>
            <ul class="space-y-2">
              <li><span class="text-slate-500">Privacy Policy</span></li>
              <li><span class="text-slate-500">Terms of Service</span></li>
              <li><span class="text-slate-500">Cookie Policy</span></li>
            </ul>
          </div>
        </div>

        <div class="mt-8 border-t border-slate-800/80 pt-8 text-center font-mono text-slate-500">
          &copy; {{ year }} G20 Tech. All rights reserved.
        </div>
      </div>
    </footer>
  </div>
</template>
