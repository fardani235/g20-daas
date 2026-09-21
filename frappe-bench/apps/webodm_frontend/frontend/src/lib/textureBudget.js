// Texture memory budgeting for the 3D viewer.
//
// ODM textured models ship many large JPEG atlases (8192² and 4096² are
// common). GLTFLoader decodes every image in parallel at full size, which for
// a typical survey means 2+ GB of RGBA in RAM and VRAM at once: tabs crash on
// laptops and phones. The plugin below replaces the loader's image path so
// textures are decoded a couple at a time and downsampled at decode time to
// fit a budget derived from the device's memory. The pure helpers are
// unit-tested; the plugin itself needs a browser (createImageBitmap).

const MIPMAP_OVERHEAD = 4 / 3
const BYTES_PER_PIXEL = 4
export const TEXTURE_CAPS = [8192, 4096, 2048, 1024, 512, 256]
export const MIN_TEXTURE_CAP = 256

// Default when the browser does not report device memory (Firefox, Safari).
const ASSUMED_DEVICE_MEMORY_GB = 8
// Planning size for images whose dimensions cannot be read up front.
const ASSUMED_IMAGE_SIZE = 4096

/**
 * Total decoded-texture budget in bytes for this device.
 * `deviceMemoryGB` is navigator.deviceMemory (Chrome reports 0.25-8, capped).
 */
export function textureBudgetBytes(deviceMemoryGB, isMobile = false) {
  const gb = Number(deviceMemoryGB) || ASSUMED_DEVICE_MEMORY_GB
  let budget
  if (gb >= 8) budget = 1024
  else if (gb >= 4) budget = 512
  else budget = 256
  if (isMobile) budget = Math.min(budget, 384)
  return budget * 1024 * 1024
}

/** Decoded bytes for a list of `{width, height}` when each side is limited to `cap`. */
export function decodedBytes(dims, cap) {
  let total = 0
  for (const d of dims) {
    const w = Math.min(d?.width || ASSUMED_IMAGE_SIZE, cap)
    const h = Math.min(d?.height || ASSUMED_IMAGE_SIZE, cap)
    total += w * h * BYTES_PER_PIXEL * MIPMAP_OVERHEAD
  }
  return total
}

/**
 * Largest side length (from TEXTURE_CAPS) at which every texture fits the
 * budget together; never above `maxTextureSize`, never below MIN_TEXTURE_CAP.
 */
export function textureCapFor(dims, budgetBytes, maxTextureSize = 8192) {
  const caps = TEXTURE_CAPS.filter(c => c <= Math.max(maxTextureSize, MIN_TEXTURE_CAP))
  for (const cap of caps) {
    if (decodedBytes(dims, cap) <= budgetBytes) return cap
  }
  return caps[caps.length - 1] || MIN_TEXTURE_CAP
}

/** Target size for an image under `cap`, keeping aspect; null when no resize is needed. */
export function resizeFor(dims, cap) {
  if (!dims) return null
  const longest = Math.max(dims.width, dims.height)
  if (longest <= cap) return null
  const s = cap / longest
  return { width: Math.max(1, Math.round(dims.width * s)), height: Math.max(1, Math.round(dims.height * s)) }
}

/**
 * Pixel dimensions from the first bytes of a PNG, JPEG or WebP (VP8/VP8L/VP8X)
 * file; null when unrecognised or truncated.
 */
export function imageDimensions(bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || [])
  if (b.length < 16) return null
  const u32be = i => ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0
  const u16be = i => (b[i] << 8) | b[i + 1]
  const u16le = i => b[i] | (b[i + 1] << 8)

  // PNG: 8-byte signature, IHDR chunk with width/height at 16 and 20.
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) {
    if (b.length < 24) return null
    return { width: u32be(16), height: u32be(20) }
  }

  // JPEG: walk segments to the first SOF marker.
  if (b[0] === 0xff && b[1] === 0xd8) {
    let i = 2
    while (i + 9 < b.length) {
      if (b[i] !== 0xff) return null
      const marker = b[i + 1]
      if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0x01 || marker === 0xff) { i += marker === 0xff ? 1 : 2; continue }
      const len = u16be(i + 2)
      if ((marker >= 0xc0 && marker <= 0xcf) && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
        return { width: u16be(i + 7), height: u16be(i + 5) }
      }
      if (len < 2) return null
      i += 2 + len
    }
    return null
  }

  // WebP: RIFF....WEBP then VP8 / VP8L / VP8X chunk.
  if (b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 && b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50) {
    if (b.length < 30) return null
    const chunk = String.fromCharCode(b[12], b[13], b[14], b[15])
    if (chunk === 'VP8 ') return { width: u16le(26) & 0x3fff, height: u16le(28) & 0x3fff }
    if (chunk === 'VP8L') {
      const bits = b[21] | (b[22] << 8) | (b[23] << 16) | (b[24] << 24)
      return { width: (bits & 0x3fff) + 1, height: ((bits >> 14) & 0x3fff) + 1 }
    }
    if (chunk === 'VP8X') {
      return { width: 1 + (b[24] | (b[25] << 8) | (b[26] << 16)), height: 1 + (b[27] | (b[28] << 8) | (b[29] << 16)) }
    }
  }
  return null
}

// ---------------------------------------------------------------------------
// GLTFLoader plugin (browser only).
// ---------------------------------------------------------------------------

/** Run async jobs with at most `limit` in flight. */
export function createLimiter(limit) {
  let active = 0
  const queue = []
  const next = () => {
    if (active >= limit || !queue.length) return
    active++
    const { job, resolve, reject } = queue.shift()
    Promise.resolve().then(job).then(resolve, reject).finally(() => { active--; next() })
  }
  return job => new Promise((resolve, reject) => { queue.push({ job, resolve, reject }); next() })
}

/**
 * GLTFLoader plugin: `loader.register(parser => new TextureBudgetPlugin(parser, opts))`.
 *
 * Takes over plain (non-extension) textures, decoding them through
 * createImageBitmap with a resize so no texture exceeds the cap computed for
 * the whole set, `concurrency` at a time. Reports progress via
 * `onProgress(done, total, cap)`.
 */
export class TextureBudgetPlugin {
  constructor(parser, { budgetBytes, maxTextureSize = 8192, concurrency = 2, onProgress } = {}) {
    this.parser = parser
    this.name = 'WEBODM_texture_budget'
    this.budgetBytes = budgetBytes ?? textureBudgetBytes(globalThis.navigator?.deviceMemory)
    this.maxTextureSize = maxTextureSize
    this.onProgress = onProgress
    this.run = createLimiter(concurrency)
    this.cap = null
    this.done = 0
    this.total = (parser.json?.images || []).length
    const plugin = this
    this.loader = {
      isImageBitmapLoader: true,
      load(url, onLoad, _onProgress, onError) {
        plugin.run(() => plugin.decode(url)).then(onLoad, onError)
      },
    }
  }

  loadTexture(textureIndex) {
    const json = this.parser.json
    const textureDef = json.textures?.[textureIndex]
    if (!textureDef || textureDef.extensions || textureDef.source === undefined) return null
    const sourceDef = json.images?.[textureDef.source]
    if (!sourceDef) return null
    if (sourceDef.uri && this.parser.options.manager.getHandler(sourceDef.uri) !== null) return null
    if (typeof createImageBitmap === 'undefined') return null
    return this.parser.loadTextureImage(textureIndex, textureDef.source, this.loader)
  }

  /** Cap for the whole texture set, computed once from the image headers. */
  resolveCap() {
    if (this.cap !== null) return this.cap
    const json = this.parser.json
    const body = this.parser.extensions?.KHR_binary_glTF?.body
    const dims = (json.images || []).map(img => {
      if (img.bufferView === undefined || !body) return null
      const bv = json.bufferViews?.[img.bufferView]
      if (!bv || bv.buffer !== 0) return null
      const offset = bv.byteOffset || 0
      return imageDimensions(new Uint8Array(body, offset, Math.min(bv.byteLength, 65536)))
    })
    this.cap = textureCapFor(dims, this.budgetBytes, this.maxTextureSize)
    return this.cap
  }

  async decode(url) {
    const cap = this.resolveCap()
    const response = await fetch(url)
    if (!response.ok) throw new Error(`texture fetch failed (${response.status})`)
    const blob = await response.blob()
    const head = new Uint8Array(await blob.slice(0, 65536).arrayBuffer())
    const dims = imageDimensions(head)
    const base = { premultiplyAlpha: 'none', colorSpaceConversion: 'none' }
    const target = resizeFor(dims, cap)
    let bitmap
    if (target) {
      bitmap = await createImageBitmap(blob, { ...base, resizeWidth: target.width, resizeHeight: target.height, resizeQuality: 'high' })
    } else {
      bitmap = await createImageBitmap(blob, base)
      // Unknown header format: check after decoding and shrink if needed.
      const late = resizeFor({ width: bitmap.width, height: bitmap.height }, cap)
      if (late) {
        const small = await createImageBitmap(bitmap, { ...base, resizeWidth: late.width, resizeHeight: late.height, resizeQuality: 'high' })
        bitmap.close()
        bitmap = small
      }
    }
    this.done++
    this.onProgress?.(this.done, this.total, cap)
    return bitmap
  }
}
