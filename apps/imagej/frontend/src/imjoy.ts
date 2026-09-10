/**
 * Talking to ImageJ.JS over imjoy-rpc.
 *
 * ImageJ.JS is an ImJoy plugin, and an ImJoy plugin only exposes its API to an
 * ImJoy core. `?open=<url>` needs no core, but it is one-way: the URL goes in
 * and nothing — not whether the image loaded, not the pixels, not an error —
 * comes back.
 *
 * So this loads a core. Deliberately `loadImJoyCore` and not
 * `loadImJoyBasicApp`: the basic app brings ImJoy's own window manager and
 * menu bar, and this desktop already owns window placement. The core lets the
 * plugin mount into a container of our choosing through the `add_window`
 * event.
 *
 * The loader is fetched from lib.imjoy.io on first use rather than at boot,
 * since it is a sizeable script that most sessions never need.
 */

const LOADER_URL = 'https://lib.imjoy.io/imjoy-loader.js'
const IMAGEJ_URL = 'https://ij.imjoy.io'

/** The subset of ImageJ.JS's API this app uses. See its README for the rest. */
export interface ImageJApi {
  runMacro(macro: string, args?: string): Promise<unknown>
  getDimensions(): Promise<number[]>
  getImage(format?: string): Promise<unknown>
  viewImage(image: unknown, config?: Record<string, unknown>): Promise<unknown>
  selectWindow(title: string): Promise<unknown>
  close?(): Promise<void>
}

export interface ImageJState {
  image_count: number
  current_image: { id: number; title: string; dimensions: number[] } | null
}

/** Prefix only main-program returns, retaining function-local return values.
 * A nested eval is not safe here: this ImageJA.JS fork starts it on another
 * Java thread and can resolve the outer RPC before the evaluated macro ends. */
function markMacroReturns(source: string, prefix: string): string {
  const variable = `atriumResult${prefix.replaceAll('_', '')}`
  // Tokens retain source offsets; strings/comments cannot introduce syntax.
  const tokens = [...source.matchAll(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\/[^\n]*|\/\*[\s\S]*?\*\/|[A-Za-z_$][\w$]*|[^\s]/g)]
    .filter((token) => !token[0].startsWith('//') && !token[0].startsWith('/*'))
  const edits: { start: number; end: number; value: string }[] = []
  const functions: boolean[] = []
  let inFunction = 0, functionHeader = false
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]!
    if (token[0] === 'function') functionHeader = true
    if (token[0] === '{') {
      functions.push(functionHeader)
      if (functionHeader) inFunction++
      functionHeader = false
    } else if (token[0] === '}') {
      if (functions.pop()) inFunction--
    } else if (token[0] === 'return' && !inFunction) {
      const start = token.index! + token[0].length
      let j = i + 1, depth = 0
      for (; j < tokens.length; j++) {
        const value = tokens[j]![0]
        if (depth === 0 && (value === ';' || value === '}')) break
        if (value === '(' || value === '[') depth++
        if (value === ')' || value === ']') depth--
      }
      const end = tokens[j]?.index ?? source.length
      const expression = source.slice(start, end).trim()
      // ImageJ treats parentheses as a numeric expression: prefix + (a
      // string expression) silently becomes "NaN". Assign first instead.
      edits.push({ start: token.index!, end: end + (tokens[j]?.[0] === ';' ? 1 : 0),
        value: expression ? `{ ${variable} = ${expression}; return ${JSON.stringify(prefix)} + ${variable}; }`
          : `{ return ${JSON.stringify(prefix)}; }` })
      i = j - 1
    }
  }
  for (const edit of edits.reverse()) source = source.slice(0, edit.start) + edit.value + source.slice(edit.end)
  return `var ${variable};\n` + source + `\nreturn ${JSON.stringify(prefix)};`
}

/** ImageJA.JS rejects runMacro's promise for *any* non-null return value,
 * including success. Only our per-call marker means confirmed completion;
 * unmarked Java/macro failures are never converted into successful results. */
export async function runImageJMacro(api: ImageJApi, macro: string, args = ''): Promise<string> {
  const prefix = `__ATRIUM_RESULT_${crypto.randomUUID().replaceAll('-', '')}__`
  const marked = markMacroReturns(macro, prefix)
  const decode = (value: unknown): string | undefined => {
    const message = typeof value === 'string' ? value
      : value instanceof Error ? value.message : undefined
    return message?.startsWith(prefix) ? message.slice(prefix.length) : undefined
  }
  try {
    const value = await api.runMacro(marked, args)
    const result = decode(value)
    if (result !== undefined) return result
    throw new Error('ImageJ did not confirm the macro result')
  } catch (error) {
    const result = decode(error)
    if (result !== undefined) return result
    throw error
  }
}

/** Read without calling getImage when no image exists (that opens a dialog). */
export async function readImageJState(api: ImageJApi): Promise<ImageJState> {
  const value = await runImageJMacro(api,
    'if (nImages == 0) return "0"; getDimensions(w, h, c, z, t); return "" + nImages + "\\n" + getImageID() + "\\n" + w + "\\n" + h + "\\n" + c + "\\n" + z + "\\n" + t + "\\n" + getTitle();',
  )
  const [count, id, ...rest] = value.split('\n')
  const image_count = Number(count)
  if (!Number.isInteger(image_count) || image_count < 0) throw new Error(`ImageJ returned invalid image state: ${JSON.stringify(value.slice(0, 160))}`)
  if (!image_count) return { image_count, current_image: null }
  const imageId = Number(id)
  // Identity and dimensions come from one macro, so a user switching the
  // active image cannot mix one image's id with another image's dimensions.
  const dimensions = rest.slice(0, 5).map(Number)
  if (!Number.isInteger(imageId) || dimensions.length !== 5 || !dimensions.every((n) => Number.isInteger(n) && n > 0)) {
    throw new Error('ImageJ could not read the current image')
  }
  return { image_count, current_image: { id: imageId, title: rest.slice(5).join('\n'), dimensions } }
}

/** Export image pixels through ImageJ's own PNG encoder, not the cross-origin iframe. */
export async function snapshotImageJ(api: ImageJApi): Promise<string> {
  const state = await readImageJState(api)
  if (!state.current_image) throw new Error('ImageJ has no open image to capture')
  const result = await api.getImage('png')
  const bytes = result instanceof ArrayBuffer ? new Uint8Array(result)
    : ArrayBuffer.isView(result) ? new Uint8Array(result.buffer, result.byteOffset, result.byteLength)
      : Array.isArray(result) ? Uint8Array.from(result) : null
  if (!bytes?.length) throw new Error('ImageJ did not return PNG bytes')
  if (bytes[0] !== 137 || bytes[1] !== 80 || bytes[2] !== 78 || bytes[3] !== 71) {
    throw new Error('ImageJ returned an invalid PNG')
  }
  let binary = ''
  for (let start = 0; start < bytes.length; start += 8192) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 8192))
  }
  return `data:image/png;base64,${btoa(binary)}`
}

interface ImJoyHost {
  start(config: { workspace: string }): Promise<void>
  event_bus: { on(event: string, handler: (window: { window_id: string }) => void): void }
  api: { createWindow(config: Record<string, unknown>): Promise<ImageJApi> }
  destroy?(): void
}
interface ImJoyCore {
  ImJoy: new (config: { imjoy_api: Record<string, unknown> }) => ImJoyHost
}

declare global {
  interface Window {
    loadImJoyCore?: (config?: { version?: string }) => Promise<ImJoyCore>
  }
}

let loaderPromise: Promise<void> | null = null

function loadScript(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${url}"]`)
    if (existing) {
      existing.addEventListener('load', () => resolve())
      existing.addEventListener('error', () => reject(new Error(`could not load ${url}`)))
      if (window.loadImJoyCore) resolve()
      return
    }
    const script = document.createElement('script')
    script.src = url
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error(`could not load ${url}`))
    document.head.appendChild(script)
  })
}

async function ensureLoader(): Promise<void> {
  if (!loaderPromise) loaderPromise = loadScript(LOADER_URL)
  await loaderPromise
  if (!window.loadImJoyCore) throw new Error('the ImJoy loader did not register')
}

/**
 * Start ImageJ.JS inside `container` and return its API.
 *
 * One core per window rather than one shared: ImJoy cores hold plugin state,
 * and two ImageJ windows sharing one would fight over which is "current".
 */
export async function createImageJ(container: HTMLElement): Promise<{
  api: ImageJApi
  dispose: () => void
}> {
  await ensureLoader()

  // No version pin. The integration docs show one, and the version they show
  // (0.13.16) was never published — the loader's own default is `latest`,
  // which is both current and actually resolvable.
  const imjoyCore = await window.loadImJoyCore!()
  const imjoy = new imjoyCore.ImJoy({ imjoy_api: {} })
  await imjoy.start({ workspace: 'default' })

  // The core announces a window before the plugin mounts into it, and expects
  // an element whose id is the window id to already exist.
  imjoy.event_bus.on('add_window', (w: { window_id: string }) => {
    if (document.getElementById(w.window_id)) return
    const mount = document.createElement('div')
    mount.id = w.window_id
    mount.style.width = '100%'
    mount.style.height = '100%'
    container.appendChild(mount)
  })

  const api = (await imjoy.api.createWindow({
    src: IMAGEJ_URL,
    name: 'ImageJ',
    type: 'window',
    // Fill the container rather than ImJoy's default window box.
    fullscreen: false,
    w: 40,
    h: 30,
  })) as ImageJApi

  return {
    api,
    dispose: () => {
      try {
        void api.close?.()
      } catch {
        /* the plugin may already be gone */
      }
      try {
        imjoy.destroy?.()
      } catch {
        /* best effort */
      }
      container.replaceChildren()
    },
  }
}

/**
 * Open a URL in ImageJ and confirm something arrived.
 *
 * The bytes are fetched here and handed over as an **ArrayBuffer**, which is
 * not incidental. ImageJ.JS's `showImage` branches on the argument's type:
 * an ArrayBuffer is written into CheerpJ's virtual filesystem as
 * `/str/<name>` and opened from there, while anything else is treated as an
 * ndarray and pushed through ImageJ's "Raw…" importer. A Uint8Array crosses
 * imjoy-rpc as an ndarray, so it takes the second branch and is read as a
 * one-dimensional raster — which is how a perfectly good TIFF becomes "There
 * are no images open".
 *
 * That virtual-filesystem route is also why this works at all: ImageJ opens a
 * local path and never touches the network. Asking Java to fetch the URL —
 * `open(url)`, which is what `?open=` does — cannot reach a cross-origin
 * address under CheerpJ.
 *
 * `name` matters too: ImageJ picks its reader from the extension, so it has
 * to be the real filename.
 *
 * Then it waits. `viewImage` calls `showImage` **without awaiting it**, so it
 * resolves before the image has loaded — asking for dimensions straight after
 * finds nothing and makes ImageJ pop its "There are no images open" dialog.
 * So we poll the macro variable `nImages`, which reports the count without
 * showing a dialog for zero, and only ask for dimensions once one exists.
 */
export async function openUrl(
  api: ImageJApi,
  url: string,
  name: string,
  onProgress?: (stage: 'fetching' | 'decoding', bytes?: number) => void,
): Promise<number[]> {
  onProgress?.('fetching')
  const res = await fetch(url)
  if (!res.ok) throw new Error(`could not fetch the file (${res.status})`)
  const buffer = await res.arrayBuffer()

  // ImageJ1 runs on a 32-bit JVM under CheerpJ, so decoding is where the time
  // goes on anything sizeable — worth distinguishing from the download.
  onProgress?.('decoding', buffer.byteLength)

  const before = await imageCount(api)
  await api.viewImage(buffer, { name })
  await waitForImage(api, before)

  const dims = (await api.getDimensions()) as number[]
  if (!Array.isArray(dims) || !dims[0]) {
    throw new Error(`ImageJ could not read ${name}`)
  }
  return dims
}

/** How many images ImageJ has open. Never pops a dialog, unlike getImage. */
async function imageCount(api: ImageJApi): Promise<number> {
  const n = Number(await runImageJMacro(api, 'return "" + nImages;'))
  if (!Number.isInteger(n) || n < 0) throw new Error('ImageJ returned an invalid image count')
  return n
}

/** A big TIFF takes a while to decode; a missing reader never finishes at all. */
const OPEN_TIMEOUT_MS = 120_000
const POLL_MS = 300

async function waitForImage(api: ImageJApi, before: number): Promise<void> {
  const deadline = Date.now() + OPEN_TIMEOUT_MS
  while (Date.now() < deadline) {
    if ((await imageCount(api)) > before) return
    await new Promise((r) => setTimeout(r, POLL_MS))
  }
  throw new Error('ImageJ did not open the file — it may be a format it cannot read')
}
