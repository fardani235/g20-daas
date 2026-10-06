import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  UPLOAD_PHASES,
  UploadCancelled,
  createUploadTracker,
  extensionOf,
  filesFromDataTransfer,
  formatEta,
  formatRate,
  isCancelled,
  isGcpFile,
  isImageFile,
  mergeFiles,
  partitionDrop,
  progressLabel,
  progressSnapshot,
  totalBytes,
  uploadForm,
} from './upload'

const f = (name, size = 1, type = '') => {
  const file = new File([new Uint8Array(size)], name, { type })
  return file
}

describe('file classification', () => {
  it('recognises images by MIME type or extension', () => {
    expect(isImageFile(f('a.jpg'))).toBe(true)
    expect(isImageFile(f('A.JPG'))).toBe(true)
    expect(isImageFile(f('scan.TIF'))).toBe(true)
    expect(isImageFile(f('raw.dng'))).toBe(true)
    expect(isImageFile(f('blob', 1, 'image/png'))).toBe(true)
    expect(isImageFile(f('gcp_list.txt'))).toBe(false)
    expect(isImageFile(f('notes.pdf'))).toBe(false)
    expect(isImageFile(null)).toBe(false)
    expect(extensionOf('x.tar.gz')).toBe('gz')
    expect(extensionOf('noext')).toBe('')
  })

  it('treats a .txt as a ground control file', () => {
    expect(isGcpFile(f('gcp_list.txt'))).toBe(true)
    expect(isGcpFile(f('GCP.TXT'))).toBe(true)
    expect(isGcpFile(f('a.jpg'))).toBe(false)
  })

  it('partitions a mixed drop into images, one gcp and the rest', () => {
    const { images, gcp, ignored } = partitionDrop([
      f('IMG_1.JPG'), f('gcp_list.txt'), f('IMG_2.JPG'), f('readme.md'), f('other.txt'),
    ])
    expect(images.map(x => x.name)).toEqual(['IMG_1.JPG', 'IMG_2.JPG'])
    expect(gcp.name).toBe('gcp_list.txt')
    expect(ignored.map(x => x.name)).toEqual(['readme.md', 'other.txt'])
  })

  it('merges rounds of files without duplicates and sums sizes', () => {
    const a = f('a.jpg', 10)
    const merged = mergeFiles([a], [f('a.jpg', 10), f('b.jpg', 5)])
    expect(merged.map(x => x.name)).toEqual(['a.jpg', 'b.jpg'])
    expect(totalBytes(merged)).toBe(15)
    expect(totalBytes([])).toBe(0)
  })
})

describe('filesFromDataTransfer', () => {
  it('falls back to dt.files when the browser offers no directory entries', async () => {
    const files = [f('a.jpg')]
    expect(await filesFromDataTransfer({ files, items: [] })).toEqual(files)
    expect(await filesFromDataTransfer(null)).toEqual([])
  })

  it('walks a dropped folder through webkitGetAsEntry', async () => {
    const fileEntry = (name) => ({ isFile: true, isDirectory: false, name, file: cb => cb(f(name)) })
    let reads = 0
    const dir = {
      isFile: false, isDirectory: true, name: 'flight',
      createReader: () => ({
        readEntries: cb => {
          reads++
          cb(reads === 1 ? [fileEntry('IMG_1.JPG'), fileEntry('.DS_Store'), fileEntry('gcp_list.txt')] : [])
        },
      }),
    }
    const dt = { files: [], items: [{ webkitGetAsEntry: () => dir }] }
    const out = await filesFromDataTransfer(dt)
    expect(out.map(x => x.name)).toEqual(['IMG_1.JPG', 'gcp_list.txt'])
  })
})

describe('progress maths', () => {
  it('computes percent, rate and eta from bytes and time', () => {
    const s = progressSnapshot({ loaded: 50, total: 200, startedAt: 0, now: 2000 })
    expect(s.percent).toBe(25)
    expect(s.rate).toBe(25)
    expect(s.eta).toBe(6)
    expect(s.complete).toBe(false)
    expect(progressSnapshot({ loaded: 200, total: 200, startedAt: 0, now: 2000 }).complete).toBe(true)
    expect(progressSnapshot({ loaded: 300, total: 200, startedAt: 0, now: 2000 }).percent).toBe(100)
  })

  it('holds back rate and eta until there is something to say', () => {
    expect(progressSnapshot({ loaded: 10, total: 100, startedAt: 0, now: 100 }).rate).toBe(0)
    expect(progressSnapshot({ loaded: 10, total: 100, startedAt: 0, now: 800 }).eta).toBeNull()
    expect(progressSnapshot({ loaded: 0, total: 0 }).percent).toBe(0)
    expect(progressSnapshot().eta).toBeNull()
  })

  it('formats rate and eta', () => {
    expect(formatRate(0)).toBe('')
    expect(formatRate(512)).toBe('512 B/s')
    expect(formatRate(20 * 1024)).toBe('20 KB/s')
    expect(formatRate(2.5 * 1024 * 1024)).toBe('2.5 MB/s')
    expect(formatEta(null)).toBe('')
    expect(formatEta(42)).toBe('42s left')
    expect(formatEta(125)).toBe('2m 5s left')
    expect(formatEta(3700)).toBe('1h 1m left')
  })

  it('labels every phase honestly', () => {
    const snap = progressSnapshot({ loaded: 50, total: 200, startedAt: 0, now: 2000 })
    expect(progressLabel(UPLOAD_PHASES.uploading, snap, { formatBytes: b => `${b}B` })).toBe('50B of 200B · 25 B/s · 6s left')
    expect(progressLabel(UPLOAD_PHASES.processing)).toMatch(/server is registering/)
    expect(progressLabel(UPLOAD_PHASES.done)).toBe('Done')
    expect(progressLabel(UPLOAD_PHASES.failed)).toBe('Upload failed')
    expect(progressLabel(UPLOAD_PHASES.cancelled)).toBe('Upload cancelled')
    expect(progressLabel(UPLOAD_PHASES.idle)).toBe('')
  })
})

describe('upload tracker', () => {
  it('moves uploading -> processing when the last byte is out, then done', () => {
    let now = 1000
    const t = createUploadTracker(() => now)
    expect(t.state.phase).toBe('idle')
    const signal = t.start(100)
    expect(signal).toBeInstanceOf(AbortSignal)
    expect(t.state.phase).toBe('uploading')
    now = 3000
    t.update({ loaded: 50, total: 100 })
    expect(t.state.snapshot.percent).toBe(50)
    expect(t.state.phase).toBe('uploading')
    t.update({ loaded: 100, total: 100 })
    expect(t.state.phase).toBe('processing')
    t.finish()
    expect(t.state.phase).toBe('done')
    t.reset()
    expect(t.state.phase).toBe('idle')
    expect(t.state.loaded).toBe(0)
  })

  it('cancel aborts the signal and fail marks the phase', () => {
    const t = createUploadTracker()
    const signal = t.start(10)
    t.cancel()
    expect(signal.aborted).toBe(true)
    expect(t.state.phase).toBe('cancelled')
    t.start(10)
    t.fail()
    expect(t.state.phase).toBe('failed')
    t.update({ loaded: 5, total: 10 }) // ignored once terminal
    expect(t.state.loaded).toBe(0)
  })
})

describe('uploadForm', () => {
  class FakeXHR {
    constructor() {
      this.headers = {}
      this.listeners = {}
      this.upload = { addEventListener: (n, fn) => { this.listeners[`upload:${n}`] = fn } }
      this.status = 200
      this.responseText = JSON.stringify({ message: { ok: true } })
      FakeXHR.instances.push(this)
    }
    open(method, url) { this.method = method; this.url = url }
    setRequestHeader(k, v) { this.headers[k] = v }
    addEventListener(n, fn) { this.listeners[n] = fn }
    send(body) { this.body = body }
    abort() { this.aborted = true; this.listeners.abort && this.listeners.abort() }
    fire(name, ev) { this.listeners[name](ev) }
  }
  FakeXHR.instances = []

  beforeEach(() => { FakeXHR.instances = []; window.csrf_token = 'tok' })
  afterEach(() => { delete window.csrf_token })

  const send = (opts = {}) => {
    const form = new FormData()
    form.append('x', '1')
    const p = uploadForm('/api/method/m', form, { xhrFactory: () => new FakeXHR(), ...opts })
    return { p, xhr: FakeXHR.instances.at(-1) }
  }

  it('posts the form with the CSRF header and resolves the message', async () => {
    const { p, xhr } = send()
    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/api/method/m')
    expect(xhr.headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(xhr.body.get('x')).toBe('1')
    xhr.fire('load')
    await expect(p).resolves.toEqual({ ok: true })
  })

  it('forwards upload progress events', async () => {
    const seen = []
    const { p, xhr } = send({ onProgress: e => seen.push(e) })
    xhr.fire('upload:progress', { loaded: 3, total: 9, lengthComputable: true })
    xhr.fire('upload:progress', { loaded: 4, total: 9, lengthComputable: false })
    xhr.fire('load')
    await p
    expect(seen).toEqual([{ loaded: 3, total: 9 }, { loaded: 4, total: 0 }])
  })

  it('rejects with the Frappe message on HTTP errors and a plain error on network failure', async () => {
    let { p, xhr } = send()
    xhr.status = 417
    xhr.responseText = JSON.stringify({ _server_messages: JSON.stringify([JSON.stringify({ message: 'Not a valid ground control file: line 2: x' })]) })
    xhr.fire('load')
    await expect(p).rejects.toThrow(/Not a valid ground control file: line 2/)
    ;({ p, xhr } = send())
    xhr.status = 500
    xhr.responseText = 'not json'
    xhr.fire('load')
    await expect(p).rejects.toThrow(/HTTP 500/)
    ;({ p, xhr } = send())
    xhr.fire('error')
    await expect(p).rejects.toThrow(/network error/)
  })

  it('aborts through the signal and rejects with a cancellation', async () => {
    const controller = new AbortController()
    const { p, xhr } = send({ signal: controller.signal })
    controller.abort()
    expect(xhr.aborted).toBe(true)
    const err = await p.catch(e => e)
    expect(err).toBeInstanceOf(UploadCancelled)
    expect(isCancelled(err)).toBe(true)
    expect(isCancelled(new Error('x'))).toBe(false)
    // an already-aborted signal never sends
    const pre = new AbortController(); pre.abort()
    const before = FakeXHR.instances.length
    await expect(uploadForm('/u', new FormData(), { signal: pre.signal, xhrFactory: () => new FakeXHR() })).rejects.toBeInstanceOf(UploadCancelled)
    expect(FakeXHR.instances.length).toBe(before + 1)
    expect(FakeXHR.instances.at(-1).body).toBeUndefined()
  })

  it('uses the global XMLHttpRequest when no factory is given', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    const p = uploadForm('/u', new FormData())
    FakeXHR.instances.at(-1).fire('load')
    await expect(p).resolves.toEqual({ ok: true })
    vi.unstubAllGlobals()
  })
})
