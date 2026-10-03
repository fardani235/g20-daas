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
import { createPointCloudLayer } from '@/composables/pointCloudLayer'
import { BACKGROUNDS, canFinish, centroid, measurementText } from '@/lib/potree'

const FOV = 50
const BACKGROUND = 0x1c2030
const GRID_MAJOR = 0x3d4360
const GRID_MINOR = 0x2b3047
const TWEEN_MS = 450
const ZOOM_STEP = 0.7
const DRACO_PATH = '/assets/webodm_frontend/frontend/draco/'
// A pointer-up this close (px) and this soon (ms) after pointer-down is a click, not an orbit drag.
const CLICK_PX = 5
const CLICK_MS = 500
// Clicking this close (px) to a polygon's first vertex closes it.
const CLOSE_PX = 14

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
 * @param {{ onInteract?: () => void, onVolume?: (points: number[][]) => Promise<string>, onMeasureMiss?: () => void }} [options]
 *   `onVolume` receives a finished volume polygon (native CRS vertices) and resolves to the readout.
 */
export function useModelViewer(containerRef, options = {}) {
  const state = reactive({
    status: 'idle', // idle | loading | ready | error | unsupported
    phase: null, // download | extract | parse | prepare | octree
    kind: null, // model | pointcloud (what is on screen)
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
  // Point cloud mode: what the panel/stats chip reads.
  const pointCloud = reactive({
    metadata: null, // the octree's metadata.json (attributes, bounds, projection)
    origin: [0, 0, 0], // native coordinates of the scene origin
    totalPoints: 0,
    visiblePoints: 0,
    visibleNodes: 0,
    loadingNodes: 0,
    colorMode: 'rgb',
  })
  // Measurements picked on the rendered points. `points` are native [x, y, z].
  const measure = reactive({
    kind: null, // distance | area | volume while a tool is active
    points: [], // vertices of the shape being drawn
    text: '', // live readout while drawing
    measurements: [], // finished: { id, kind, points, text, status }
    labels: [], // screen-space labels, refreshed after each render
  })

  let scene, camera, renderer, controls
  let layer = null // point cloud layer (created with the scene)
  let up = 'y' // 'y' for models, 'z' for point clouds (Potree colours/clips by world Z)
  let pointerDown = null
  let measureSeq = 0
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
    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    renderer.domElement.addEventListener('pointerup', onPointerUp)

    layer = createPointCloudLayer({ scene, getCamera: () => camera, getRenderer: () => renderer, requestRender })

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
    // Potree decides per frame which octree nodes to show for this camera and
    // budget; it requests a render whenever that set changes or data arrives.
    if (layer?.loaded) layer.update()
    if ((changed || needsRender) && renderer && scene && camera) {
      needsRender = false
      renderer.render(scene, camera)
      if (layer?.loaded) {
        syncPointCloudStats()
        updateLabels()
      }
    }
  }

  function syncPointCloudStats() {
    const s = layer.stats()
    if (s.visiblePoints !== pointCloud.visiblePoints) pointCloud.visiblePoints = s.visiblePoints
    if (s.visibleNodes !== pointCloud.visibleNodes) pointCloud.visibleNodes = s.visibleNodes
    if (s.loadingNodes !== pointCloud.loadingNodes) pointCloud.loadingNodes = s.loadingNodes
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
    state.kind = 'model'
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
      setUp('y')
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

  /**
   * Stream a task's Potree octree. `files` maps metadata.json / hierarchy.bin /
   * octree.bin to their private URLs (from the backend's potree_state).
   * Resolves once the root node is on screen; finer nodes keep arriving as
   * the camera moves.
   */
  async function loadPointCloud(taskName, files) {
    if (state.status === 'unsupported') return
    if (!renderer) init()
    const id = ++loadId
    abortController?.abort()
    abortController = new AbortController()
    const { signal } = abortController
    const current = () => id === loadId && !signal.aborted

    clear()
    state.status = 'loading'
    state.phase = 'octree'
    state.kind = 'pointcloud'
    state.error = null
    state.loaded = 0
    state.total = 0
    try {
      const { metadata, origin, box } = await layer.load(taskName, files, { signal })
      if (!current()) { layer.dispose(); return }
      setUp('z')
      mountPointCloud(box, metadata, origin)
      state.status = 'ready'
    } catch (e) {
      if (signal.aborted || !current()) return
      console.error('Point cloud load failed:', e)
      state.status = 'error'
      state.error = e?.message || 'The point cloud could not be loaded'
    } finally {
      if (current()) state.phase = null
    }
  }

  /** Frame a loaded octree (already centred at the origin, Z-up). */
  function mountPointCloud(box, metadata, origin) {
    const size = box.getSize(new THREE.Vector3())
    modelCenter = box.getCenter(new THREE.Vector3())
    buildGrid(box, size)
    framing = framingFor(size, camera.aspect, FOV)
    camera.near = framing.near
    camera.far = framing.far
    camera.updateProjectionMatrix()
    controls.minDistance = framing.minDistance
    controls.maxDistance = framing.maxDistance
    placeCamera(presetPosition('iso', modelCenter, framing.distance, up), modelCenter)
    renderer.setPixelRatio(pixelRatioFor(window.devicePixelRatio, 0))
    resize()
    pointCloud.metadata = metadata
    pointCloud.origin = origin
    pointCloud.totalPoints = metadata?.points || 0
    pointCloud.visiblePoints = 0
    pointCloud.visibleNodes = 0
    pointCloud.loadingNodes = 0
    pointCloud.colorMode = layer.colorMode
    state.stats = { triangles: 0, vertices: 0, bytes: 0, textures: 0, textureCap: null }
    requestRender()
  }

  /** Switch the scene's up axis: Y for glTF models, Z for point clouds. */
  function setUp(axis) {
    up = axis
    if (!camera) return
    if (axis === 'z') camera.up.set(0, 0, 1)
    else camera.up.set(0, 1, 0)
    controls?.update()
  }

  /** Point cloud rendering option. Keys: colorMode, pointSize, pointBudget, background, elevationFilter, hiddenClasses. */
  function setPointCloudOption(key, value) {
    if (!layer) return
    switch (key) {
      case 'colorMode':
        layer.setColorMode(value)
        pointCloud.colorMode = value
        break
      case 'pointSize': layer.setPointSize(Number(value)); break
      case 'pointBudget': layer.setPointBudget(Number(value)); break
      case 'background': {
        const bg = BACKGROUNDS.find(b => b.value === value)
        layer.setBackground(bg ? bg.color : BACKGROUNDS[0].color)
        break
      }
      case 'elevationFilter': layer.setElevationFilter(value); break
      case 'hiddenClasses': layer.setHiddenClasses(value instanceof Set ? value : new Set(value || [])); break
      default: break
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
    const extent = gridSizeFor(up === 'z' ? Math.max(size.x, size.y) : Math.max(size.x, size.z))
    grid = new THREE.GridHelper(extent, 20, GRID_MAJOR, GRID_MINOR)
    if (up === 'z') {
      // GridHelper lies in XZ; stand it in XY for a Z-up scene.
      grid.rotation.x = Math.PI / 2
      grid.position.set(modelCenter.x, modelCenter.y, box.min.z - size.z * 0.002)
    } else {
      grid.position.set(modelCenter.x, box.min.y - size.y * 0.002, modelCenter.z)
    }
    grid.visible = state.gridVisible
    scene.add(grid)
  }

  /** Remove the current model or point cloud and free its GPU resources. */
  function clear() {
    if (root) {
      scene.remove(root)
      disposeObject(root)
      root = null
    }
    if (layer) {
      layer.dispose()
      if (scene) scene.background = new THREE.Color(BACKGROUND)
    }
    cancelMeasurement()
    measure.measurements = []
    measure.labels = []
    pointCloud.metadata = null
    pointCloud.totalPoints = 0
    pointCloud.visiblePoints = 0
    pointCloud.visibleNodes = 0
    pointCloud.loadingNodes = 0
    state.kind = null
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
    animateCamera(presetPosition(preset, modelCenter, framing.distance, up), modelCenter)
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

  function ndcFor(event) {
    const rect = renderer.domElement.getBoundingClientRect()
    return new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    )
  }

  /** Re-target the orbit on the point under the pointer; while measuring, a double-click finishes the shape. */
  function onDoubleClick(event) {
    if (!renderer) return
    if (measure.kind) {
      // The second click of the double-click just added a duplicate vertex.
      if (measure.points.length > 1) measure.points.pop()
      updateDrawing()
      finishMeasurement()
      return
    }
    const ndc = ndcFor(event)
    let point = null
    if (layer?.loaded) {
      point = layer.pick(ndc)?.local || null
    } else if (root) {
      raycaster.setFromCamera(ndc, camera)
      point = raycaster.intersectObject(root, true)[0]?.point || null
    }
    if (!point) return
    const delta = point.clone().sub(controls.target)
    animateCamera(camera.position.clone().add(delta), point, 350)
    options.onInteract?.()
  }

  // ---------------------------------------------------------- measurement

  function onPointerDown(event) {
    pointerDown = { x: event.clientX, y: event.clientY, t: performance.now(), button: event.button }
  }

  function onPointerUp(event) {
    const down = pointerDown
    pointerDown = null
    if (!down || !measure.kind || !layer?.loaded || down.button !== 0 || event.button !== 0) return
    if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > CLICK_PX) return
    if (performance.now() - down.t > CLICK_MS) return
    addMeasurePoint(event)
  }

  function addMeasurePoint(event) {
    const hit = layer.pick(ndcFor(event))
    if (!hit) { options.onMeasureMiss?.(); return }
    // Clicking the first vertex again closes a polygon.
    if (measure.kind !== 'distance' && canFinish(measure.kind, measure.points)) {
      const first = screenPositionOf(measure.points[0])
      const rect = renderer.domElement.getBoundingClientRect()
      if (first && Math.hypot(first.x - (event.clientX - rect.left), first.y - (event.clientY - rect.top)) <= CLOSE_PX) {
        finishMeasurement()
        return
      }
    }
    const last = measure.points[measure.points.length - 1]
    if (last && Math.hypot(last[0] - hit.native[0], last[1] - hit.native[1], last[2] - hit.native[2]) < 1e-6) return
    measure.points = [...measure.points, hit.native]
    updateDrawing()
  }

  function updateDrawing() {
    measure.text = measure.kind ? measurementText(measure.kind, measure.points) : ''
    layer?.setDrawing(measure.points, measure.kind !== 'distance')
    requestRender()
  }

  /** Activate a measurement tool (distance | area | volume); null switches measuring off. */
  function startMeasurement(kind) {
    measure.kind = kind || null
    measure.points = []
    updateDrawing()
  }

  /** Drop the shape being drawn; a second call (nothing drawn) leaves the tool. */
  function cancelMeasurement() {
    if (measure.points.length) {
      measure.points = []
    } else {
      measure.kind = null
    }
    measure.text = ''
    layer?.setDrawing(null)
    requestRender()
  }

  /** Complete the shape being drawn (Enter / double-click / closing click). */
  function finishMeasurement() {
    const kind = measure.kind
    if (!kind || !canFinish(kind, measure.points)) return false
    const points = measure.points.slice()
    const id = `m${++measureSeq}`
    const entry = {
      id, kind, points,
      text: kind === 'volume' ? 'Computing volume…' : measurementText(kind, points),
      status: kind === 'volume' ? 'pending' : 'ready',
    }
    measure.measurements = [...measure.measurements, entry]
    measure.points = []
    measure.text = ''
    layer?.setDrawing(null)
    layer?.setMeasurements(measure.measurements)
    requestRender()
    if (kind === 'volume') {
      const done = (text, status) => {
        measure.measurements = measure.measurements.map(m => (m.id === id ? { ...m, text, status } : m))
        requestRender()
      }
      if (!options.onVolume) done('Volume needs a DSM', 'error')
      else {
        Promise.resolve()
          .then(() => options.onVolume(points))
          .then(text => done(text || 'Volume unavailable', 'ready'))
          .catch(e => done(`Volume failed: ${e?.message || e}`, 'error'))
      }
    }
    return true
  }

  function removeMeasurement(id) {
    measure.measurements = measure.measurements.filter(m => m.id !== id)
    layer?.setMeasurements(measure.measurements)
    requestRender()
  }

  function clearMeasurements() {
    measure.measurements = []
    measure.points = []
    measure.text = ''
    layer?.setDrawing(null)
    layer?.setMeasurements([])
    requestRender()
  }

  function screenPositionOf(native) {
    if (!renderer || !layer) return null
    return layer.project(native, renderer.domElement.clientWidth, renderer.domElement.clientHeight)
  }

  /** Where each measurement's readout sits on screen (anchored on the shape). */
  function updateLabels() {
    if (!measure.measurements.length && !measure.points.length) {
      if (measure.labels.length) measure.labels = []
      return
    }
    const labels = []
    for (const m of measure.measurements) {
      const anchor = m.kind === 'distance' ? m.points[m.points.length - 1] : centroid(m.points)
      const pos = screenPositionOf(anchor)
      if (pos) labels.push({ id: m.id, kind: m.kind, text: m.text, status: m.status, x: pos.x, y: pos.y })
    }
    if (measure.points.length) {
      const pos = screenPositionOf(measure.points[measure.points.length - 1])
      if (pos) labels.push({ id: 'drawing', kind: measure.kind, text: measure.text, status: 'drawing', x: pos.x, y: pos.y })
    }
    measure.labels = labels
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
      case 'finishMeasure': return finishMeasurement()
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
      kind: state.kind,
      up,
      background: scene?.background?.getHexString?.() ?? null,
      pointCloud: {
        totalPoints: pointCloud.totalPoints,
        visiblePoints: pointCloud.visiblePoints,
        visibleNodes: pointCloud.visibleNodes,
        loadingNodes: pointCloud.loadingNodes,
        colorMode: pointCloud.colorMode,
        origin: pointCloud.origin.slice(),
        hasMetadata: !!pointCloud.metadata,
      },
      measure: {
        kind: measure.kind,
        drawing: measure.points.length,
        measurements: measure.measurements.map(m => ({ kind: m.kind, text: m.text, status: m.status, points: m.points.length })),
        labels: measure.labels.length,
      },
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
      renderer.domElement.removeEventListener('pointerdown', onPointerDown)
      renderer.domElement.removeEventListener('pointerup', onPointerUp)
      renderer.renderLists.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
      renderer = null
    }
    layer = null
    scene = null
    camera = null
  }

  return {
    state: readonly(state),
    pointCloud: readonly(pointCloud),
    measure: readonly(measure),
    init,
    load,
    loadPointCloud,
    setPointCloudOption,
    startMeasurement,
    cancelMeasurement,
    finishMeasurement,
    removeMeasurement,
    clearMeasurements,
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
