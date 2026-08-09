<script setup>
import { Badge, Button } from '@/components/ui'
import {
  ArrowRight,
  Box,
  Camera,
  Check,
  Cpu,
  Layers,
  Map as MapIcon,
  Mountain,
  Ruler,
  Share2,
  Users,
} from 'lucide-vue-next'

// The four-step arc from raw imagery to a shareable, measurable result. This is
// the technical-credibility beat: it mirrors the real pipeline, not a metaphor.
const pipeline = [
  {
    icon: Camera,
    step: 'Capture',
    body: 'Fly your mission and upload the raw images. GPS EXIF is preserved end to end, so outputs land in real-world coordinates.',
  },
  {
    icon: Cpu,
    step: 'Process',
    body: 'An OpenDroneMap pipeline reconstructs the scene — structure from motion, dense point cloud, mesh, texture.',
  },
  {
    icon: Ruler,
    step: 'Measure',
    body: 'Draw on the map to get distance, area, and DSM-backed volume. Answers, not just pictures.',
  },
  {
    icon: Share2,
    step: 'Share',
    body: 'Give your team one workspace with per-organization projects, presets, and access.',
  },
]

const capabilities = [
  {
    icon: MapIcon,
    title: 'Georeferenced orthophotos',
    body: 'Cloud-optimized GeoTIFFs served as map tiles, aligned to real coordinates.',
  },
  {
    icon: Mountain,
    title: 'DSM & DTM',
    body: 'Digital surface and terrain models — the elevation basis for volume math.',
  },
  {
    icon: Box,
    title: 'Textured 3D models',
    body: 'Browser-viewable reconstructions. No plugin, no desktop install.',
  },
  {
    icon: Layers,
    title: 'Dense point clouds',
    body: 'Export the full reconstruction for downstream CAD and GIS work.',
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
    name: 'Advanced',
    description: 'For enterprises with large-scale needs.',
    price: 'Rp 34.000.000',
    period: 'month',
    featured: false,
    features: [
      '600 GB storage',
      '3,000 processing credits/month',
      'API access & integrations',
      'User access control',
      'On-premise deployment',
      'Dedicated GIS expert support',
      'SLA guarantee',
    ],
  },
]

const year = new Date().getFullYear()
</script>

<template>
  <div class="min-h-screen bg-background">
    <!-- Nav -->
    <nav class="fixed top-0 z-50 w-full border-b border-border bg-background/80 backdrop-blur-md">
      <div class="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <router-link to="/" class="flex items-center gap-2">
          <MapIcon class="size-5 text-primary" />
          <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
        </router-link>
        <div class="flex items-center gap-6">
          <a href="#pipeline" class="hidden text-sm text-muted-foreground transition-colors hover:text-foreground sm:block">How it works</a>
          <a href="#capabilities" class="hidden text-sm text-muted-foreground transition-colors hover:text-foreground sm:block">Capabilities</a>
          <a href="#pricing" class="hidden text-sm text-muted-foreground transition-colors hover:text-foreground sm:block">Pricing</a>
          <router-link to="/login">
            <Button size="sm">Sign in</Button>
          </router-link>
        </div>
      </div>
    </nav>

    <!-- Hero: dark, high-contrast, split. Leads with the product, not a photo. -->
    <section class="relative overflow-hidden bg-slate-950 pt-14 text-slate-100">
      <div
        class="pointer-events-none absolute inset-0 opacity-30"
        style="background-image: radial-gradient(circle at 20% 20%, rgb(37 99 235 / 0.5), transparent 55%), radial-gradient(circle at 80% 60%, rgb(37 99 235 / 0.25), transparent 50%)"
      />
      <div class="relative mx-auto grid max-w-6xl items-center gap-12 px-6 py-20 lg:grid-cols-2 lg:py-28">
        <div class="animate-slide-up">
          <Badge variant="outline" class="border-slate-700 text-slate-300">
            Drone data as a service
          </Badge>
          <h1 class="mt-5 text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            From flight to
            <span class="text-primary">measurable answers</span>
          </h1>
          <p class="mt-5 max-w-xl text-lg text-slate-300">
            Upload raw drone imagery and get georeferenced orthophotos, elevation
            models, point clouds, and 3D reconstructions — then measure volumes
            and areas directly on the result.
          </p>
          <div class="mt-8 flex flex-wrap items-center gap-3">
            <router-link to="/login">
              <Button size="lg">
                Get started
                <ArrowRight />
              </Button>
            </router-link>
            <a href="#pipeline">
              <Button size="lg" variant="outline" class="border-slate-700 bg-transparent text-slate-100 hover:bg-slate-900">
                See how it works
              </Button>
            </a>
          </div>
        </div>

        <!-- Product panel: a mock of the actual map view, with a slow tile-load
             reveal. Deliberately not a stock aerial photo. -->
        <div class="animate-fade-in">
          <div class="overflow-hidden rounded-xl border border-slate-800 bg-slate-900 shadow-2xl">
            <div class="flex items-center gap-2 border-b border-slate-800 px-4 py-2.5">
              <span class="size-2.5 rounded-full bg-slate-700" />
              <span class="size-2.5 rounded-full bg-slate-700" />
              <span class="size-2.5 rounded-full bg-slate-700" />
              <span class="ml-2 text-xs text-slate-500">orthophoto · EPSG:32615</span>
            </div>
            <div class="relative aspect-[4/3]">
              <img
                src="/images/background.png"
                alt="Georeferenced orthophoto rendered as map tiles"
                class="size-full object-cover"
              />
              <div class="absolute inset-x-4 bottom-4 rounded-lg border border-slate-700 bg-slate-950/80 px-4 py-3 backdrop-blur">
                <p class="text-xs uppercase tracking-wide text-slate-500">Measured area</p>
                <p class="mt-0.5 font-mono text-lg text-primary">2,539 × 2,444 px</p>
                <p class="text-xs text-slate-400">georeferenced output resolution</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Pipeline strip -->
    <section id="pipeline" class="border-b border-border py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          From flight to insight
        </h2>
        <p class="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">
          Four steps, fully automated between them. You upload; the pipeline does
          the reconstruction; you measure the result.
        </p>
        <ol class="mt-14 grid gap-6 md:grid-cols-4">
          <li
            v-for="(stage, i) in pipeline"
            :key="stage.step"
            class="relative rounded-lg border border-border bg-card p-6"
          >
            <span class="font-mono text-xs text-muted-foreground">0{{ i + 1 }}</span>
            <component :is="stage.icon" class="mt-3 size-6 text-primary" />
            <h3 class="mt-4 font-medium text-card-foreground">{{ stage.step }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ stage.body }}</p>
          </li>
        </ol>
      </div>
    </section>

    <!-- Capabilities -->
    <section id="capabilities" class="bg-muted/40 py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          Every output the pipeline produces
        </h2>
        <div class="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <div
            v-for="cap in capabilities"
            :key="cap.title"
            class="rounded-lg border border-border bg-card p-6 transition-transform hover:-translate-y-0.5"
          >
            <component :is="cap.icon" class="size-6 text-primary" />
            <h3 class="mt-4 font-medium text-card-foreground">{{ cap.title }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ cap.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Outcome band.
         GLOBAL CONSTRAINT 7: only the orthophoto resolution is verifiable from
         this codebase. The other two figures are placeholders the user must fill
         in with real numbers — do NOT invent values for them. -->
    <section class="border-y border-border py-16">
      <div class="mx-auto grid max-w-5xl gap-8 px-6 text-center sm:grid-cols-3">
        <div>
          <p class="font-mono text-3xl font-semibold text-foreground">2,539 × 2,444</p>
          <p class="mt-1 text-sm text-muted-foreground">
            px georeferenced orthophoto, from 18 source images
          </p>
        </div>
        <div>
          <p class="font-mono text-3xl font-semibold text-foreground">TODO(user)</p>
          <p class="mt-1 text-sm text-muted-foreground">
            typical turnaround per mission — fill in a real measured figure
          </p>
        </div>
        <div>
          <p class="font-mono text-3xl font-semibold text-foreground">TODO(user)</p>
          <p class="mt-1 text-sm text-muted-foreground">
            hectares processed to date — fill in a real figure
          </p>
        </div>
      </div>
    </section>

    <!-- Pricing -->
    <section id="pricing" class="py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          Simple, transparent pricing
        </h2>
        <p class="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">
          Every plan includes the full processing pipeline. Scale storage and
          credits as your operation grows.
        </p>
        <div class="mx-auto mt-14 grid max-w-5xl gap-6 lg:grid-cols-3">
          <div
            v-for="plan in plans"
            :key="plan.name"
            class="relative flex flex-col rounded-lg border bg-card p-8"
            :class="plan.featured ? 'border-primary ring-1 ring-primary' : 'border-border'"
          >
            <Badge
              v-if="plan.featured"
              variant="default"
              class="absolute -top-3 left-1/2 -translate-x-1/2 bg-primary text-primary-foreground"
            >
              Most popular
            </Badge>
            <h3 class="text-lg font-medium text-card-foreground">{{ plan.name }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ plan.description }}</p>
            <div class="mt-6 mb-8">
              <span class="text-3xl font-semibold tracking-tight text-card-foreground">
                {{ plan.price }}
              </span>
              <span class="text-sm text-muted-foreground">/{{ plan.period }}</span>
            </div>
            <ul class="mb-8 flex-1 space-y-3">
              <li
                v-for="feature in plan.features"
                :key="feature"
                class="flex items-start gap-2 text-sm text-muted-foreground"
              >
                <Check class="mt-0.5 size-4 flex-shrink-0 text-primary" />
                {{ feature }}
              </li>
            </ul>
            <router-link to="/login" class="block">
              <Button class="w-full" :variant="plan.featured ? 'default' : 'secondary'">
                Get started
              </Button>
            </router-link>
          </div>
        </div>
      </div>
    </section>

    <!-- Final CTA -->
    <section class="bg-slate-950 py-20 text-slate-100">
      <div class="mx-auto max-w-3xl px-6 text-center">
        <h2 class="text-3xl font-semibold tracking-tight">Put your imagery to work</h2>
        <p class="mt-4 text-slate-300">
          Create an organization, upload a mission, and measure the result.
        </p>
        <router-link to="/login" class="mt-8 inline-block">
          <Button size="lg">
            Get started
            <ArrowRight />
          </Button>
        </router-link>
      </div>
    </section>

    <footer class="border-t border-border py-8">
      <div class="mx-auto max-w-6xl px-6 text-center text-sm text-muted-foreground">
        &copy; {{ year }} G20 Tech. All rights reserved.
      </div>
    </footer>
  </div>
</template>
