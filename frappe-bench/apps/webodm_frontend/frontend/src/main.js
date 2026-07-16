import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { FrappeUI } from 'frappe-ui'
import App from './App.vue'
import './index.css'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('./pages/Login.vue'),
    meta: { layout: false },
  },
  {
    path: '/',
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
    path: '/project/:id',
    name: 'MapView',
    component: () => import('./pages/MapView.vue'),
    meta: { requiresAuth: true, title: 'Project' },
  },
  {
    path: '/project/:id/task/:taskId/model',
    name: 'ModelView',
    component: () => import('./pages/ModelView.vue'),
    meta: { requiresAuth: true, title: '3D Model' },
  },
  {
    path: '/project/:id/task/:taskId/console',
    name: 'Console',
    component: () => import('./pages/Console.vue'),
    meta: { requiresAuth: true, title: 'Task Console' },
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

async function isLoggedIn() {
  try {
    const res = await fetch('/api/method/frappe.auth.get_logged_user')
    if (!res.ok) return false
    const data = await res.json()
    return data.message && data.message !== 'Guest'
  } catch {
    return false
  }
}

router.beforeEach(async (to, from, next) => {
  if (to.meta.requiresAuth) {
    const loggedIn = await isLoggedIn()
    if (!loggedIn) {
      next({ name: 'Login', query: { redirect: to.fullPath } })
      return
    }
  }
  next()
})

const app = createApp(App)
app.use(router)
app.use(FrappeUI, { socketio: false })
app.mount('#app')
