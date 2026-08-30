<script setup>
import { onMounted } from 'vue'
import { Button } from '@/components/ui'
import {
  ArrowRight,
  Camera,
  Cpu,
  Database,
  Globe,
  Layers,
  Map as MapIcon,
  Server,
  Users,
  Zap,
} from 'lucide-vue-next'

const team = [
  { name: 'Team Member', role: 'Founder & CEO', initials: 'TM' },
  { name: 'Team Member', role: 'CTO', initials: 'TM' },
  { name: 'Team Member', role: 'Lead Engineer', initials: 'TM' },
  { name: 'Team Member', role: 'GIS Specialist', initials: 'TM' },
]

const industries = [
  {
    icon: MapIcon,
    title: 'Survey & Mapping',
    body: 'High-precision orthophotos and topographic maps for land surveying, cadastral mapping, and site planning.',
  },
  {
    icon: Layers,
    title: 'Construction',
    body: 'Progress monitoring, stockpile volume calculations, and cut/fill analysis for earthworks.',
  },
  {
    icon: Globe,
    title: 'Agriculture',
    body: 'Crop health analysis, field boundaries, and irrigation planning with multispectral imagery.',
  },
  {
    icon: Database,
    title: 'Mining',
    body: 'Volumetric analysis of stockpiles, pit mapping, and haul road monitoring.',
  },
  {
    icon: Camera,
    title: 'Environmental',
    body: 'Coastal erosion monitoring, deforestation tracking, and environmental impact assessment.',
  },
  {
    icon: Users,
    title: 'Infrastructure',
    body: 'Bridge inspections, road surveys, and asset management with high-resolution 3D models.',
  },
]

const techStack = [
  { icon: Cpu, name: 'OpenDroneMap', desc: 'Open-source photogrammetry pipeline' },
  { icon: Database, name: 'PostGIS', desc: 'Geospatial database with spatial queries' },
  { icon: Server, name: 'Cloud Processing', desc: 'Scalable NodeODM worker fleet' },
  { icon: Zap, name: 'Real-time', desc: 'WebSocket progress and live updates' },
]

const year = new Date().getFullYear()
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
          <router-link to="/about" class="text-sm text-foreground transition-colors">About</router-link>
          <router-link to="/contact" class="text-sm text-muted-foreground transition-colors hover:text-foreground">Contact</router-link>
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
        style="background-image: radial-gradient(circle at 30% 30%, rgb(37 99 235 / 0.5), transparent 55%)"
      />
      <div class="relative mx-auto max-w-4xl px-6 py-20 text-center lg:py-28">
        <h1 class="text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
          About <span class="text-primary">G20 Tech</span>
        </h1>
        <p class="mx-auto mt-5 max-w-2xl text-lg text-slate-300">
          We're building the infrastructure for drone data processing — making
          geospatial intelligence accessible to surveyors, engineers, and mapping
          teams across Indonesia and beyond.
        </p>
      </div>
    </section>

    <!-- Our Story -->
    <section class="border-b border-border py-20">
      <div class="mx-auto max-w-4xl px-6">
        <h2 class="text-3xl font-semibold tracking-tight text-foreground">Our Story</h2>
        <div class="mt-8 space-y-4 text-muted-foreground">
          <p>
            G20 Tech was founded with a simple observation: drone technology has
            democratized aerial data collection, but the processing pipeline
            remains complex, expensive, and inaccessible to many teams.
          </p>
          <p>
            We set out to change that. By combining open-source photogrammetry
            (OpenDroneMap) with modern cloud infrastructure and a multi-tenant
            SaaS platform, we've made it possible for any team to upload drone
            imagery and get professional-grade outputs — orthophotos, elevation
            models, 3D reconstructions — without the technical overhead.
          </p>
          <p>
            Our mission is to bridge the gap between raw drone data and actionable
            insights, enabling better decisions in surveying, construction,
            agriculture, and environmental monitoring.
          </p>
        </div>
      </div>
    </section>

    <!-- What We Do -->
    <section class="bg-muted/40 py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          IT Solutions Across Industries
        </h2>
        <p class="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">
          Beyond drone mapping, we provide comprehensive IT solutions tailored
          to various sectors.
        </p>
        <div class="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <div
            v-for="industry in industries"
            :key="industry.title"
            class="rounded-lg border border-border bg-card p-6 transition-transform hover:-translate-y-0.5"
          >
            <component :is="industry.icon" class="size-6 text-primary" />
            <h3 class="mt-4 font-medium text-card-foreground">{{ industry.title }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ industry.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Technology -->
    <section class="py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          Built on Proven Technology
        </h2>
        <p class="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">
          Our platform leverages industry-standard open-source tools and modern
          cloud infrastructure.
        </p>
        <div class="mx-auto mt-14 grid max-w-4xl gap-6 sm:grid-cols-2">
          <div
            v-for="tech in techStack"
            :key="tech.name"
            class="flex items-start gap-4 rounded-lg border border-border bg-card p-6"
          >
            <div class="flex size-10 items-center justify-center rounded-lg bg-primary/10">
              <component :is="tech.icon" class="size-5 text-primary" />
            </div>
            <div>
              <h3 class="font-medium text-card-foreground">{{ tech.name }}</h3>
              <p class="mt-1 text-sm text-muted-foreground">{{ tech.desc }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Team -->
    <section class="border-y border-border bg-muted/40 py-20">
      <div class="mx-auto max-w-4xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          Meet the Team
        </h2>
        <div class="mt-14 grid grid-cols-2 gap-6 sm:grid-cols-4">
          <div
            v-for="member in team"
            :key="member.name"
            class="text-center"
          >
            <div class="mx-auto flex size-20 items-center justify-center rounded-full bg-primary/10 text-xl font-semibold text-primary">
              {{ member.initials }}
            </div>
            <h3 class="mt-4 font-medium text-card-foreground">{{ member.name }}</h3>
            <p class="text-sm text-muted-foreground">{{ member.role }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- CTA -->
    <section class="bg-slate-950 py-20 text-slate-100">
      <div class="mx-auto max-w-3xl px-6 text-center">
        <h2 class="text-3xl font-semibold tracking-tight">Work with us</h2>
        <p class="mt-4 text-slate-300">
          Whether you're looking for a mapping platform or custom GIS solutions,
          we'd love to hear from you.
        </p>
        <div class="mt-8 flex flex-wrap items-center justify-center gap-3">
          <router-link to="/contact">
            <Button size="lg">
              Get in touch
              <ArrowRight />
            </Button>
          </router-link>
          <router-link to="/login">
            <Button size="lg" variant="outline" class="border-slate-700 bg-transparent text-slate-100 hover:bg-slate-900">
              Try the platform
            </Button>
          </router-link>
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
          &copy; {{ year }} G20 Tech. All rights reserved.
        </div>
      </div>
    </footer>
  </div>
</template>
