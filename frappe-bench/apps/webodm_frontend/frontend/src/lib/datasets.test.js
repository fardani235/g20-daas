import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  INPUT_MODES,
  createDataset,
  createTask,
  defaultDatasetTitle,
  deleteDataset,
  formatBytes,
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
  beforeEach(() => {
    window.csrf_token = 'tok'
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ message: { name: 'DS9' } }) }))
  })
  afterEach(() => {
    delete window.csrf_token
    vi.restoreAllMocks()
  })

  it('createDataset posts files, title and description with the CSRF token', async () => {
    const out = await createDataset({ files: [new File(['x'], 'a.jpg')], title: 'T', description: 'D' })
    expect(out).toEqual({ name: 'DS9' })
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/method/webodm_core.api.dataset.create_dataset')
    expect(init.method).toBe('POST')
    expect(init.headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(init.body).toBeInstanceOf(FormData)
    expect(init.body.getAll('files')).toHaveLength(1)
    expect(init.body.get('title')).toBe('T')
    expect(init.body.get('description')).toBe('D')
  })

  it('createTask sends either the dataset or the files, never a JSON body', async () => {
    await createTask({ projectId: 'P1', dataset: 'DS1', options: [{ name: 'dsm', value: true }] })
    let body = fetch.mock.calls[0][1].body
    expect(body.get('project_id')).toBe('P1')
    expect(body.get('dataset')).toBe('DS1')
    expect(body.getAll('files')).toHaveLength(0)
    expect(JSON.parse(body.get('options'))).toEqual([{ name: 'dsm', value: true }])

    await createTask({ projectId: 'P1', files: [new File(['x'], 'a.jpg')], datasetTitle: 'Fresh' })
    body = fetch.mock.calls[1][1].body
    expect(body.get('dataset')).toBeNull()
    expect(body.getAll('files')).toHaveLength(1)
    expect(body.get('dataset_title')).toBe('Fresh')
  })

  it('surfaces Frappe server messages on failure', async () => {
    fetch.mockImplementationOnce(async () => ({
      ok: false,
      json: async () => ({ _server_messages: JSON.stringify([JSON.stringify({ message: 'Dataset "X" is still used by 1 task(s): "t"' })]) }),
    }))
    await expect(deleteDataset('X')).rejects.toThrow(/still used by 1 task/)
  })
})
