import { marked } from 'marked'
import DOMPurify from 'dompurify'

/** Render untrusted file contents; metadata remains literal text, never HTML. */
export function renderMarkdown(text, target, baseUrl) {
  const document = target.ownerDocument
  const fragment = document.createDocumentFragment()
  const frontmatter = text.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)\s*(?:\r?\n|$)/)
  if (frontmatter) {
    const details = document.createElement('details')
    details.className = 'tv-metadata'
    const summary = document.createElement('summary')
    summary.textContent = 'Document metadata'
    const code = document.createElement('pre')
    code.textContent = frontmatter[1]
    details.append(summary, code)
    fragment.append(details)
    text = text.slice(frontmatter[0].length)
  }
  const article = document.createElement('article')
  article.className = 'tv-prose'
  article.innerHTML = DOMPurify.sanitize(marked.parse(text, { gfm: true }), {
    FORBID_TAGS: ['style', 'iframe', 'object', 'embed', 'form'],
    FORBID_ATTR: ['style', 'data-action'],
  })
  const ids = new Set()
  article.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(heading => {
    const slug = heading.textContent.toLowerCase().trim().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s+/g, '-') || 'section'
    let id = slug, suffix = 1
    while (ids.has(id)) id = `${slug}-${suffix++}`
    ids.add(id); heading.id = id
  })
  article.querySelectorAll('a[href],img[src]').forEach(el => {
    const attribute = el.tagName === 'A' ? 'href' : 'src'
    const value = el.getAttribute(attribute)
    if (value.startsWith('#')) return
    try {
      const url = new URL(value, baseUrl)
      const allowed = ['https:', 'http:'].includes(url.protocol) ||
        (attribute === 'href' && ['mailto:', 'tel:'].includes(url.protocol)) ||
        (attribute === 'src' && /^data:image\//i.test(value))
      if (!allowed) { el.removeAttribute(attribute); return }
      el.setAttribute(attribute, url.href)
      if (attribute === 'href') { el.target = '_blank'; el.rel = 'noopener noreferrer' }
      else { el.loading = 'lazy'; el.referrerPolicy = 'no-referrer' }
    } catch { el.removeAttribute(attribute) }
  })
  article.querySelectorAll('input').forEach(input => { input.disabled = true })
  if (!article.textContent.trim() && !article.querySelector('img')) {
    const empty = document.createElement('p')
    empty.className = 'tv-empty-preview'
    empty.textContent = 'This document is empty. Switch to Edit to start writing.'
    article.append(empty)
  }
  fragment.append(article)
  target.replaceChildren(fragment)
}
