// html2canvas 1.4 predates modern CSS colours. Let the browser resolve them
// on the cloned document, preserving the rendered colours and source DOM.
// Classic script so opaque-origin app-host frames can load it without CORS.
globalThis.atriumSnapshotColors = function normaliseSnapshotColors(doc) {
  const canvas = doc.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('could not initialise screenshot colour conversion')
  const properties = [
    'color', 'background-color', 'background-image',
    'border-top-color', 'border-right-color', 'border-bottom-color', 'border-left-color',
    'outline-color', 'column-rule-color', 'text-decoration-color', 'text-emphasis-color',
    'caret-color', 'fill', 'stroke', 'box-shadow', 'text-shadow',
  ]
  const cache = new Map()
  const toRGB = (color) => {
    if (cache.has(color)) return cache.get(color)
    context.clearRect(0, 0, 1, 1)
    context.fillStyle = color
    context.fillRect(0, 0, 1, 1)
    const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data
    const result = `rgba(${r}, ${g}, ${b}, ${a / 255})`
    cache.set(color, result)
    return result
  }
  const convert = (value) => {
    const pattern = /(?:color-mix|color|oklch|oklab|lab|lch|hwb)\(/gi
    let out = '', offset = 0, match
    while ((match = pattern.exec(value))) {
      let end = pattern.lastIndex, depth = 1
      for (; end < value.length && depth; end++) {
        if (value[end] === '(') depth++
        else if (value[end] === ')') depth--
      }
      if (depth) break
      out += value.slice(offset, match.index) + toRGB(value.slice(match.index, end))
      offset = end
      pattern.lastIndex = end
    }
    return out + value.slice(offset)
  }
  const pseudoRules = []
  let seq = 0
  for (const element of doc.querySelectorAll('*')) {
    // html2canvas adopts nodes cloned in the source realm into a new iframe.
    // Their prototypes may still belong to that original realm.
    if (!element.style) continue
    for (const pseudo of ['', '::before', '::after']) {
      const computed = doc.defaultView.getComputedStyle(element, pseudo || null)
      const changed = []
      for (const property of properties) {
        const value = computed.getPropertyValue(property)
        const converted = convert(value)
        if (converted !== value) changed.push([property, converted])
      }
      if (!changed.length) continue
      if (!pseudo) {
        for (const [property, value] of changed) element.style.setProperty(property, value, 'important')
      } else {
        const id = element.getAttribute('data-atrium-snapshot-color') || String(++seq)
        element.setAttribute('data-atrium-snapshot-color', id)
        pseudoRules.push(`[data-atrium-snapshot-color="${id}"]${pseudo}{${changed.map(([p, v]) => `${p}:${v}!important`).join(';')}}`)
      }
    }
  }
  if (pseudoRules.length) {
    const style = doc.createElement('style')
    style.textContent = pseudoRules.join('\n')
    doc.head.appendChild(style)
  }
}
