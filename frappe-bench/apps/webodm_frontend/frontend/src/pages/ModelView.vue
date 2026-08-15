<template>
  <div class="flex h-full flex-col bg-background">
    <div class="flex flex-shrink-0 items-center gap-3 border-b border-border px-4 py-3">
      <h2 class="text-base font-medium text-foreground">{{ task?.title || '3D viewer' }}</h2>
      <Badge v-if="task" :variant="statusVariant(task.status)">{{ task.status }}</Badge>
      <div class="ml-auto flex items-center gap-2">
        <a
          v-if="task?.model"
          :href="task.model"
          download
          class="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent"
        >
          <Download class="size-3.5" />
          Model
        </a>
        <Button variant="outline" size="sm" @click="resetCamera">
          <Maximize />
          Reset view
        </Button>
      </div>
    </div>
    <div ref="viewerRef" class="flex-1 relative">
      <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-gray-900/80 z-20">
        <div class="text-center">
          <div class="animate-spin h-8 w-8 border-2 border-blue-500 border-t-transparent rounded-full mx-auto mb-3"></div>
          <p class="text-gray-300 text-sm">{{ loadingMessage }}</p>
        </div>
      </div>
      <div v-if="error" class="absolute inset-0 flex items-center justify-center bg-gray-900/80 z-20">
        <div class="text-center max-w-md">
          <TriangleAlert class="mx-auto mb-3 size-10 text-warning" />
          <p class="text-gray-300 text-sm mb-2">{{ error }}</p>
          <Button variant="outline" size="sm" @click="loadModel">Retry</Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { Download, Maximize, TriangleAlert } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import { statusVariant } from '@/lib/status'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js'
import JSZip from 'jszip'

const route = useRoute()
const viewerRef = ref(null)
const task = ref(null)
const loading = ref(true)
const loadingMessage = ref('Loading...')
const error = ref(null)

let scene, camera, renderer, controls
let modelGroup = null
let animationId = null
let cleanupScene = null

function csrfHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
  return headers
}

async function fetchTask() {
  try {
    const res = await fetch('/api/method/webodm_core.api.task.get_task_progress', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ task_name: route.params.taskId }),
    })
    if (!res.ok) return null
    const { message } = await res.json()
    return message
  } catch { return null }
}

function initScene() {
  const el = viewerRef.value
  if (!el) return

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x1a1a2e)

  camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.1, 1000)
  camera.position.set(5, 3, 5)

  renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setSize(el.clientWidth, el.clientHeight)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.0
  el.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.1
  controls.target.set(0, 0, 0)
  controls.update()

  // Lights
  const ambientLight = new THREE.AmbientLight(0x404060, 0.5)
  scene.add(ambientLight)

  const dirLight = new THREE.DirectionalLight(0xffffff, 1.5)
  dirLight.position.set(5, 10, 5)
  dirLight.castShadow = false
  scene.add(dirLight)

  const fillLight = new THREE.DirectionalLight(0x8888ff, 0.3)
  fillLight.position.set(-5, 0, 5)
  scene.add(fillLight)

  // Grid
  const gridHelper = new THREE.GridHelper(100, 20, 0x666688, 0x444466)
  scene.add(gridHelper)

  // Axes
  // scene.add(new THREE.AxesHelper(50))

  // Resize
  const onResize = () => {
    if (!el) return
    camera.aspect = el.clientWidth / el.clientHeight
    camera.updateProjectionMatrix()
    renderer.setSize(el.clientWidth, el.clientHeight)
  }
  window.addEventListener('resize', onResize)

  // Animate
  function animate() {
    animationId = requestAnimationFrame(animate)
    controls.update()
    renderer.render(scene, camera)
  }
  animate()

  return () => {
    window.removeEventListener('resize', onResize)
  }
}

function fitCamera() {
  if (!modelGroup) return
  const box = new THREE.Box3().setFromObject(modelGroup)
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z)
  const distance = maxDim * 2.5

  controls.target.copy(center)
  camera.position.set(center.x + distance * 0.6, center.y + distance * 0.4, center.z + distance * 0.6)
  controls.update()
}

function resetCamera() {
  if (!modelGroup) return
  const box = new THREE.Box3().setFromObject(modelGroup)
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z)
  const distance = maxDim * 2.5

  camera.near = distance * 0.001
  camera.far = distance * 100
  camera.updateProjectionMatrix()
  controls.target.copy(center)
  camera.position.set(center.x + distance * 0.6, center.y + distance * 0.4, center.z + distance * 0.6)
  controls.update()
}

async function loadModel() {
  if (!task.value) return
  error.value = null
  loading.value = true
  loadingMessage.value = 'Loading 3D model...'

  if (modelGroup) {
    scene.remove(modelGroup)
    modelGroup = null
  }

  try {
    const modelUrl = task.value.model
    if (!modelUrl) {
      loading.value = false
      error.value = 'No 3D model available for this task'
      return
    }

    loadingMessage.value = 'Downloading model...'
    const res = await fetch(modelUrl)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)

    loadingMessage.value = 'Parsing model...'

    let loaderUrl = modelUrl

    if (modelUrl.endsWith('.zip')) {
      loadingMessage.value = 'Extracting model archive...'
      const buf = await res.arrayBuffer()
      const zip = await JSZip.loadAsync(buf)
      const files = {}
      const promises = []
      zip.forEach((relPath, file) => {
        if (!file.dir) {
          promises.push(
            file.async('blob').then(blob => {
              files[relPath] = URL.createObjectURL(blob)
            })
          )
        }
      })
      await Promise.all(promises)

      const gltfEntry = Object.keys(files).find(k => k.endsWith('.gltf') || k.endsWith('.glb'))
      if (!gltfEntry) throw new Error('No GLTF/GLB found in archive')

      if (gltfEntry.endsWith('.glb')) {
        loaderUrl = files[gltfEntry]
      } else {
        const gltfText = await (await fetch(files[gltfEntry])).text()
        const gltfJson = JSON.parse(gltfText)
        for (const key of ['buffers', 'images']) {
          for (const item of (gltfJson[key] || [])) {
            if (item.uri && files[item.uri]) {
              item.uri = files[item.uri]
            }
          }
        }
        const patched = new Blob([JSON.stringify(gltfJson)], { type: 'application/json' })
        loaderUrl = URL.createObjectURL(patched)
      }
    }

    const loader = new GLTFLoader()
    const dracoLoader = new DRACOLoader()
    dracoLoader.setDecoderPath('/assets/webodm_frontend/frontend/draco/')
    loader.setDRACOLoader(dracoLoader)
    const gltf = await new Promise((resolve, reject) => {
      loader.load(loaderUrl, resolve, (p) => {
        if (p.total) loadingMessage.value = `Loading model... ${Math.round(p.loaded / p.total * 100)}%`
      }, reject)
    })

    // Center geometry data at origin (handles UTM coordinates)
    const box = new THREE.Box3().setFromObject(gltf.scene)
    const center = box.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3())
    console.log('Model center:', center, 'size:', size)

    if (size.x === 0 && size.y === 0 && size.z === 0) {
      throw new Error('Model has no geometry')
    }

    // Translate geometry vertices to origin (not via parent group)
    // This avoids float32 precision loss from large-world-coordinates
    const offset = center.clone()
    gltf.scene.traverse((child) => {
      if (child.isMesh) {
        const pos = child.geometry.getAttribute('position')
        for (let i = 0; i < pos.count; i++) {
          pos.setXYZ(i,
            pos.getX(i) - offset.x,
            pos.getY(i) - offset.y,
            pos.getZ(i) - offset.z
          )
        }
        pos.needsUpdate = true
        child.geometry.computeBoundingSphere()
        child.geometry.computeBoundingBox()

        // Ensure visible material color in case textures fail
        if (child.material) {
          const mats = Array.isArray(child.material) ? child.material : [child.material]
          for (const m of mats) {
            if (m.type === 'MeshBasicMaterial' || m.type === 'MeshStandardMaterial') {
              // Keep texture colors, but ensure base color is white so model is visible
              m.color.setHex(0xffffff)
            }
            m.side = THREE.DoubleSide
          }
        }
      }
    })

    modelGroup = gltf.scene
    scene.add(modelGroup)

    // Apply textures/materials
    gltf.scene.traverse((child) => {
      if (child.isMesh) {
        child.castShadow = true
        child.receiveShadow = true
      }
    })

    loadingMessage.value = 'Adjusting view...'
    await nextTick()

    // Fit camera to model dimensions (now near origin)
    const maxDim = Math.max(size.x, size.y, size.z) || 1
    const distance = maxDim * 1.5

    camera.near = distance * 0.001
    camera.far = distance * 100
    camera.updateProjectionMatrix()

    controls.target.set(0, 0, 0)
    camera.position.set(distance, distance * 0.5, distance)
    controls.update()
    loading.value = false
  } catch (e) {
    console.error('Model load error:', e)
    loading.value = false
    error.value = `Failed to load model: ${e.message || 'Unknown error'}`
  }
}

onMounted(async () => {
  task.value = await fetchTask()
  cleanupScene = initScene()

  if (task.value?.model) {
    await loadModel()
  } else {
    loading.value = false
    error.value = 'No 3D model available for this task'
  }

  // Start polling if running
  if (['Pending', 'Running', 'Queued'].includes(task.value?.status)) {
    const pollTimer = setInterval(async () => {
      const updated = await fetchTask()
      if (updated) {
        task.value = updated
        if (updated.model && !modelGroup) {
          clearInterval(pollTimer)
          await loadModel()
        }
        if (!['Pending', 'Running', 'Queued'].includes(updated.status)) {
          clearInterval(pollTimer)
        }
      }
    }, 5000)
    onUnmounted(() => clearInterval(pollTimer))
  }
})

onUnmounted(() => {
  if (animationId) cancelAnimationFrame(animationId)
  if (cleanupScene) cleanupScene()
  if (renderer) {
    renderer.dispose()
    renderer.domElement?.remove()
  }
  if (viewerRef.value) {
    viewerRef.value.innerHTML = ''
  }
})
</script>
