// Potree 2.0 point cloud layer for the 3D viewer. Owns the potree-core
// instance, the octree object, its material settings, picking and the
// measurement geometry; useModelViewer.js drives it (camera, render loop,
// pointer events) and pages/ModelView.vue only sees reactive state.
//
// potree-core 2.0.15 against three 0.185 needs three small corrections, all
// applied at runtime on the material (verified in headless Chrome, see
// docs/point-cloud/README.md "Loader notes"):
//   1. Its Potree 2 vertex path ignores the colour type (`vColor = rgba`); the
//      define block is rewritten so RGB / elevation / intensity /
//      classification / flat colour all work on the new format.
//   2. Hidden classes (LUT alpha 0) are only culled while colouring by
//      classification; the guard is widened so the class filter applies in
//      every colour mode.
//   3. The default sRGB->linear encoding pass washes Potree 2 colours out to
//      white on ANGLE; both encodings are set to linear (the data is sRGB and
//      the canvas is sRGB, so nothing needs converting).
// Elevation filtering uses a clip box (public API) rather than clipping
// planes, whose shader define potree-core forgets to set.
import * as THREE from 'three'
import { ClipMode, PointColorType, PointShape, PointSizeType, Potree, createClipBox } from 'potree-core'
import {
  CLASSIFICATION_COLORS,
  classificationColor,
  classificationsPresent,
  createPotreeRequestManager,
  elevationRangeOf,
  intensityRangeOf,
  potreeBaseUrl,
} from '@/lib/potree'

const MEASURE_COLOR = 0xf59e0b
const DRAWING_COLOR = 0x38bdf8

const COLOR_TYPES = {
  rgb: PointColorType.RGB,
  elevation: PointColorType.ELEVATION,
  intensity: PointColorType.INTENSITY,
  classification: PointColorType.CLASSIFICATION,
}

const NEW_FORMAT_COLOR_BLOCK = `#ifdef new_format
	#if defined color_type_rgb
		vColor = vec4(rgba.rgb, 1.0);
	#elif defined color_type_height
		vColor = vec4(getElevation(), 1.0);
	#elif defined color_type_intensity
		float wIntensity = getIntensity();
		vColor = vec4(wIntensity, wIntensity, wIntensity, 1.0);
	#elif defined color_type_classification
		vec4 classColor = getClassification();
		vColor = vec4(classColor.rgb, 1.0);
	#elif defined color_type_color
		vColor = vec4(uColor, 1.0);
	#else
		vColor = vec4(rgba.rgb, 1.0);
	#endif
#elif defined color_type_rgb`

const NEW_FORMAT_COLOR_RE = /#ifdef new_format\r?\n\s*vColor = rgba;\r?\n\s*#elif defined color_type_rgb/
const CLASS_CULL_GUARD = '#if !defined color_type_composite && defined color_type_classification'
const CLASS_CULL_GUARD_ALL_MODES = '#if !defined color_type_composite'

/** Rewrites potree-core's vertex shader so colour modes and class culling work on Potree 2 octrees. */
export function patchMaterial(material) {
  if (material.__webodmPatched) return material
  const original = material.applyDefines.bind(material)
  material.applyDefines = src => original(
    src.replace(NEW_FORMAT_COLOR_RE, NEW_FORMAT_COLOR_BLOCK).replace(CLASS_CULL_GUARD, CLASS_CULL_GUARD_ALL_MODES),
  )
  material.inputColorEncoding = 0 // ColorEncoding.LINEAR
  material.outputColorEncoding = 0
  material.__webodmPatched = true
  material.updateShaderSource()
  return material
}

export function createPointCloudLayer({ scene, getCamera, getRenderer, requestRender }) {
  const potree = new Potree()
  const group = new THREE.Group()
  group.name = 'pointcloud'
  const measureGroup = new THREE.Group()
  measureGroup.name = 'measurements'
  group.add(measureGroup)

  let pco = null
  let metadata = null
  let origin = [0, 0, 0] // native coordinates of the local frame's origin
  let localBox = new THREE.Box3() // tight bounds in the local (centred) frame
  let lastVisibleNodes = -1
  let classFilter = null // Set of hidden class codes
  let colorMode = 'rgb'
  let drawingObjects = null
  const measurementObjects = new Map()

  async function load(taskName, files, { signal } = {}) {
    dispose()
    const requestManager = createPotreeRequestManager(files, (url, init) => fetch(url, { ...init, signal }))
    const cloud = await potree.loadPointCloud(potreeBaseUrl(taskName), requestManager)
    if (signal?.aborted) { cloud.dispose(); throw new DOMException('aborted', 'AbortError') }
    pco = cloud
    metadata = cloud.pcoGeometry?.loader?.metadata || {}

    const bbMin = cloud.pcoGeometry.boundingBox.min.clone()
    const bbMax = cloud.pcoGeometry.boundingBox.max.clone()
    const nativeMin = metadata.boundingBox?.min || [0, 0, 0]
    // Tight bounds (position attribute min/max) when the converter wrote them, else the cube.
    const pos = (metadata.attributes || []).find(a => a.name === 'position')
    const tightMin = pos?.min?.length === 3 ? new THREE.Vector3(...pos.min).sub(new THREE.Vector3(...nativeMin)) : bbMin
    const tightMax = pos?.max?.length === 3 ? new THREE.Vector3(...pos.max).sub(new THREE.Vector3(...nativeMin)) : bbMax
    const center = tightMin.clone().add(tightMax).multiplyScalar(0.5)
    cloud.position.copy(center).negate()
    cloud.updateMatrixWorld(true)
    origin = [nativeMin[0] + center.x, nativeMin[1] + center.y, nativeMin[2] + center.z]
    localBox = new THREE.Box3(tightMin.clone().sub(center), tightMax.clone().sub(center))

    const material = patchMaterial(cloud.material)
    material.pointSizeType = PointSizeType.FIXED
    material.shape = PointShape.CIRCLE
    material.minSize = 1
    material.maxSize = 32
    const [iMin, iMax] = intensityRangeOf(metadata)
    material.intensityRange = [iMin, iMax]
    material.elevationRange = [localBox.min.z, localBox.max.z]
    material.updateShaderSource()

    group.add(cloud)
    scene.add(group)
    lastVisibleNodes = -1
    requestRender()
    return { metadata, origin, box: localBox.clone() }
  }

  /** Per-frame LOD update. Returns true while nodes are still streaming in. */
  function update() {
    const camera = getCamera(), renderer = getRenderer()
    if (!pco || !camera || !renderer) return false
    const result = potree.updatePointClouds([pco], camera, renderer)
    const loading = pco.pcoGeometry.numNodesLoading > 0 || result.exceededMaxLoadsToGPU
    if (loading || pco.visibleNodes.length !== lastVisibleNodes) {
      lastVisibleNodes = pco.visibleNodes.length
      requestRender()
    }
    return loading
  }

  function stats() {
    if (!pco) return { visiblePoints: 0, visibleNodes: 0, loadingNodes: 0, totalPoints: 0 }
    return {
      visiblePoints: pco.numVisiblePoints,
      visibleNodes: pco.visibleNodes.length,
      loadingNodes: pco.pcoGeometry.numNodesLoading,
      totalPoints: metadata?.points || 0,
    }
  }

  // ------------------------------------------------------------ settings

  function setColorMode(mode) {
    if (!pco) return
    colorMode = mode
    pco.material.pointColorType = COLOR_TYPES[mode] ?? PointColorType.RGB
    if (mode === 'elevation') pco.material.elevationRange = [localBox.min.z, localBox.max.z]
    requestRender()
  }

  function setPointSize(px) {
    if (!pco) return
    pco.material.size = px
    requestRender()
  }

  function setPointBudget(points) {
    potree.pointBudget = points
    requestRender()
  }

  /** Keep only points whose native elevation is within [min, max]; null = no filter. */
  function setElevationFilter(nativeRange) {
    if (!pco) return
    const m = pco.material
    if (!nativeRange) {
      m.setClipBoxes([])
      m.clipMode = ClipMode.DISABLED
      requestRender()
      return
    }
    const zMin = nativeRange[0] - origin[2]
    const zMax = Math.max(nativeRange[1] - origin[2], zMin + 1e-3)
    const size = localBox.getSize(new THREE.Vector3())
    const box = createClipBox(
      new THREE.Vector3(size.x + 2, size.y + 2, zMax - zMin),
      new THREE.Vector3(0, 0, (zMin + zMax) / 2),
    )
    m.setClipBoxes([box])
    m.clipMode = ClipMode.CLIP_OUTSIDE
    requestRender()
  }

  /** Hide the given class codes (applies in every colour mode). */
  function setHiddenClasses(codes) {
    if (!pco) return
    classFilter = codes?.size ? new Set(codes) : null
    const lut = {}
    const present = classificationsPresent(metadata)
    const codesToColor = present.length ? present : Object.keys(CLASSIFICATION_COLORS).filter(k => k !== 'DEFAULT').map(Number)
    for (const code of codesToColor) {
      const [r, g, b] = classificationColor(code)
      lut[code] = new THREE.Vector4(r, g, b, classFilter?.has(code) ? 0 : 1)
    }
    const d = CLASSIFICATION_COLORS.DEFAULT
    lut.DEFAULT = new THREE.Vector4(d[0], d[1], d[2], 1)
    pco.material.classification = lut
    requestRender()
  }

  function setBackground(hex) {
    scene.background = new THREE.Color(hex)
    requestRender()
  }

  // ------------------------------------------------------------- picking

  /** Nearest point under the pointer: `{ local: Vector3, native: [x,y,z] }` or null. */
  function pick(ndc, { windowSize = 15 } = {}) {
    const camera = getCamera(), renderer = getRenderer()
    if (!pco || !camera || !renderer) return null
    const ray = new THREE.Raycaster()
    ray.setFromCamera(ndc, camera)
    const hit = pco.pick(renderer, camera, ray.ray, { pickWindowSize: windowSize })
    if (!hit?.position) return null
    const local = hit.position.clone()
    return { local, native: [local.x + origin[0], local.y + origin[1], local.z + origin[2]] }
  }

  // -------------------------------------------------------- measurements

  function makeObjects(nativePoints, closed, color) {
    const pts = nativePoints.map(p => new THREE.Vector3(p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]))
    const linePts = closed && pts.length > 2 ? [...pts, pts[0]] : pts
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(linePts.length ? linePts : [new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.95 }),
    )
    const dots = new THREE.Points(
      new THREE.BufferGeometry().setFromPoints(pts.length ? pts : [new THREE.Vector3()]),
      new THREE.PointsMaterial({ color, size: 9, sizeAttenuation: false, depthTest: false, transparent: true }),
    )
    line.renderOrder = 20
    dots.renderOrder = 21
    line.visible = linePts.length > 1
    dots.visible = pts.length > 0
    const obj = new THREE.Group()
    obj.add(line, dots)
    return obj
  }

  function disposeObject(obj) {
    obj?.traverse(o => { o.geometry?.dispose?.(); o.material?.dispose?.() })
    if (obj?.parent) obj.parent.remove(obj)
  }

  function setDrawing(nativePoints, closed) {
    disposeObject(drawingObjects)
    drawingObjects = null
    if (nativePoints?.length) {
      drawingObjects = makeObjects(nativePoints, closed, DRAWING_COLOR)
      measureGroup.add(drawingObjects)
    }
    requestRender()
  }

  function setMeasurements(list) {
    const keep = new Set()
    for (const m of list) {
      keep.add(m.id)
      if (!measurementObjects.has(m.id)) {
        const obj = makeObjects(m.points, m.kind !== 'distance', MEASURE_COLOR)
        measurementObjects.set(m.id, obj)
        measureGroup.add(obj)
      }
    }
    for (const [id, obj] of measurementObjects) {
      if (!keep.has(id)) { disposeObject(obj); measurementObjects.delete(id) }
    }
    requestRender()
  }

  /** Screen position (px, relative to the canvas) of a native point, or null when behind the camera. */
  function project(native, width, height) {
    const camera = getCamera()
    if (!camera) return null
    const v = new THREE.Vector3(native[0] - origin[0], native[1] - origin[1], native[2] - origin[2]).project(camera)
    if (v.z > 1) return null
    return { x: (v.x + 1) / 2 * width, y: (1 - v.y) / 2 * height }
  }

  function dispose() {
    setDrawing(null)
    setMeasurements([])
    if (pco) {
      group.remove(pco)
      pco.dispose()
      pco = null
    }
    if (group.parent) group.parent.remove(group)
    metadata = null
    lastVisibleNodes = -1
  }

  return {
    load, update, stats, dispose, pick, project,
    setColorMode, setPointSize, setPointBudget, setElevationFilter, setHiddenClasses, setBackground,
    setDrawing, setMeasurements,
    get metadata() { return metadata },
    get origin() { return origin },
    get box() { return localBox },
    get colorMode() { return colorMode },
    get loaded() { return !!pco },
  }
}
