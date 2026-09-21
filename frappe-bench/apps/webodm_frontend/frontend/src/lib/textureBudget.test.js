import { describe, it, expect } from 'vitest'
import {
  textureBudgetBytes,
  decodedBytes,
  textureCapFor,
  resizeFor,
  imageDimensions,
  createLimiter,
} from './textureBudget'

const MB = 1024 * 1024

function pngHeader(width, height) {
  const b = new Uint8Array(24)
  b.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52])
  new DataView(b.buffer).setUint32(16, width)
  new DataView(b.buffer).setUint32(20, height)
  return b
}

function jpegHeader(width, height) {
  // SOI, APP0 (16 bytes), SOF0 with the frame size.
  const app0 = [0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 1, 1, 0, 0, 1, 0, 1, 0, 0]
  const sof = [0xff, 0xc0, 0x00, 0x11, 0x08, height >> 8, height & 0xff, width >> 8, width & 0xff, 3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1]
  return new Uint8Array([0xff, 0xd8, ...app0, ...sof])
}

describe('texture budget', () => {
  it('scales the budget with device memory and caps it on mobile', () => {
    expect(textureBudgetBytes(8)).toBe(1024 * MB)
    expect(textureBudgetBytes(4)).toBe(512 * MB)
    expect(textureBudgetBytes(2)).toBe(256 * MB)
    expect(textureBudgetBytes(undefined)).toBe(1024 * MB) // browsers that hide it
    expect(textureBudgetBytes(8, true)).toBe(384 * MB)
  })

  it('accounts for RGBA and mipmaps and plans unknown images at 4096', () => {
    expect(decodedBytes([{ width: 1024, height: 1024 }], 8192)).toBeCloseTo(1024 * 1024 * 4 * (4 / 3))
    expect(decodedBytes([null], 8192)).toBeCloseTo(4096 * 4096 * 4 * (4 / 3))
    expect(decodedBytes([{ width: 8192, height: 8192 }], 2048)).toBeCloseTo(2048 * 2048 * 4 * (4 / 3))
  })

  it('picks the largest cap where the whole set fits (real ODM survey)', () => {
    const dims = [
      ...Array(5).fill({ width: 8192, height: 8192 }),
      ...Array(10).fill({ width: 4096, height: 4096 }),
      ...Array(3).fill({ width: 2048, height: 2048 }),
      ...Array(6).fill({ width: 1024, height: 1024 }),
      ...Array(7).fill({ width: 512, height: 512 }),
      ...Array(3).fill({ width: 256, height: 256 }),
    ]
    expect(textureCapFor(dims, 1024 * MB)).toBe(2048)
    expect(textureCapFor(dims, 256 * MB)).toBe(1024)
    expect(textureCapFor(dims, 8 * 1024 * MB)).toBe(8192)
  })

  it('never exceeds the GPU maximum or drops below the minimum', () => {
    expect(textureCapFor([{ width: 8192, height: 8192 }], 8 * 1024 * MB, 4096)).toBe(4096)
    expect(textureCapFor(Array(500).fill({ width: 8192, height: 8192 }), 1 * MB)).toBe(256)
  })

  it('computes aspect-preserving resize targets only when needed', () => {
    expect(resizeFor({ width: 8192, height: 4096 }, 2048)).toEqual({ width: 2048, height: 1024 })
    expect(resizeFor({ width: 1024, height: 1024 }, 2048)).toBeNull()
    expect(resizeFor(null, 2048)).toBeNull()
  })
})

describe('imageDimensions', () => {
  it('reads PNG and JPEG headers', () => {
    expect(imageDimensions(pngHeader(4096, 2048))).toEqual({ width: 4096, height: 2048 })
    expect(imageDimensions(jpegHeader(8192, 8192))).toEqual({ width: 8192, height: 8192 })
  })

  it('returns null for unknown or truncated data', () => {
    expect(imageDimensions(new Uint8Array([1, 2, 3]))).toBeNull()
    expect(imageDimensions(new Uint8Array(32))).toBeNull()
    expect(imageDimensions(jpegHeader(10, 10).slice(0, 12))).toBeNull()
  })
})

describe('createLimiter', () => {
  it('runs at most `limit` jobs concurrently and preserves results', async () => {
    const run = createLimiter(2)
    let active = 0, peak = 0
    const job = v => async () => {
      active++; peak = Math.max(peak, active)
      await new Promise(r => setTimeout(r, 5))
      active--
      return v
    }
    const results = await Promise.all([1, 2, 3, 4, 5].map(v => run(job(v))))
    expect(results).toEqual([1, 2, 3, 4, 5])
    expect(peak).toBe(2)
  })

  it('propagates failures without stalling the queue', async () => {
    const run = createLimiter(1)
    await expect(run(() => Promise.reject(new Error('nope')))).rejects.toThrow('nope')
    expect(await run(() => 'ok')).toBe('ok')
  })
})
