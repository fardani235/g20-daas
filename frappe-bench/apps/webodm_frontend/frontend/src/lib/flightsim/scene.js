// The simulation in 3D (three.js).
//
// A thin shell: the terrain mesh, the path, the photo positions and the
// camera poses are computed by `sceneGeometry.js` and `cameraRig.js` (plain
// arrays, tested without a GPU); this module puts them in buffers, lights
// them and draws the state it is handed by `update(state)` - the same state
// object the 2D map draws in the same frame. It keeps no clock and makes no
// decision about the flight.
//
// three.js rather than a globe engine: the app already ships it (the model
// and point cloud viewers), a mission is at most 60 km across so a local
// metric scene is exact where a globe adds nothing, the terrain must be the
// planner's own grid rather than a third-party tileset, and imagery loads
// as plain images, which the deployment's content security policy already
// allows. Nothing here needs a token, a new service or a policy change.
//
// Loaded on demand (the 3D pane is an async component), so the planner
// page does not pay for three.js until a simulation is opened.

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js'
import { SHUTTER_FLASH_S } from './camera'
import {
  aircraftPosition, aircraftScale, defaultTopHeight, followOffset, gimbalFraming, isAttached, rigPose,
} from './cameraRig'
import { drawTiles, orthoTemplate, tilePlan } from './imagery'
import {
  drapedRings, flownPieces, gridUV, groundGrid, groundHeightFn, groundMesh, pathPieces, photoPoints, sceneBounds,
  skirtMesh, waypointPoints,
} from './sceneGeometry'

const SKY = 0xcfe3f5
const GROUND_FILL = '#7d8f77'
const TRACK = 0x22d3ee
const FOOTPRINT = 0x10b981
const DEFAULT_FOV = 50
const LIFT = 0.8          // metres: lines laid on the ground are lifted so they do not sink into it

export class WebGLUnavailable extends Error {}

// A recognisable aircraft about a metre across; `aircraftScale` enlarges it with distance.
function buildAircraft(hovers) {
  const group = new THREE.Group()
  const body = new THREE.MeshLambertMaterial({ color: 0x1f2937 })
  const accent = new THREE.MeshLambertMaterial({ color: 0x0ea5e9 })
  const nose = new THREE.MeshLambertMaterial({ color: 0xef4444 })
  const rotors = []
  const add = (geometry, material, x, y, z, parent = group) => {
    const mesh = new THREE.Mesh(geometry, material)
    mesh.position.set(x, y, z)
    parent.add(mesh)
    return mesh
  }
  if (hovers) {
    add(new THREE.BoxGeometry(0.26, 0.11, 0.4), accent, 0, 0, 0)
    add(new THREE.BoxGeometry(0.12, 0.06, 0.12), nose, 0, 0, -0.26)                // the nose points -z (north at heading 0)
    add(new THREE.SphereGeometry(0.07, 12, 8), body, 0, -0.1, -0.12)               // the camera gimbal
    const arm = new THREE.BoxGeometry(1.0, 0.035, 0.05)
    add(arm, body, 0, 0.02, 0).rotation.y = Math.PI / 4
    add(arm, body, 0, 0.02, 0).rotation.y = -Math.PI / 4
    const disc = new THREE.CylinderGeometry(0.24, 0.24, 0.012, 20)
    const discMaterial = new THREE.MeshLambertMaterial({ color: 0xf8fafc, transparent: true, opacity: 0.55 })
    const blade = new THREE.BoxGeometry(0.46, 0.014, 0.04)
    for (const [sx, sz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const rotor = new THREE.Group()
      rotor.position.set(sx * 0.354, 0.07, sz * 0.354)
      add(disc, discMaterial, 0, 0, 0, rotor)
      add(blade, body, 0, 0.004, 0, rotor)
      group.add(rotor)
      rotors.push(rotor)
    }
  } else {
    add(new THREE.BoxGeometry(0.12, 0.11, 0.95), accent, 0, 0, 0)
    add(new THREE.BoxGeometry(0.1, 0.09, 0.14), nose, 0, 0, -0.52)
    add(new THREE.BoxGeometry(1.7, 0.03, 0.3), body, 0, 0.03, -0.05)               // wing
    add(new THREE.BoxGeometry(0.55, 0.025, 0.16), body, 0, 0.03, 0.42)             // tailplane
    add(new THREE.BoxGeometry(0.025, 0.2, 0.18), body, 0, 0.12, 0.42)              // fin
    const prop = new THREE.Group()
    prop.position.set(0, 0, -0.6)
    prop.rotation.x = Math.PI / 2
    add(new THREE.BoxGeometry(0.34, 0.012, 0.035), body, 0, 0, 0, prop)
    group.add(prop)
    rotors.push(prop)
  }
  // Stand on the ground rather than in it: the model's origin is under its belly.
  for (const child of group.children) child.position.y += 0.14
  group.rotation.order = 'YXZ'
  return { group, rotors }
}

/**
 * Create the 3D view in `container`. Options: `coverageCanvas` (the shared
 * coverage raster), `basemap` (`{ url, maxZoom }`), `ortho` (`{ task }` to
 * lay that task's orthophoto over the basemap), `mode` (initial camera
 * mode), `onStatus(status)` (imagery progress).
 *
 * Throws `WebGLUnavailable` when the browser cannot give a WebGL context.
 */
export function createSimScene(container, simulation, options = {}) {
  const { trajectory, coverage, camera: cameraModel } = simulation
  let renderer
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
  } catch (e) {
    throw new WebGLUnavailable(e?.message || 'WebGL is not available')
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.domElement.style.cssText = 'display:block;width:100%;height:100%;touch-action:none'
  renderer.domElement.setAttribute('aria-label', '3D view of the simulated flight')
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(SKY)
  const camera = new THREE.PerspectiveCamera(DEFAULT_FOV, 1, 1, 100000)
  const disposables = []
  const own = thing => { disposables.push(thing); return thing }
  const lineMaterials = []

  // -- ground ------------------------------------------------------------------------
  const grid = groundGrid(simulation)
  const heightAt = groundHeightFn(simulation, grid)
  const bounds = sceneBounds(grid, trajectory)
  const center = new THREE.Vector3(...bounds.center)

  scene.add(new THREE.HemisphereLight(0xffffff, 0x9a968a, 1.6))
  const sun = new THREE.DirectionalLight(0xffffff, 1.5)
  sun.position.set(-0.6, 1.4, 0.5)                      // high in the south-west
  scene.add(sun)
  scene.fog = new THREE.Fog(SKY, bounds.radius * 5, bounds.radius * 16)

  // Up to 8 x 8 tiles: a 2048 px mosaic, sharp enough to fly over at survey height.
  const plan = tilePlan(grid.bounds, { maxTiles: 64, maxZoom: options.basemap?.maxZoom || 19 })
  const imageryCanvas = document.createElement('canvas')
  imageryCanvas.width = plan.width
  imageryCanvas.height = plan.height
  const imageryCtx = imageryCanvas.getContext('2d')
  const imagery = own(new THREE.CanvasTexture(imageryCanvas))
  imagery.colorSpace = THREE.SRGBColorSpace
  imagery.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy())
  imagery.generateMipmaps = true

  const blank = own(new THREE.DataTexture(new Uint8Array([0, 0, 0, 0]), 1, 1))
  blank.needsUpdate = true
  const coverageTexture = options.coverageCanvas && coverage ? own(new THREE.CanvasTexture(options.coverageCanvas)) : null
  if (coverageTexture) {
    coverageTexture.colorSpace = THREE.SRGBColorSpace
    coverageTexture.generateMipmaps = false
    coverageTexture.minFilter = THREE.LinearFilter
  }
  const coverageUniforms = { coverageMap: { value: coverageTexture || blank }, coverageOpacity: { value: 0 } }

  const mesh = groundMesh(grid)
  const groundGeometry = own(new THREE.BufferGeometry())
  groundGeometry.setAttribute('position', new THREE.BufferAttribute(mesh.positions, 3))
  groundGeometry.setAttribute('uv', new THREE.BufferAttribute(gridUV(grid, plan.box), 2))
  groundGeometry.setAttribute('uvCoverage', new THREE.BufferAttribute(
    coverage ? gridUV(grid, coverage.bounds) : new Float32Array(grid.x.length * 2), 2))
  groundGeometry.setIndex(new THREE.BufferAttribute(mesh.indices, 1))
  groundGeometry.computeVertexNormals()
  const groundMaterial = own(new THREE.MeshLambertMaterial({ color: 0xffffff, map: imagery }))
  // The coverage raster is blended into the ground's own colour: one surface,
  // so it can never flicker against the terrain the way a second mesh would.
  groundMaterial.onBeforeCompile = shader => {
    shader.uniforms.coverageMap = coverageUniforms.coverageMap
    shader.uniforms.coverageOpacity = coverageUniforms.coverageOpacity
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nattribute vec2 uvCoverage;\nvarying vec2 vCoverageUv;')
      .replace('#include <uv_vertex>', '#include <uv_vertex>\nvCoverageUv = uvCoverage;')
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform sampler2D coverageMap;\nuniform float coverageOpacity;\nvarying vec2 vCoverageUv;')
      .replace('#include <map_fragment>', '#include <map_fragment>\n'
        + 'vec4 coverageTexel = texture2D( coverageMap, vCoverageUv );\n'
        + 'diffuseColor.rgb = mix( diffuseColor.rgb, coverageTexel.rgb, coverageTexel.a * coverageOpacity );')
  }
  scene.add(new THREE.Mesh(groundGeometry, groundMaterial))

  const floor = bounds.bottom - Math.max(15, bounds.radius * 0.04)
  const skirt = skirtMesh(grid, floor)
  const skirtGeometry = own(new THREE.BufferGeometry())
  skirtGeometry.setAttribute('position', new THREE.BufferAttribute(skirt.positions, 3))
  skirtGeometry.setIndex(new THREE.BufferAttribute(skirt.indices, 1))
  // The terrain is a block on a pale table: beyond the elevation grid nothing is known, and nothing is drawn.
  scene.add(new THREE.Mesh(skirtGeometry, own(new THREE.MeshBasicMaterial({ color: 0x8a8f85, side: THREE.DoubleSide }))))
  const apron = new THREE.Mesh(
    own(new THREE.CircleGeometry(bounds.radius * 20, 48)),
    own(new THREE.MeshBasicMaterial({ color: 0xdde6ee })))
  apron.rotation.x = -Math.PI / 2
  apron.position.set(center.x, floor - 0.5, center.z)
  scene.add(apron)

  // -- imagery: the basemap, then (optionally) the project's orthophoto over it ----------------
  const status = { imagery: 'loading', tiles: 0, loaded: 0, failed: 0 }
  let imageryRun = 0
  let disposed = false
  function setImagery({ basemap = options.basemap, ortho = options.ortho } = {}) {
    options.basemap = basemap
    options.ortho = ortho
    const run = ++imageryRun
    const cancelled = () => disposed || run !== imageryRun
    imageryCtx.fillStyle = GROUND_FILL
    imageryCtx.fillRect(0, 0, plan.width, plan.height)
    imagery.needsUpdate = true
    Object.assign(status, { imagery: basemap?.url ? 'loading' : 'none', tiles: plan.nx * plan.ny, loaded: 0, failed: 0 })
    report()
    if (!basemap?.url) return Promise.resolve(status)
    const refresh = () => { status.loaded++; imagery.needsUpdate = true; invalidate() }
    return drawTiles(imageryCanvas, plan, basemap.url, { onTile: refresh, isCancelled: cancelled })
      .then(result => {
        if (cancelled()) return status
        status.failed = result.failed
        status.imagery = result.loaded ? 'ready' : 'unavailable'
        report()
        if (!ortho?.task) return status
        return drawTiles(imageryCanvas, plan, orthoTemplate(ortho.task), {
          onTile: () => { imagery.needsUpdate = true; invalidate() }, isCancelled: cancelled, crossOrigin: null,
        }).then(() => status)
      })
  }
  const report = () => { if (options.onStatus) options.onStatus({ ...status }) }

  // -- the route ----------------------------------------------------------------------------
  const pieces = pathPieces(trajectory)
  const fat = (positions, colors, params) => {
    const geometry = own(new LineSegmentsGeometry())
    geometry.setPositions(positions)
    if (colors) geometry.setColors(colors)
    const material = own(new LineMaterial({ linewidth: 3, ...params }))
    lineMaterials.push(material)
    const line = new LineSegments2(geometry, material)
    line.frustumCulled = false
    return line
  }
  const planned = fat(pieces.positions, pieces.colors, { vertexColors: true, linewidth: 2.5, transparent: true, opacity: 0.8 })
  scene.add(planned)
  // Drawn in the same pass as the planned path (transparent) so that the render order decides: flown on top.
  const flown = fat(pieces.positions, null, { color: TRACK, linewidth: 4.5, transparent: true })
  flown.geometry.instanceCount = 0
  flown.renderOrder = 2
  scene.add(flown)
  const partialPositions = new Float32Array(6)
  const partial = fat(partialPositions, null, { color: TRACK, linewidth: 4.5, transparent: true })
  partial.renderOrder = 2
  partial.visible = false
  scene.add(partial)

  const points = (positions, params) => {
    const geometry = own(new THREE.BufferGeometry())
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    const cloud = new THREE.Points(geometry, own(new THREE.PointsMaterial({ sizeAttenuation: false, ...params })))
    cloud.frustumCulled = false
    return cloud
  }
  const waypoints = points(waypointPoints(trajectory), { color: 0xf59e0b, size: 6 })
  scene.add(waypoints)
  const photos = points(photoPoints(trajectory), { color: 0xffffff, size: 3.5 })
  photos.geometry.setDrawRange(0, 0)
  scene.add(photos)

  const boundaryMaterial = own(new THREE.LineBasicMaterial({ color: 0x2563eb }))
  for (const ring of drapedRings(trajectory.mission, trajectory.frame, heightAt, { lift: LIFT })) {
    const geometry = own(new THREE.BufferGeometry())
    geometry.setAttribute('position', new THREE.BufferAttribute(ring, 3))
    scene.add(new THREE.Line(geometry, boundaryMaterial))
  }

  // The takeoff point: a ring on the ground, by definition at height zero.
  const pad = new THREE.Mesh(
    own(new THREE.RingGeometry(3, 5, 32)),
    own(new THREE.MeshBasicMaterial({ color: 0x7c3aed, side: THREE.DoubleSide })))
  pad.rotation.x = -Math.PI / 2
  pad.position.set(trajectory.takeoff.x, heightAt(trajectory.takeoff.x, trajectory.takeoff.y) + LIFT, -trajectory.takeoff.y)
  scene.add(pad)

  // -- the aircraft, its camera's view and its height above the ground ---------------------------
  const aircraft = buildAircraft(trajectory.hovers)
  aircraft.group.traverse(o => { if (o.isMesh) { own(o.geometry); own(o.material) } })
  scene.add(aircraft.group)

  const frustumPositions = new Float32Array(8 * 6)       // 4 rays and the 4 sides of the footprint
  const frustumGeometry = own(new THREE.BufferGeometry())
  frustumGeometry.setAttribute('position', new THREE.BufferAttribute(frustumPositions, 3))
  const frustumMaterial = own(new THREE.LineBasicMaterial({ color: FOOTPRINT, transparent: true, opacity: 0.9 }))
  const frustum = new THREE.LineSegments(frustumGeometry, frustumMaterial)
  frustum.frustumCulled = false
  scene.add(frustum)
  const fillPositions = new Float32Array(4 * 3)
  const fillGeometry = own(new THREE.BufferGeometry())
  fillGeometry.setAttribute('position', new THREE.BufferAttribute(fillPositions, 3))
  fillGeometry.setIndex([0, 1, 2, 0, 2, 3])
  const fillMaterial = own(new THREE.MeshBasicMaterial({
    color: FOOTPRINT, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false,
  }))
  const fill = new THREE.Mesh(fillGeometry, fillMaterial)
  fill.frustumCulled = false
  scene.add(fill)

  const dropPositions = new Float32Array(6)
  const dropGeometry = own(new THREE.BufferGeometry())
  dropGeometry.setAttribute('position', new THREE.BufferAttribute(dropPositions, 3))
  const drop = new THREE.Line(dropGeometry, own(new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 4, gapSize: 3, transparent: true, opacity: 0.8 })))
  drop.frustumCulled = false
  scene.add(drop)

  // -- camera -------------------------------------------------------------------------------------
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.12
  controls.maxPolarAngle = Math.PI * 0.495
  controls.minDistance = 3
  controls.maxDistance = bounds.radius * 12
  controls.addEventListener('change', () => { dirty = true })

  const layers = { path: true, waypoints: true, photos: true, footprint: true, coverage: 'covered', ...(options.layers || {}) }
  let mode = options.mode || 'follow'
  let state = null
  let dirty = true
  let frames = 0
  let topHeight = null
  const position = new THREE.Vector3()
  const lastPosition = new THREE.Vector3()
  const offset = new THREE.Vector3()
  const tmp = new THREE.Vector3()
  const invalidate = () => { dirty = true }

  function frameSite() {
    camera.position.set(center.x + bounds.radius * 0.7, bounds.top + bounds.radius * 0.9, center.z + bounds.radius * 1.1)
    controls.target.copy(center)
    controls.update()
  }

  function applyMode(next, previous) {
    mode = next
    const attached = isAttached(mode)
    controls.enabled = !attached
    aircraft.group.visible = mode !== 'fpv' && mode !== 'gimbal'
    drop.visible = aircraft.group.visible
    camera.up.set(0, 1, 0)
    if (!attached) camera.fov = DEFAULT_FOV
    if (mode === 'follow' && state) {
      if (previous === 'follow') return
      offset.set(...followOffset(state.heading, bounds.radius))
      camera.position.copy(position).add(offset)
      controls.target.copy(position)
      controls.update()
    } else if (mode === 'free' && (previous === undefined || isAttached(previous))) {
      frameSite()
    }
    if (mode === 'top' && state && topHeight === null) topHeight = defaultTopHeight(state, state.footprintAcross)
    camera.updateProjectionMatrix()
    dirty = true
  }

  function onWheel(e) {
    if (mode !== 'top') return
    e.preventDefault()
    topHeight = Math.min(Math.max((topHeight || 300) * (e.deltaY > 0 ? 1.15 : 1 / 1.15), 20), bounds.radius * 12)
    if (state) update(state)
  }
  renderer.domElement.addEventListener('wheel', onWheel, { passive: false })

  function placeCamera() {
    if (!state) return
    if (mode === 'follow') {
      // Carry the user's orbit along with the aircraft.
      camera.position.add(tmp.copy(position).sub(lastPosition))
      controls.target.copy(position)
      return
    }
    if (!isAttached(mode)) return
    const size = renderer.getSize(new THREE.Vector2())
    const pose = rigPose(mode, state, cameraModel, { topHeight: topHeight || 300, viewAspect: size.x / Math.max(1, size.y) })
    camera.position.set(...pose.position)
    camera.up.set(...pose.up)
    camera.lookAt(pose.target[0], pose.target[1], pose.target[2])
    if (camera.fov !== pose.fov) { camera.fov = pose.fov; camera.updateProjectionMatrix() }
  }

  // The depth buffer's precision follows what the camera is looking at: near
  // plane close for the nose view, far for an overview of a 60 km site.
  function fitDepth() {
    const focus = mode === 'free' ? controls.target : position
    const distance = isAttached(mode) && mode !== 'top' ? Math.max(state?.aglAssumed || 50, 20) : camera.position.distanceTo(focus)
    const near = Math.min(Math.max(distance / 250, 0.25), 60)
    const far = Math.max(distance * 60, bounds.radius * 24, 4000)
    if (Math.abs(near - camera.near) > camera.near * 0.2 || Math.abs(far - camera.far) > camera.far * 0.2) {
      camera.near = near
      camera.far = far
      camera.updateProjectionMatrix()
    }
  }

  /** Draw a sampled state. */
  function update(next) {
    if (disposed) return
    const first = !state
    state = next
    lastPosition.copy(position)
    position.set(...aircraftPosition(state))
    if (first) {
      lastPosition.copy(position)
      applyMode(mode)
    }

    aircraft.group.position.copy(position)
    aircraft.group.rotation.set(state.pitch * Math.PI / 180, -state.heading * Math.PI / 180, -state.roll * Math.PI / 180)
    const spin = state.airborne ? state.t * 38 : 0
    aircraft.rotors.forEach((rotor, i) => {
      if (trajectory.hovers) rotor.rotation.y = spin * (i % 2 ? -1 : 1)
      else rotor.rotation.y = spin
    })

    // Flown so far: every finished piece, and the one in progress up to the aircraft.
    const done = flownPieces(pieces, trajectory, state)
    flown.geometry.instanceCount = done.whole
    if (done.partial >= 0) {
      const seg = trajectory.segments
      const i = done.partial
      partialPositions.set([seg.x0[i], seg.z0[i], -seg.y0[i], position.x, position.y, position.z])
      const start = partial.geometry.attributes.instanceStart
      start.data.array.set(partialPositions)
      start.data.needsUpdate = true
      partial.visible = layers.path
    } else {
      partial.visible = false
    }
    photos.geometry.setDrawRange(0, state.photos)

    // The camera's view: rays to the footprint's corners, the footprint on the ground.
    const corners = layers.footprint ? simulation.footprintAt(state) : null
    // Through the camera itself the photo is the frame on screen; its footprint would only tint the view.
    frustum.visible = !!corners && mode !== 'gimbal'
    fill.visible = frustum.visible
    if (corners) {
      for (let k = 0; k < 4; k++) {
        const x = corners[2 * k]
        const y = corners[2 * k + 1]
        fillPositions.set([x, heightAt(x, y) + LIFT, -y], 3 * k)
      }
      for (let k = 0; k < 4; k++) {
        const m = (k + 1) % 4
        // A ray from the aircraft...
        frustumPositions.set([position.x, position.y, position.z], 12 * k)
        frustumPositions.set(fillPositions.subarray(3 * k, 3 * k + 3), 12 * k + 3)
        // ...and a side of the footprint.
        frustumPositions.set(fillPositions.subarray(3 * k, 3 * k + 3), 12 * k + 6)
        frustumPositions.set(fillPositions.subarray(3 * m, 3 * m + 3), 12 * k + 9)
      }
      frustumGeometry.attributes.position.needsUpdate = true
      fillGeometry.attributes.position.needsUpdate = true
      // Brighter for a moment when a photo is taken.
      fillMaterial.opacity = state.capturing && state.sincePhoto < SHUTTER_FLASH_S ? 0.55 : state.capturing ? 0.3 : 0.08
      frustumMaterial.opacity = state.capturing ? 0.95 : 0.4
    }

    const below = heightAt(state.x, state.y)
    dropPositions.set([position.x, position.y, position.z, position.x, below, position.z])
    dropGeometry.attributes.position.needsUpdate = true
    drop.computeLineDistances()

    placeCamera()
    dirty = true
  }

  function render() {
    if (disposed) return
    if (state && aircraft.group.visible) {
      aircraft.group.scale.setScalar(aircraftScale(camera.position.distanceTo(position)))
    }
    fitDepth()
    renderer.render(scene, camera)
    frames++
  }

  let raf = 0
  function loop() {
    raf = requestAnimationFrame(loop)
    const moved = controls.enabled ? controls.update() : false
    if (dirty || moved) {
      dirty = false
      render()
    }
  }

  function resize() {
    const w = Math.max(1, container.clientWidth)
    const h = Math.max(1, container.clientHeight)
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    for (const m of lineMaterials) m.resolution.set(w, h)
    if (state && isAttached(mode)) placeCamera()
    dirty = true
  }
  const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null
  if (observer) observer.observe(container)
  resize()
  frameSite()
  setImagery()
  loop()

  function setLayers(next) {
    Object.assign(layers, next)
    planned.visible = layers.path
    flown.visible = layers.path
    waypoints.visible = layers.waypoints
    photos.visible = layers.photos
    coverageUniforms.coverageOpacity.value = coverageTexture && layers.coverage !== 'off' ? 1 : 0
    if (state) update(state)
    dirty = true
  }
  setLayers({})

  return {
    update,
    setLayers,
    setImagery,
    /** Switch the camera: 'follow' | 'free' | 'top' | 'fpv' | 'gimbal'. */
    setMode(next) { if (next !== mode) { const previous = mode; applyMode(next, previous); if (state) update(state) } },
    getMode: () => mode,
    /** The shared coverage canvas was repainted. */
    coverageChanged() { if (coverageTexture) { coverageTexture.needsUpdate = true; dirty = true } },
    /** The photo's frame in the viewport while looking through the camera: `{ width, height }` fractions, or null. */
    gimbalFrame() {
      if (mode !== 'gimbal' || !cameraModel) return null
      const size = renderer.getSize(new THREE.Vector2())
      const f = gimbalFraming(cameraModel, size.x / Math.max(1, size.y))
      return { width: f.width, height: f.height }
    },
    resize,
    /** What is on screen, for the browser checks. */
    snapshot() {
      return {
        mode,
        frames,
        camera: { position: camera.position.toArray().map(round), fov: round(camera.fov), near: round(camera.near), far: round(camera.far) },
        aircraft: state ? { position: position.toArray().map(round), visible: aircraft.group.visible, scale: round(aircraft.group.scale.x) } : null,
        terrain: { rows: grid.rows, cols: grid.cols, flat: grid.flat, relief: round(grid.upMax - grid.upMin) },
        path: { pieces: pieces.count, flown: flown.geometry.instanceCount },
        photos: photos.geometry.drawRange.count,
        imagery: { ...status, zoom: plan.z },
        coverage: coverageUniforms.coverageOpacity.value > 0,
        draw: { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles },
      }
    },
    dispose() {
      if (disposed) return
      disposed = true
      cancelAnimationFrame(raf)
      if (observer) observer.disconnect()
      renderer.domElement.removeEventListener('wheel', onWheel)
      controls.dispose()
      for (const d of disposables) d.dispose?.()
      renderer.renderLists.dispose()
      renderer.dispose()
      renderer.forceContextLoss?.()
      renderer.domElement.remove()
    },
  }
}

const round = v => Math.round(v * 100) / 100
