<script setup>
import { ref, onMounted } from 'vue'
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
  Star,
  Users,
  Menu,
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

const observer = ref(null)

onMounted(() => {
  observer.value = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('animate-in')
        }
      })
    },
    { threshold: 0.1 }
  )
  document.querySelectorAll('.fade-up').forEach((el) => {
    observer.value.observe(el)
  })
})
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

        <!-- Desktop nav -->
        <div class="hidden items-center gap-6 md:flex">
          <a href="#pipeline" class="text-sm text-muted-foreground transition-colors hover:text-foreground">How it works</a>
          <a href="#capabilities" class="text-sm text-muted-foreground transition-colors hover:text-foreground">Capabilities</a>
          <a href="#pricing" class="text-sm text-muted-foreground transition-colors hover:text-foreground">Pricing</a>
          <router-link to="/about" class="text-sm text-muted-foreground transition-colors hover:text-foreground">About</router-link>
          <router-link to="/login">
            <Button size="sm" variant="ghost">Sign in</Button>
          </router-link>
          <router-link to="/login">
            <Button size="sm">Get started</Button>
          </router-link>
        </div>

        <!-- Mobile menu button -->
        <button class="md:hidden" @click="mobileMenuOpen = !mobileMenuOpen">
          <Menu v-if="!mobileMenuOpen" class="size-5" />
          <X v-else class="size-5" />
        </button>
      </div>

      <!-- Mobile menu -->
      <div v-if="mobileMenuOpen" class="border-t border-border bg-background px-6 py-4 md:hidden">
        <div class="flex flex-col gap-4">
          <a href="#pipeline" class="text-sm text-muted-foreground" @click="mobileMenuOpen = false">How it works</a>
          <a href="#capabilities" class="text-sm text-muted-foreground" @click="mobileMenuOpen = false">Capabilities</a>
          <a href="#pricing" class="text-sm text-muted-foreground" @click="mobileMenuOpen = false">Pricing</a>
          <router-link to="/about" class="text-sm text-muted-foreground" @click="mobileMenuOpen = false">About</router-link>
          <router-link to="/login">
            <Button size="sm" class="w-full">Get started</Button>
          </router-link>
        </div>
      </div>
    </nav>

    <!-- Hero -->
    <section class="relative overflow-hidden bg-slate-950 pt-16 text-slate-100">
      <div
        class="pointer-events-none absolute inset-0 opacity-30"
        style="background-image: radial-gradient(circle at 20% 20%, rgb(37 99 235 / 0.5), transparent 55%), radial-gradient(circle at 80% 60%, rgb(37 99 235 / 0.25), transparent 50%)"
      />
      <div class="relative mx-auto grid max-w-6xl items-center gap-12 px-6 py-20 lg:grid-cols-2 lg:py-28">
        <div class="fade-up">
          <Badge variant="outline" class="border-slate-700 text-slate-300">
            Drone data as a service
          </Badge>
          <h1 class="mt-5 text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            Turn Drone Imagery Into
            <span class="text-primary">Actionable Intelligence</span>
          </h1>
          <p class="mt-5 max-w-xl text-lg text-slate-300">
            Upload raw drone imagery and get georeferenced orthophotos, elevation
            models, point clouds, and 3D reconstructions — then measure volumes
            and areas directly on the result.
          </p>
          <div class="mt-8 flex flex-wrap items-center gap-3">
            <router-link to="/login">
              <Button size="lg">
                Get started free
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

        <div class="fade-up">
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

      <!-- Stats row -->
      <div class="border-t border-slate-800 bg-slate-900/50">
        <div class="mx-auto grid max-w-5xl gap-8 px-6 py-8 text-center sm:grid-cols-3">
          <div>
            <p class="font-mono text-2xl font-semibold text-primary">2,539 × 2,444</p>
            <p class="mt-1 text-sm text-slate-400">px georeferenced resolution</p>
          </div>
          <div>
            <p class="font-mono text-2xl font-semibold text-primary">4-step</p>
            <p class="mt-1 text-sm text-slate-400">fully automated pipeline</p>
          </div>
          <div>
            <p class="font-mono text-2xl font-semibold text-primary">Multi-tenant</p>
            <p class="mt-1 text-sm text-slate-400">per-organization isolation</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Pipeline -->
    <section id="pipeline" class="border-b border-border py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          From flight to insight
        </h2>
        <p class="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">
          Four steps, fully automated between them. You upload; the pipeline does
          the reconstruction; you measure the result.
        </p>
        <div class="relative mt-14">
          <!-- Connecting line -->
          <div class="absolute left-0 right-0 top-6 hidden h-0.5 bg-border md:block" />
          <ol class="relative grid gap-6 md:grid-cols-4">
            <li
              v-for="(stage, i) in pipeline"
              :key="stage.step"
              class="fade-up relative rounded-lg border border-border bg-card p-6"
            >
              <div class="mb-4 flex items-center gap-3">
                <span class="relative z-10 flex size-12 items-center justify-center rounded-full border border-border bg-background">
                  <component :is="stage.icon" class="size-5 text-primary" />
                </span>
                <span class="font-mono text-xs text-muted-foreground">0{{ i + 1 }}</span>
              </div>
              <h3 class="font-medium text-card-foreground">{{ stage.step }}</h3>
              <p class="mt-2 text-sm text-muted-foreground">{{ stage.body }}</p>
            </li>
          </ol>
        </div>
        <div class="mt-10 text-center">
          <a href="#capabilities" class="text-sm text-primary hover:underline">
            See it in action →
          </a>
        </div>
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
            class="fade-up rounded-lg border border-border bg-card p-6 transition-transform hover:-translate-y-0.5"
          >
            <component :is="cap.icon" class="size-6 text-primary" />
            <h3 class="mt-4 font-medium text-card-foreground">{{ cap.title }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ cap.body }}</p>
          </div>
        </div>
        <div class="mt-10 text-center">
          <a href="#pricing" class="text-sm text-primary hover:underline">
            View all outputs →
          </a>
        </div>
      </div>
    </section>

    <!-- Social Proof -->
    <section class="border-y border-border py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          Trusted by surveyors, engineers, and mapping teams
        </h2>
        <div class="mt-14 grid gap-6 md:grid-cols-3">
          <div
            v-for="testimonial in testimonials"
            :key="testimonial.name"
            class="fade-up rounded-lg border border-border bg-card p-6"
          >
            <div class="mb-4 flex gap-1">
              <Star v-for="i in 5" :key="i" class="size-4 fill-yellow-400 text-yellow-400" />
            </div>
            <p class="text-sm text-muted-foreground">"{{ testimonial.quote }}"</p>
            <div class="mt-6 flex items-center gap-3">
              <div class="flex size-10 items-center justify-center rounded-full bg-primary/10 text-sm font-medium text-primary">
                {{ testimonial.initials }}
              </div>
              <div>
                <p class="text-sm font-medium text-card-foreground">{{ testimonial.name }}</p>
                <p class="text-xs text-muted-foreground">{{ testimonial.role }}, {{ testimonial.company }}</p>
              </div>
            </div>
          </div>
        </div>

        <!-- Logo strip -->
        <div class="mt-14 flex flex-wrap items-center justify-center gap-8 opacity-50">
          <span class="text-sm text-muted-foreground">PT Geospasial Nusantara</span>
          <span class="text-sm text-muted-foreground">Konstruksi Maju</span>
          <span class="text-sm text-muted-foreground">DataBumi Solutions</span>
          <span class="text-sm text-muted-foreground">Survey Mandiri</span>
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
            class="fade-up relative flex flex-col rounded-lg border bg-card p-8"
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
              <span v-if="plan.period" class="text-sm text-muted-foreground">/{{ plan.period }}</span>
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
            <router-link v-if="plan.cta" to="/contact" class="block">
              <Button class="w-full" variant="secondary">
                {{ plan.cta }}
              </Button>
            </router-link>
            <router-link v-else to="/login" class="block">
              <Button class="w-full" :variant="plan.featured ? 'default' : 'secondary'">
                Get started
              </Button>
            </router-link>
          </div>
        </div>
        <p class="mt-8 text-center text-sm text-muted-foreground">
          Have questions?
          <router-link to="/contact" class="text-primary hover:underline">Contact us</router-link>
        </p>
      </div>
    </section>

    <!-- Final CTA -->
    <section class="bg-slate-950 py-20 text-slate-100">
      <div class="mx-auto max-w-3xl px-6 text-center">
        <h2 class="text-3xl font-semibold tracking-tight">Ready to transform your drone data?</h2>
        <p class="mt-4 text-slate-300">
          Create an organization, upload a mission, and measure the result.
        </p>
        <div class="mt-8 flex flex-wrap items-center justify-center gap-3">
          <router-link to="/login">
            <Button size="lg">
              Get started free
              <ArrowRight />
            </Button>
          </router-link>
          <router-link to="/contact">
            <Button size="lg" variant="outline" class="border-slate-700 bg-transparent text-slate-100 hover:bg-slate-900">
              Contact sales
            </Button>
          </router-link>
        </div>
        <div class="mt-6 flex flex-wrap items-center justify-center gap-4 text-sm text-slate-400">
          <span class="flex items-center gap-1">
            <Check class="size-4 text-primary" />
            No credit card required
          </span>
          <span class="flex items-center gap-1">
            <Check class="size-4 text-primary" />
            Free trial
          </span>
          <span class="flex items-center gap-1">
            <Check class="size-4 text-primary" />
            Cancel anytime
          </span>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="border-t border-border py-12">
      <div class="mx-auto max-w-6xl px-6">
        <div class="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          <!-- Brand -->
          <div>
            <router-link to="/" class="flex items-center gap-2">
              <MapIcon class="size-5 text-primary" />
              <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
            </router-link>
            <p class="mt-3 text-sm text-muted-foreground">
              Drone data processing and GIS solutions for modern surveying teams.
            </p>
          </div>

          <!-- Product -->
          <div>
            <h4 class="mb-3 text-sm font-medium text-foreground">Product</h4>
            <ul class="space-y-2">
              <li><a href="#capabilities" class="text-sm text-muted-foreground hover:text-foreground">Features</a></li>
              <li><a href="#pricing" class="text-sm text-muted-foreground hover:text-foreground">Pricing</a></li>
              <li><span class="text-sm text-muted-foreground">API Docs</span></li>
              <li><span class="text-sm text-muted-foreground">Changelog</span></li>
            </ul>
          </div>

          <!-- Company -->
          <div>
            <h4 class="mb-3 text-sm font-medium text-foreground">Company</h4>
            <ul class="space-y-2">
              <li><router-link to="/about" class="text-sm text-muted-foreground hover:text-foreground">About</router-link></li>
              <li><span class="text-sm text-muted-foreground">Careers</span></li>
              <li><span class="text-sm text-muted-foreground">Blog</span></li>
              <li><router-link to="/contact" class="text-sm text-muted-foreground hover:text-foreground">Contact</router-link></li>
            </ul>
          </div>

          <!-- Legal -->
          <div>
            <h4 class="mb-3 text-sm font-medium text-foreground">Legal</h4>
            <ul class="space-y-2">
              <li><span class="text-sm text-muted-foreground">Privacy Policy</span></li>
              <li><span class="text-sm text-muted-foreground">Terms of Service</span></li>
              <li><span class="text-sm text-muted-foreground">Cookie Policy</span></li>
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

<style scoped>
.fade-up {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s ease, transform 0.6s ease;
}
.fade-up.animate-in {
  opacity: 1;
  transform: translateY(0);
}
</style>
