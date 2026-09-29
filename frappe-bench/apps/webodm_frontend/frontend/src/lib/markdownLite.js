/**
 * A small, safe Markdown subset for product descriptions and docs.
 *
 * Supports: `#`–`###` headings, paragraphs, `-`/`*` bullet lists, `1.` numbered
 * lists, fenced code blocks, inline `code`, **bold**, *italic*, and
 * [links](https://…) with http(s)/mailto/relative targets. All input is
 * HTML-escaped first, so authored HTML never reaches the DOM; anything the
 * subset does not understand renders as text. No dependency, ~100 lines.
 */

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, ch => ESC[ch])
}

function safeHref(href) {
  const h = String(href || '').trim()
  if (/^(https?:\/\/|mailto:)/i.test(h)) return h
  if (h.startsWith('/') && !h.startsWith('//')) return h
  if (h.startsWith('#')) return h
  return null
}

function inline(text) {
  let out = escapeHtml(text)
  out = out.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`)
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, href) => {
    const safe = safeHref(href)
    if (!safe) return label
    const external = /^https?:\/\//i.test(safe)
    const attrs = external ? ' target="_blank" rel="noopener noreferrer"' : ''
    return `<a href="${escapeHtml(safe)}"${attrs}>${label}</a>`
  })
  return out
}

export function renderMarkdown(source) {
  const lines = String(source ?? '').replace(/\r\n?/g, '\n').split('\n')
  const html = []
  let para = []
  let list = null // { tag, items }
  let code = null // array of lines

  const flushPara = () => {
    if (para.length) {
      html.push(`<p>${inline(para.join(' '))}</p>`)
      para = []
    }
  }
  const flushList = () => {
    if (list) {
      html.push(`<${list.tag}>${list.items.map(i => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`)
      list = null
    }
  }
  const flushBlocks = () => { flushPara(); flushList() }

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '')

    if (code) {
      if (/^```/.test(line)) {
        html.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`)
        code = null
      } else {
        code.push(raw)
      }
      continue
    }
    if (/^```/.test(line)) {
      flushBlocks()
      code = []
      continue
    }

    if (!line.trim()) {
      flushBlocks()
      continue
    }

    const heading = line.match(/^(#{1,3})\s+(.*)$/)
    if (heading) {
      flushBlocks()
      const level = heading[1].length
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`)
      continue
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/)
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (bullet || numbered) {
      flushPara()
      const tag = bullet ? 'ul' : 'ol'
      if (!list || list.tag !== tag) {
        flushList()
        list = { tag, items: [] }
      }
      list.items.push((bullet || numbered)[1])
      continue
    }

    flushList()
    para.push(line.trim())
  }

  if (code) html.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`)
  flushBlocks()
  return html.join('\n')
}
