// Owns the three.js side of the 3D model viewer: renderer, camera, orbit
// controls, model loading/disposal and the camera actions the toolbar and
// keyboard trigger. pages/ModelView.vue only deals with task data and UI
// state; nothing here knows about tasks or routes.
//
// Design notes
// - Render on demand: a requestAnimationFrame loop runs, but the scene is only
//   drawn when the controls report a change, a tween is running, or something
//   called requestRender(). Idle GPU usage is ~zero even with a large mesh.
// - Loading is cancellable: switching datasets or leaving the page aborts the
//   download and drops a late-arriving parse result instead of adding it.
// - Everything allocated for a model (geometry, materials, textures, blob
//   URLs) is released in clear(), and the renderer is torn down in dispose().
// - Camera moves from the toolbar/keyboard are short eased tweens so the
//   view never jumps; direct drag input stays immediate.

import { reactive, readonly } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js'
import {
  MODES,
  mouseButtonsFor,
  touchesFor,
  framingFor,
  presetPosition,
  dollyDistance,
  panStep,
  Z_UP_TO_Y_UP_X_ROTATION,
  needsVertexRecenter,
  gridSizeFor,
  pixelRatioFor,
  pickModelEntry,
  resolveZipUri,
} from '@/lib/modelViewer'
import { TextureBudgetPlugin, textureBudgetBytes } from '@/lib/textureBudget'

const FOV = 50
const BACKGROUND = 0x1c2030
const GRID_MAJOR = 0x3d4360
const GRID_MINOR = 0x2b3047
const TWEEN_MS = 450
const ZOOM_STEP = 0.7
const DRACO_PATH = '/assets/webodm_frontend/frontend/draco/'

const TEXTURE_SLOTS = ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap', 'alphaMap', 'bumpMap']

function easeInOut(t) {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
}

function webglSupported() {
  try {
    const canvas = document.createElement('canvas')
    return !!(window.WebGLRenderingContext && (canvas.getContext('webgl2') || canvas.getContext('webgl')))
  } catch {
    return false
  }
}

function disposeMaterial(material) {
  for (const slot of TEXTURE_SLOTS) {
    if (material[slot]?.dispose) material[slot].dispose()
  }
  material.dispose?.()
}

function disposeObject(root) {
  const seenGeometry = new Set()
  const seenMaterial = new Set()
  root.traverse(child => {
    if (child.geometry && !seenGeometry.has(child.geometry)) {
      seenGeometry.add(child.geometry)
      child.geometry.dispose()
    }
    const mats = Array.isArray(child.material) ? child.material : child.material ? [child.material] : []
    for (const m of mats) {
      if (!seenMaterial.has(m)) {
        seenMaterial.add(m)
        disposeMaterial(m)
      }
    }
  })
}

/**
 * @param {import('vue').Ref<HTMLElement|null>} containerRef element the canvas is mounted into
 * @param {{ onInteract?: () => void }} [options]
 */
export function useModelViewer(containerRef, options = {}) {
  const state = reactive({
    status: 'idle', // idle | loading | ready | error | unsupported
    phase: null, // download | extract | parse | prepare
    loaded: 0,
    total: 0,
    error: null,
    mode: 'rotate',
    gridVisible: true,
    fullscreen: false,
    texturesDone: 0,
    texturesTotal: 0,
    stats: { triangles: 0, vertices: 0, bytes: 0, textures: 0, textureCap: null },
  })

  let scene, camera, renderer, controls
  let root = null // wrapper group holding the current model
  let grid = null
  let framing = null // from framingFor(); null until a model is loaded
  let modelCenter = new THREE.Vector3()
  let animationId = null
  let needsRender = false
  let tween = null
  let resizeObserver = null
  let abortController = null
  let loadId = 0
  let blobUrls = []
  let dracoLoader = null
  let textureCap = null
  const raycaster = new THREE.Raycaster()

  function requestRender() {
    needsRender = true
  }

  // ------------------------------------------------------------------ setup

  function init() {
    const el = containerRef.value
    if (!el || renderer) return
    if (!webglSupported()) {
      state.status = 'unsupported'
      return
    }

    scene = new THREE.Scene()
    scene.background = new THREE.Color(BACKGROUND)

    const w = Math.max(el.clientWidth, 1), h = Math.max(el.clientHeight, 1)
    camera = new THREE.PerspectiveCamera(FOV, w / h, 0.1, 5000)
    camera.position.set(40, 30, 40)

    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
    renderer.setPixelRatio(pixelRatioFor(window.devicePixelRatio, 0))
    renderer.setSize(w, h)
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.domElement.style.display = 'block'
    renderer.domElement.style.touchAction = 'none'
    el.appendChild(renderer.domElement)

    renderer.domElement.addEventListener('webglcontextlost', onContextLost)
    renderer.domElement.addEventListener('webglcontextrestored', requestRender)
    renderer.domElement.addEventListener('dblclick', onDoubleClick)

    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.12
    controls.rotateSpeed = 0.7
    controls.screenSpacePanning = true
    controls.zoomToCursor = true
    // Keep the camera above the ground plane so terrain models are never
    // viewed from underneath (where a single-sided texture would vanish).
    controls.maxPolarAngle = Math.PI / 2 - 0.02
    controls.addEventListener('change', requestRender)
    controls.addEventListener('start', () => {
      endTween()
      options.onInteract?.()
    })
    applyMode(state.mode)

    scene.add(new THREE.HemisphereLight(0xffffff, 0x3a3f55, 1.1))
    const sun = new THREE.DirectionalLight(0xffffff, 1.3)
    sun.position.set(1, 2, 1.2)
    scene.add(sun)

    resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(el)
    document.addEventListener('fullscreenchange', onFullscreenChange)

    animate()
  }

  function resize() {
    const el = containerRef.value
    if (!el || !renderer) return
    const w = el.clientWidth, h = el.clientHeight
    if (w === 0 || h === 0) return
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
    requestRender()
  }

  function animate() {
    animationId = requestAnimationFrame(animate)
    let changed = false
    if (tween) changed = stepTween() || changed
    if (controls) changed = controls.update() || changed
    if ((changed || needsRender) && renderer && scene && camera) {
      needsRender = false
      renderer.render(scene, camera)
    }
  }

  function onContextLost(event) {
    event.preventDefault()
    state.status = 'error'
    state.error = 'The graphics context was lost. This usually means the model is too large for this device; reload to try again.'
  }

  function onFullscreenChange() {
    state.fullscreen = !!document.fullscreenElement
  }

  // --------------------------------------------------------------- loading

  async function readWithProgress(response, signal) {
    const total = Number(response.headers.get('content-length')) || 0
    state.total = total
    state.loaded = 0
    if (!response.body?.getReader) {
      const buf = await response.arrayBuffer()
      state.loaded = buf.byteLength
      return buf
    }
    const reader = response.body.getReader()
    const chunks = []
    let received = 0
    for (;;) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      received += value.byteLength
      state.loaded = received
    }
    const out = new Uint8Array(received)
    let offset = 0
    for (const c of chunks) { out.set(c, offset); offset += c.byteLength }
    return out.buffer
  }

  function makeLoader(urlModifier) {
    const manager = new THREE.LoadingManager()
    if (urlModifier) manager.setURLModifier(urlModifier)
    const loader = new GLTFLoader(manager)
    if (!dracoLoader) {
      dracoLoader = new DRACOLoader()
      dracoLoader.setDecoderPath(DRACO_PATH)
    }
    loader.setDRACOLoader(dracoLoader)
    // Decode textures a few at a time, downsampled to fit device memory
    // (see lib/textureBudget.js); avoids the multi-GB decode spike that
    // crashes tabs on large ODM models.
    const isMobile = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent)
    loader.register(parser => new TextureBudgetPlugin(parser, {
      budgetBytes: textureBudgetBytes(navigator.deviceMemory, isMobile),
      maxTextureSize: renderer?.capabilities.maxTextureSize || 8192,
      concurrency: 2,
      onProgress: (done, total, cap) => {
        state.texturesDone = done
        state.texturesTotal = total
        textureCap = cap
      },
    }))
    return loader
  }

  function parseGltf(loader, data, path = '') {
    return new Promise((resolve, reject) => loader.parse(data, path, resolve, reject))
  }

  async function parseZip(buffer) {
    const { default: JSZip } = await import('jszip')
    const zip = await JSZip.loadAsync(buffer)
    const names = Object.keys(zip.files)
    const entry = pickModelEntry(names)
    if (!entry) throw new Error('The archive contains no .glb or .gltf file')
    if (entry.toLowerCase().endsWith('.glb')) {
      const glb = await zip.file(entry).async('arraybuffer')
      return parseGltf(makeLoader(), glb)
    }
    // glTF with external buffers/images: expose siblings as blob URLs and let
    // the loader resolve relative URIs through them.
    const files = {}
    await Promise.all(names.filter(n => !zip.files[n].dir && n !== entry).map(async n => {
      const blob = await zip.file(n).async('blob')
      const url = URL.createObjectURL(blob)
      blobUrls.push(url)
      files[n] = url
    }))
    const baseDir = entry.includes('/') ? entry.slice(0, entry.lastIndexOf('/') + 1) : ''
    const text = await zip.file(entry).async('string')
    const loader = makeLoader(url => resolveZipUri(url, files, baseDir) || url)
    return parseGltf(loader, text)
  }

  /**
   * Download and display the model at `url` (a .glb, or a .zip holding a
   * glb/gltf). Replaces the current model. Returns once the model is on
   * screen or the load failed/was superseded.
   */
  async function load(url) {
    if (state.status === 'unsupported') return
    if (!renderer) init()
    const id = ++loadId
    abortController?.abort()
    abortController = new AbortController()
    const { signal } = abortController
    const current = () => id === loadId && !signal.aborted

    clear()
    state.status = 'loading'
    state.phase = 'download'
    state.error = null
    state.loaded = 0
    state.total = 0
    state.texturesDone = 0
    state.texturesTotal = 0
    textureCap = null

    try {
      const response = await fetch(url, { credentials: 'include', signal })
      if (!response.ok) {
        throw new Error(response.status === 403 || response.status === 401
          ? 'You do not have permission to open this model'
          : `The model could not be downloaded (HTTP ${response.status})`)
      }
      const buffer = await readWithProgress(response, signal)
      if (!current()) return
      const bytes = buffer.byteLength

      let gltf
      if (/\.zip(\?|$)/i.test(url)) {
        state.phase = 'extract'
        gltf = await parseZip(buffer)
      } else {
        state.phase = 'parse'
        gltf = await parseGltf(makeLoader(), buffer)
      }
      if (!current()) {
        disposeObject(gltf.scene)
        return
      }

      state.phase = 'prepare'
      mount(gltf.scene, bytes)
      state.status = 'ready'
    } catch (e) {
      if (signal.aborted || !current()) return
      console.error('Model load failed:', e)
      state.status = 'error'
      state.error = e?.message || 'The model could not be loaded'
    } finally {
      revokeBlobUrls()
      if (current()) state.phase = null
    }
  }

  /** Place a parsed glTF scene: recenter, stand upright, size grid and camera. */
  function mount(object, bytes) {
    const box = new THREE.Box3().setFromObject(object)
    if (box.isEmpty()) throw new Error('The model contains no geometry')
    const center = box.getCenter(new THREE.Vector3())

    let triangles = 0, vertices = 0
    const textures = new Set()
    const maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy())
    object.traverse(child => {
      if (!child.isMesh) return
      const pos = child.geometry.getAttribute('position')
      if (pos) {
        vertices += pos.count
        triangles += child.geometry.index ? child.geometry.index.count / 3 : pos.count / 3
      }
      const mats = Array.isArray(child.material) ? child.material : [child.material]
      for (const m of mats) {
        if (!m) continue
        m.side = THREE.DoubleSide
        // Photogrammetry textures are baked lighting; tone mapping them only
        // washes the colours out.
        if (m.isMeshBasicMaterial) m.toneMapped = false
        for (const slot of TEXTURE_SLOTS) {
          const tex = m[slot]
          if (tex) { tex.anisotropy = maxAniso; textures.add(tex) }
        }
      }
    })

    if (needsVertexRecenter(center)) {
      // Projected coordinates: bake the offset into the vertex data once so
      // the GPU works with small, precise numbers.
      const seen = new Set()
      object.updateMatrixWorld(true)
      object.traverse(child => {
        if (child.isMesh && !seen.has(child.geometry)) {
          seen.add(child.geometry)
          child.geometry.translate(-center.x, -center.y, -center.z)
        }
      })
      object.traverse(child => {
        if (child.isMesh) child.geometry.computeBoundingSphere()
      })
    } else {
      object.position.sub(center)
    }

    root = new THREE.Group()
    root.rotation.x = Z_UP_TO_Y_UP_X_ROTATION
    root.add(object)
    scene.add(root)

    const worldBox = new THREE.Box3().setFromObject(root)
    const size = worldBox.getSize(new THREE.Vector3())
    modelCenter = worldBox.getCenter(new THREE.Vector3())

    buildGrid(worldBox, size)

    framing = framingFor(size, camera.aspect, FOV)
    camera.near = framing.near
    camera.far = framing.far
    camera.updateProjectionMatrix()
    controls.minDistance = framing.minDistance
    controls.maxDistance = framing.maxDistance
    placeCamera(presetPosition('iso', modelCenter, framing.distance), modelCenter)

    renderer.setPixelRatio(pixelRatioFor(window.devicePixelRatio, triangles))
    resize()

    state.stats = { triangles: Math.round(triangles), vertices, bytes, textures: textures.size, textureCap }
    requestRender()
  }

  function buildGrid(box, size) {
    if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose() }
    const extent = gridSizeFor(Math.max(size.x, size.z))
    grid = new THREE.GridHelper(extent, 20, GRID_MAJOR, GRID_MINOR)
    grid.position.set(modelCenter.x, box.min.y - size.y * 0.002, modelCenter.z)
    grid.visible = state.gridVisible
    scene.add(grid)
  }

  /** Remove the current model and free its GPU resources. */
  function clear() {
    if (root) {
      scene.remove(root)
      disposeObject(root)
      root = null
    }
    if (grid) {
      scene.remove(grid)
      grid.geometry.dispose()
      grid.material.dispose()
      grid = null
    }
    framing = null
    tween = null
    state.stats = { triangles: 0, vertices: 0, bytes: 0, textures: 0, textureCap: null }
    requestRender()
  }

  function revokeBlobUrls() {
    for (const u of blobUrls) URL.revokeObjectURL(u)
    blobUrls = []
  }

  // ---------------------------------------------------------------- camera

  function placeCamera(position, target) {
    camera.position.set(position.x, position.y, position.z)
    controls.target.set(target.x, target.y, target.z)
    controls.update()
    requestRender()
  }

  function animateCamera(position, target, duration = TWEEN_MS) {
    if (!camera) return
    // Flush any residual inertia from a previous drag so the tween lands
    // exactly on its destination: with damping off, update() zeroes the
    // controls' internal deltas. Damping comes back when the tween ends.
    controls.enableDamping = false
    controls.update()
    tween = {
      fromPos: camera.position.clone(),
      fromTarget: controls.target.clone(),
      toPos: new THREE.Vector3(position.x, position.y, position.z),
      toTarget: new THREE.Vector3(target.x, target.y, target.z),
      start: performance.now(),
      duration,
    }
  }

  function stepTween() {
    const t = Math.min((performance.now() - tween.start) / tween.duration, 1)
    const k = easeInOut(t)
    camera.position.lerpVectors(tween.fromPos, tween.toPos, k)
    controls.target.lerpVectors(tween.fromTarget, tween.toTarget, k)
    if (t >= 1) endTween()
    return true
  }

  function endTween() {
    tween = null
    if (controls) controls.enableDamping = true
  }

  function setView(preset) {
    if (!framing) return
    animateCamera(presetPosition(preset, modelCenter, framing.distance), modelCenter)
  }

  /** Return to the default framing of the whole model. */
  function resetView() {
    setView('iso')
  }

  function zoomBy(factor) {
    if (!camera || !controls) return
    const dir = camera.position.clone().sub(controls.target)
    const dist = dir.length()
    const min = framing?.minDistance ?? controls.minDistance
    const max = framing?.maxDistance ?? controls.maxDistance
    const next = dollyDistance(dist, factor, min, max)
    const pos = controls.target.clone().add(dir.normalize().multiplyScalar(next))
    animateCamera(pos, controls.target, 250)
  }

  const zoomIn = () => zoomBy(ZOOM_STEP)
  const zoomOut = () => zoomBy(1 / ZOOM_STEP)

  /** Pan in screen space; dx/dy in [-1, 1] (right/up positive). */
  function pan(dx, dy) {
    if (!camera || !controls) return
    endTween()
    const dist = camera.position.distanceTo(controls.target)
    const step = panStep(dist, dx, dy)
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion).multiplyScalar(step.right)
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion).multiplyScalar(step.up)
    const offset = right.add(up)
    camera.position.add(offset)
    controls.target.add(offset)
    requestRender()
  }

  /** Re-target the orbit on the model point under the pointer. */
  function onDoubleClick(event) {
    if (!root || !renderer) return
    const rect = renderer.domElement.getBoundingClientRect()
    const ndc = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    )
    raycaster.setFromCamera(ndc, camera)
    const hit = raycaster.intersectObject(root, true)[0]
    if (!hit) return
    const delta = hit.point.clone().sub(controls.target)
    animateCamera(camera.position.clone().add(delta), hit.point, 350)
    options.onInteract?.()
  }

  function applyMode(mode) {
    if (!controls) return
    controls.mouseButtons = mouseButtonsFor(mode)
    controls.touches = touchesFor(mode)
  }

  function setMode(mode) {
    if (!MODES.includes(mode)) return
    state.mode = mode
    applyMode(mode)
  }

  function toggleGrid() {
    state.gridVisible = !state.gridVisible
    if (grid) grid.visible = state.gridVisible
    requestRender()
  }

  async function toggleFullscreen(el) {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else if (el?.requestFullscreen) await el.requestFullscreen()
    } catch (e) {
      console.warn('Fullscreen unavailable:', e)
    }
  }

  /**
   * Run a camera/scene action by name (see lib/modelViewer keyAction).
   * Returns false for actions this composable does not own.
   */
  function performAction(action) {
    switch (action) {
      case 'panLeft': pan(-1, 0); return true
      case 'panRight': pan(1, 0); return true
      case 'panUp': pan(0, 1); return true
      case 'panDown': pan(0, -1); return true
      case 'zoomIn': zoomIn(); return true
      case 'zoomOut': zoomOut(); return true
      case 'reset': resetView(); return true
      case 'viewIso': setView('iso'); return true
      case 'viewTop': setView('top'); return true
      case 'viewNorth': setView('north'); return true
      case 'viewEast': setView('east'); return true
      case 'toggleGrid': toggleGrid(); return true
      default: return false
    }
  }

  /** Plain-data snapshot of the camera and scene, for tests and debugging. */
  function snapshot() {
    if (!camera || !controls) return null
    return {
      status: state.status,
      mode: state.mode,
      gridVisible: state.gridVisible,
      position: camera.position.toArray(),
      target: controls.target.toArray(),
      distance: camera.position.distanceTo(controls.target),
      center: modelCenter.toArray(),
      framingDistance: framing?.distance ?? null,
      tweening: !!tween,
      leftButton: controls.mouseButtons.LEFT,
      pixelRatio: renderer?.getPixelRatio() ?? null,
      triangles: state.stats.triangles,
      textureCap: state.stats.textureCap,
    }
  }

  // --------------------------------------------------------------- teardown

  function dispose() {
    abortController?.abort()
    loadId++
    if (animationId) cancelAnimationFrame(animationId)
    animationId = null
    resizeObserver?.disconnect()
    resizeObserver = null
    document.removeEventListener('fullscreenchange', onFullscreenChange)
    clear()
    revokeBlobUrls()
    dracoLoader?.dispose()
    dracoLoader = null
    if (controls) { controls.dispose(); controls = null }
    if (renderer) {
      renderer.domElement.removeEventListener('webglcontextlost', onContextLost)
      renderer.domElement.removeEventListener('webglcontextrestored', requestRender)
      renderer.domElement.removeEventListener('dblclick', onDoubleClick)
      renderer.renderLists.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
      renderer = null
    }
    scene = null
    camera = null
  }

  return {
    state: readonly(state),
    init,
    load,
    clear,
    dispose,
    resize,
    setMode,
    setView,
    resetView,
    zoomIn,
    zoomOut,
    pan,
    toggleGrid,
    toggleFullscreen,
    performAction,
    requestRender,
    snapshot,
  }
}
