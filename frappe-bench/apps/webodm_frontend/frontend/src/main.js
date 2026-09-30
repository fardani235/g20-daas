import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './index.css'
import 'vue-sonner/style.css'
import { resolveNavigation } from './lib/routeGuard.js'

const routes = [
  {
    path: '/',
    name: 'Landing',
    component: () => import('./pages/Landing.vue'),
    meta: { layout: false },
  },
  {
    path: '/about',
    name: 'About',
    component: () => import('./pages/About.vue'),
    meta: { layout: false, title: 'About' },
  },
  {
    path: '/contact',
    name: 'Contact',
    component: () => import('./pages/Contact.vue'),
    meta: { layout: false, title: 'Contact' },
  },
  {
    // Public catalog: browsable signed-out (own chrome) and signed-in (AppLayout).
    path: '/marketplace',
    name: 'Marketplace',
    component: () => import('./pages/Marketplace.vue'),
    meta: { layout: 'auto', title: 'Marketplace' },
  },
  {
    path: '/marketplace/:product',
    name: 'MarketplaceProduct',
    component: () => import('./pages/MarketplaceProduct.vue'),
    meta: { layout: 'auto', title: 'Marketplace' },
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('./pages/Login.vue'),
    meta: { layout: false },
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('./pages/Dashboard.vue'),
    meta: { requiresAuth: true, title: 'Dashboard' },
  },
  {
    path: '/projects',
    name: 'Projects',
    component: () => import('./pages/Projects.vue'),
    meta: { requiresAuth: true, title: 'Projects' },
  },
  {
    path: '/presets',
    name: 'Presets',
    component: () => import('./pages/Presets.vue'),
    meta: { requiresAuth: true, title: 'Presets' },
  },
  {
    path: '/invoices',
    name: 'Invoices',
    component: () => import('./pages/Invoices.vue'),
    meta: { requiresAuth: true, title: 'Invoices' },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('./pages/Settings.vue'),
    meta: { requiresAuth: true, title: 'Settings' },
  },
  {
    path: '/plugins',
    name: 'Plugins',
    component: () => import('./pages/Plugins.vue'),
    meta: { requiresAuth: true, title: 'Plugins' },
  },
  {
    path: '/project/:id',
    name: 'MapView',
    component: () => import('./pages/MapView.vue'),
    meta: { requiresAuth: true, title: 'Project', fullBleed: true },
  },
  {
    path: '/project/:id/task/:taskId/model',
    name: 'ModelView',
    component: () => import('./pages/ModelView.vue'),
    meta: { requiresAuth: true, title: '3D Model', fullBleed: true },
  },
  {
    path: '/project/:id/task/:taskId/console',
    name: 'Console',
    component: () => import('./pages/Console.vue'),
    meta: { requiresAuth: true, title: 'Task Console' },
  },
  {
    path: '/account/profile',
    name: 'Profile',
    component: () => import('./pages/Profile.vue'),
    meta: { requiresAuth: true, title: 'Profile' },
  },
  {
    path: '/account/password',
    name: 'ChangePassword',
    component: () => import('./pages/ChangePassword.vue'),
    meta: { requiresAuth: true, title: 'Change Password' },
  },
  {
    path: '/onboarding',
    name: 'Onboarding',
    component: () => import('./pages/Onboarding.vue'),
    meta: { requiresAuth: true, layout: false, title: 'Get Started' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('./pages/NotFound.vue'),
    meta: { title: 'Not Found' },
  },
]

const router = createRouter({
  history: createWebHistory('/assets/webodm_frontend/frontend/'),
  routes,
})

router.beforeEach(async (to, from, next) => {
  // Session contract (protected routes, auto layout) lives in lib/routeGuard.js
  // so it can be tested; this hook only applies its decision.
  const redirect = await resolveNavigation(to)
  if (redirect) {
    next(redirect)
    return
  }
  next()
})

const app = createApp(App)
app.use(router)
app.mount('#app')
