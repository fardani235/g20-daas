import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  INPUT_MODES,
  createDataset,
  createTask,
  defaultDatasetTitle,
  deleteDataset,
  formatBytes,
  gcpLabel,
  gcpWarnings,
  removeDatasetGcp,
  setDatasetGcp,
  imageCountLabel,
  isInUseError,
  sortDatasets,
  taskInputsValid,
  thumbnailUrl,
} from './datasets'

describe('formatBytes', () => {
  it('formats sizes with a sensible unit', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe('3.0 GB')
    expect(formatBytes(150 * 1024 * 1024)).toBe('150 MB')
  })

  it('degrades on garbage', () => {
    expect(formatBytes(null)).toBe('—')
    expect(formatBytes('nope')).toBe('—')
    expect(formatBytes(-1)).toBe('—')
  })
})

describe('imageCountLabel', () => {
  it('pluralises', () => {
    expect(imageCountLabel(0)).toBe('no images')
    expect(imageCountLabel(1)).toBe('1 image')
    expect(imageCountLabel(12)).toBe('12 images')
    expect(imageCountLabel(undefined)).toBe('no images')
  })
})

describe('thumbnailUrl', () => {
  const row = { name: 'IMG1', parent: 'DS1', image: '/private/files/a.jpg',
    thumbnail: '/api/method/webodm_core.api.dataset.thumbnail?dataset=DS1&image=IMG1&size=256' }

  it('prefers the server-provided thumbnail and rewrites the size', () => {
    expect(thumbnailUrl(row)).toBe(row.thumbnail)
    expect(thumbnailUrl(row, 512)).toContain('size=512')
    expect(thumbnailUrl(row, 512)).not.toContain('size=256')
  })

  it('builds the endpoint URL from parent + name when the payload lacks one', () => {
    const url = thumbnailUrl({ name: 'IMG 2', parent: 'DS1' }, 128)
    expect(url).toBe('/api/method/webodm_core.api.dataset.thumbnail?dataset=DS1&image=IMG%202&size=128')
  })

  it('falls back to the original image, then to an empty string', () => {
    expect(thumbnailUrl({ image: '/private/files/a.jpg' })).toBe('/private/files/a.jpg')
    expect(thumbnailUrl(null)).toBe('')
    expect(thumbnailUrl({})).toBe('')
  })
})

describe('isInUseError', () => {
  it('recognises the backend refusal', () => {
    expect(isInUseError(new Error('Dataset "X" is still used by 2 task(s): "a", "b". Delete those tasks first.'))).toBe(true)
    expect(isInUseError('still used by 1 task(s)')).toBe(true)
    expect(isInUseError(new Error('Permission denied'))).toBe(false)
    expect(isInUseError(null)).toBe(false)
  })
})

describe('taskInputsValid', () => {
  it('needs a dataset in existing mode and files in upload mode', () => {
    expect(taskInputsValid({ mode: INPUT_MODES.existing, dataset: 'DS1' })).toBe(true)
    expect(taskInputsValid({ mode: INPUT_MODES.existing, dataset: '' })).toBe(false)
    expect(taskInputsValid({ mode: INPUT_MODES.upload, files: [new File(['x'], 'a.jpg')] })).toBe(true)
    expect(taskInputsValid({ mode: INPUT_MODES.upload, files: [] })).toBe(false)
    expect(taskInputsValid({ mode: 'other', dataset: 'DS1', files: [1] })).toBe(false)
  })
})

describe('defaultDatasetTitle', () => {
  it('names the upload after the project, date and count', () => {
    const now = new Date('2026-09-30T12:00:00Z')
    expect(defaultDatasetTitle('North field', 42, now)).toBe('North field images 2026-09-30 (42 images)')
    expect(defaultDatasetTitle('', 1, now)).toBe('Images 2026-09-30 (1 image)')
  })
})

describe('sortDatasets', () => {
  it('orders newest first and does not mutate the input', () => {
    const rows = [
      { name: 'a', title: 'Old', creation: '2026-01-01 10:00:00' },
      { name: 'b', title: 'New', creation: '2026-09-30 10:00:00' },
      { name: 'c', title: 'Alpha' },
    ]
    const sorted = sortDatasets(rows)
    expect(sorted.map(r => r.name)).toEqual(['b', 'a', 'c'])
    expect(rows.map(r => r.name)).toEqual(['a', 'b', 'c'])
  })
})

describe('multipart requests', () => {
  // The two upload endpoints go through XMLHttpRequest (for progress), so a
  // fake XHR records what would have gone on the wire.
  let sent
  class FakeXHR {
    constructor() { this.headers = {}; this.listeners = {}; this.upload = { addEventListener: (n, fn) => { this.listeners[`upload:${n}`] = fn } }; this.status = 200; this.responseText = JSON.stringify({ message: { name: 'DS9' } }) }
    open(method, url) { this.method = method; this.url = url }
    setRequestHeader(k, v) { this.headers[k] = v }
    addEventListener(n, fn) { this.listeners[n] = fn }
    send(body) { this.body = body; sent.push(this); if (FakeXHR.autoLoad) queueMicrotask(() => this.listeners.load && this.listeners.load()) }
    abort() {}
  }
  beforeEach(() => {
    window.csrf_token = 'tok'
    sent = []
    FakeXHR.autoLoad = true
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ message: { name: 'DS9' } }) }))
  })
  afterEach(() => {
    delete window.csrf_token
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('createDataset posts files, title, description and gcp with the CSRF token', async () => {
    const gcp = new File(['EPSG:32633\n1 2 3 4 5 a.jpg\n'], 'gcp_list.txt', { type: 'text/plain' })
    const out = await createDataset({ files: [new File(['x'], 'a.jpg')], title: 'T', description: 'D', gcp })
    expect(out).toEqual({ name: 'DS9' })
    const xhr = sent[0]
    expect(xhr.url).toBe('/api/method/webodm_core.api.dataset.create_dataset')
    expect(xhr.method).toBe('POST')
    expect(xhr.headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(xhr.body).toBeInstanceOf(FormData)
    expect(xhr.body.getAll('files')).toHaveLength(1)
    expect(xhr.body.get('title')).toBe('T')
    expect(xhr.body.get('description')).toBe('D')
    expect(xhr.body.get('gcp').name).toBe('gcp_list.txt')
  })

  it('createTask sends either the dataset or the files, never a JSON body', async () => {
    const gcp = new File(['EPSG:32633\n1 2 3 4 5 a.jpg\n'], 'gcp_list.txt', { type: 'text/plain' })
    await createTask({ projectId: 'P1', dataset: 'DS1', options: [{ name: 'dsm', value: true }], gcp })
    let body = sent[0].body
    expect(body.get('project_id')).toBe('P1')
    expect(body.get('dataset')).toBe('DS1')
    expect(body.getAll('files')).toHaveLength(0)
    expect(body.get('gcp')).toBeNull() // ground control belongs to the dataset, never sent with an existing one
    expect(JSON.parse(body.get('options'))).toEqual([{ name: 'dsm', value: true }])

    await createTask({ projectId: 'P1', files: [new File(['x'], 'a.jpg')], datasetTitle: 'Fresh', gcp })
    body = sent[1].body
    expect(body.get('dataset')).toBeNull()
    expect(body.getAll('files')).toHaveLength(1)
    expect(body.get('dataset_title')).toBe('Fresh')
    expect(body.get('gcp').name).toBe('gcp_list.txt')
  })

  it('reports upload progress and can be cancelled', async () => {
    FakeXHR.autoLoad = false
    const progress = []
    const controller = new AbortController()
    const p = createDataset({ files: [new File(['x'], 'a.jpg')], title: 'T', onProgress: e => progress.push(e), signal: controller.signal })
    await Promise.resolve()
    const xhr = sent[0]
    xhr.listeners['upload:progress']({ loaded: 5, total: 10, lengthComputable: true })
    expect(progress).toEqual([{ loaded: 5, total: 10 }])
    controller.abort()
    await expect(p).rejects.toMatchObject({ cancelled: true })
  })

  it('setDatasetGcp / removeDatasetGcp hit the dataset endpoints', async () => {
    await setDatasetGcp('DS1', new File(['EPSG:1\n1 2 3 4 5 a.jpg'], 'g.txt'))
    expect(sent[0].url).toBe('/api/method/webodm_core.api.dataset.set_gcp')
    expect(sent[0].body.get('name')).toBe('DS1')
    expect(sent[0].body.get('gcp').name).toBe('g.txt')
    await removeDatasetGcp('DS1')
    expect(fetch.mock.calls[0][0]).toBe('/api/method/webodm_core.api.dataset.remove_gcp')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ name: 'DS1' })
  })

  it('surfaces Frappe server messages on failure', async () => {
    fetch.mockImplementationOnce(async () => ({
      ok: false,
      json: async () => ({ _server_messages: JSON.stringify([JSON.stringify({ message: 'Dataset "X" is still used by 1 task(s): "t"' })]) }),
    }))
    await expect(deleteDataset('X')).rejects.toThrow(/still used by 1 task/)
  })
})

describe('gcp helpers', () => {
  it('labels and warns', () => {
    expect(gcpLabel(null)).toBe('')
    expect(gcpLabel({ filename: 'gcp_list.txt', points: 1 })).toBe('gcp_list.txt · 1 point')
    expect(gcpLabel({ filename: 'g.txt', points: 7, crs: 'EPSG:32633' })).toBe('g.txt · 7 points · EPSG:32633')
    expect(gcpWarnings(null)).toEqual([])
    expect(gcpWarnings({ points: 7, unknown_images: [], few_points: false })).toEqual([])
    const w = gcpWarnings({ points: 2, unknown_images: ['a.jpg', 'b.jpg'], few_points: true })
    expect(w).toHaveLength(2)
    expect(w[0]).toMatch(/2 image names .* not in this dataset \(a.jpg, b.jpg\)/)
    expect(w[1]).toMatch(/Fewer than 3/)
  })
})
