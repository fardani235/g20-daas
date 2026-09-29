import { describe, it, expect } from 'vitest'
import { escapeHtml, renderMarkdown } from './markdownLite.js'

describe('renderMarkdown', () => {
  it('escapes authored HTML so nothing scriptable reaches the DOM', () => {
    const html = renderMarkdown('Hello <script>alert(1)</script> & <img src=x onerror=alert(1)>')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;script&gt;')
    expect(html).toContain('&amp;')
    expect(escapeHtml(`"'`)).toBe('&quot;&#39;')
  })

  it('renders headings, paragraphs and inline styles', () => {
    const html = renderMarkdown('# Title\n\nSome **bold** and *italic* with `code`.\n\n### Sub')
    expect(html).toContain('<h1>Title</h1>')
    expect(html).toContain('<p>Some <strong>bold</strong> and <em>italic</em> with <code>code</code>.</p>')
    expect(html).toContain('<h3>Sub</h3>')
  })

  it('joins consecutive lines into one paragraph', () => {
    expect(renderMarkdown('line one\nline two')).toBe('<p>line one line two</p>')
  })

  it('renders bullet and numbered lists', () => {
    expect(renderMarkdown('- a\n- b')).toBe('<ul><li>a</li><li>b</li></ul>')
    expect(renderMarkdown('1. a\n2) b')).toBe('<ol><li>a</li><li>b</li></ol>')
    expect(renderMarkdown('- a\n\n1. b')).toBe('<ul><li>a</li></ul>\n<ol><li>b</li></ol>')
  })

  it('renders fenced code verbatim and escaped, even when unterminated', () => {
    expect(renderMarkdown('```\n<b>x</b>\n  indented\n```')).toBe('<pre><code>&lt;b&gt;x&lt;/b&gt;\n  indented</code></pre>')
    expect(renderMarkdown('```py\nprint(1)')).toBe('<pre><code>print(1)</code></pre>')
  })

  it('allows http(s), mailto and relative links only', () => {
    expect(renderMarkdown('[docs](https://example.com/d)'))
      .toBe('<p><a href="https://example.com/d" target="_blank" rel="noopener noreferrer">docs</a></p>')
    expect(renderMarkdown('[mail](mailto:a@b.c)')).toContain('href="mailto:a@b.c"')
    expect(renderMarkdown('[here](/marketplace)')).toBe('<p><a href="/marketplace">here</a></p>')
    expect(renderMarkdown('[x](javascript:alert)')).toBe('<p>x</p>')
    expect(renderMarkdown('[x](JAVASCRIPT:alert)')).not.toContain('href')
    expect(renderMarkdown('[x](//evil.example)')).toBe('<p>x</p>')
    expect(renderMarkdown('[x](https://e.com/"onmouseover="alert(1))')).not.toContain('onmouseover="')
  })

  it('handles empty and nullish input', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null)).toBe('')
    expect(renderMarkdown('\r\n\r\n')).toBe('')
  })
})
