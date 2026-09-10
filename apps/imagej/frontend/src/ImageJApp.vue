<script setup lang="ts">
/** Independent ImageJ.js App; all host access uses the scoped App SDK. */
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import FilePicker from './FilePicker.vue'
import type { AppHost } from './host'
const baseName = (path: string) => path.split('/').pop() || path
import { createImageJ, openUrl, readImageJState, runImageJMacro, snapshotImageJ, type ImageJApi, type ImageJState } from './imjoy'

/** What ImageJ1 reads; mirrors the registry's list for this app. */
const OPENABLE = [
  '.tif', '.tiff', '.ome.tif', '.ome.tiff',
  '.png', '.jpg', '.jpeg', '.gif', '.bmp',
  '.dcm', '.dicom', '.fits', '.pgm', '.avi', '.nrrd', '.zarr', '.ome.zarr',
]

const props = defineProps<{ host: AppHost; path?: string; started: () => void; failed: (error: Error) => void }>()
const container = ref<HTMLElement | null>(null)
const status = ref<'starting' | 'ready' | 'opening' | 'error'>('starting')
const degraded = ref(false)
const frameSrc = ref('')
const error = ref('')
const picking = ref(false)
const openPath = ref<string | undefined>(undefined)
const workspace = { ready: true }
const dimensions = ref<number[] | null>(null)
const stage = ref<'preparing' | 'fetching' | 'decoding' | null>(null)
/** Set when the pod had to rewrite the file for ImageJ, and why. */
const rewritten = ref<{ reason?: string; downsampled?: number } | null>(null)
const bytes = ref(0)
const elapsed = ref(0)
let clock: ReturnType<typeof setInterval> | null = null

let api: ImageJApi | null = null
let dispose: (() => void) | null = null
let operation = ''
let imageState: ImageJState = { image_count: 0, current_image: null }
const bootResolve = props.started
const bootReject = props.failed

function requireApi(): ImageJApi {
  if (degraded.value) throw new Error('ImageJ is in limited mode: its RPC control channel is unavailable')
  if (!api) throw new Error('ImageJ is still starting; its RPC control channel is not ready')
  return api
}

/** ImageJ.JS has one global macro callback. Concurrent RPC macros would
 * overwrite it and strand one caller, so every operation uses this gate. */
async function exclusive<T>(name: string, action: () => Promise<T>): Promise<T> {
  if (operation) throw new Error(`ImageJ is busy with ${operation}; wait for it before issuing another operation`)
  operation = name
  try { return await action() }
  finally { operation = '' }
}

async function readState(): Promise<Record<string, unknown>> {
  const summary = { app: 'imagej', path: current.value ?? null, status: status.value,
    connected: !!api && !degraded.value, degraded: degraded.value, error: error.value }
  if (!api || degraded.value || operation) {
    return { ...summary, ...imageState, busy: operation || null, state_fresh: false }
  }
  await exclusive('reading image state', async () => { imageState = await readImageJState(requireApi()) })
  dimensions.value = imageState.current_image?.dimensions ?? null
  return { ...summary, ...imageState, busy: null, state_fresh: true }
}

async function agentOpen(path: unknown) {
  requireApi()
  if (operation) throw new Error(`ImageJ is busy with ${operation}`)
  if (typeof path !== 'string' || !path.trim()) throw new Error('open needs a sandbox path')
  openPath.value = path
  await openCurrent(true)
  props.host.setState({ path })
  return await readState()
}

const bridge = {
  read: readState,
  update: async (patch: Record<string, unknown>) => {
    if (typeof patch.path !== 'string') throw new Error('ImageJ writable state requires path; use run_macro for image operations')
    await agentOpen(patch.path)
  },
  actions: {
    open: ({ path }: Record<string, unknown>) => agentOpen(path),
    status: readState,
    run_macro: async ({ macro, args, expected_image_id }: Record<string, unknown>) => {
      const target = requireApi()
      if (typeof macro !== 'string' || !macro.trim()) throw new Error('run_macro needs a macro string')
      if (macro.length > 100_000) throw new Error('macro is too large (maximum 100000 characters)')
      if (args !== undefined && typeof args !== 'string') throw new Error('macro args must be a string')
      if (expected_image_id !== undefined && !Number.isInteger(expected_image_id)) {
        throw new Error('expected_image_id must be the integer returned by desktop_read')
      }
      const guard = expected_image_id === undefined ? ''
        : `if (nImages == 0 || getImageID() != ${expected_image_id}) return "__ATRIUM_IMAGE_CHANGED__";\n`
      return await exclusive('running macro', async () => {
        const result = await runImageJMacro(target, guard + macro, args as string | undefined)
        if (result === '__ATRIUM_IMAGE_CHANGED__') throw new Error('the current ImageJ image changed; read this window again before running the macro')
        imageState = await readImageJState(target)
        dimensions.value = imageState.current_image?.dimensions ?? null
        return { result: result ?? null, ...imageState }
      })
    },
    select_image: async ({ title }: Record<string, unknown>) => {
      const target = requireApi()
      if (typeof title !== 'string' || !title) throw new Error('select_image needs an image title')
      return await exclusive('selecting image', async () => {
        const available = (await runImageJMacro(target, 'titles = getList("image.titles"); text = ""; for (i=0; i<titles.length; i++) { if (i>0) text=text+"\\n"; text=text+titles[i]; } return text;')).split('\n')
        if (!available.includes(title)) throw new Error(`no open ImageJ image named '${title}'`)
        await target.selectWindow(title)
        imageState = await readImageJState(target)
        if (imageState.current_image?.title !== title) {
          throw new Error('the requested ImageJ image is no longer active; read this window again')
        }
        dimensions.value = imageState.current_image?.dimensions ?? null
        return imageState
      })
    },
  },
  snapshot: () => exclusive('capturing image', () => snapshotImageJ(requireApi())),
}
for (const [name, action] of Object.entries(bridge.actions)) {
  props.host.defineAction(name, async (args: Record<string, unknown>) => {
    const result = await action(args)
    props.host.setState(await readState())
    return result
  })
}
props.host.onSnapshot(bridge.snapshot)

const current = computed(() => openPath.value ?? props.path)
const label = computed(() => (current.value ? baseName(current.value) : 'no file'))

/** So "slow" and "stuck" are distinguishable while a big image decodes. */
const progress = computed(() => {
  if (!stage.value) return ''
  const mb = bytes.value ? ` · ${(bytes.value / 1e6).toFixed(1)} MB` : ''
  const secs = elapsed.value > 2 ? ` · ${elapsed.value}s` : ''
  return `${stage.value}${mb}${secs}`
})

/**
 * Compressed size understates the work badly: 39 MB of LZW-packed uint16
 * becomes several hundred megabytes once ImageJ has it in memory, on a 32-bit
 * JVM. So the threshold sits well below what the file size suggests.
 */
const rewrittenTitle = computed(() => {
  const r = rewritten.value
  if (!r) return ''
  const why = r.reason ? `ImageJ1 cannot read ${r.reason}` : 'ImageJ1 cannot read this variant'
  const scaled = r.downsampled && r.downsampled > 1
    ? `, and it was decimated 1/${r.downsampled} to fit a 32-bit JVM`
    : ''
  return `${why}, so the pod rewrote it as a plain uncompressed TIFF${scaled}.`
})

const heavy = computed(() => stage.value === 'decoding' && bytes.value > 20e6)

const size = computed(() => {
  const d = dimensions.value
  if (!d) return ''
  const [w, h, c, z, t] = d
  const extra = [c > 1 && `${c}c`, z > 1 && `${z}z`, t > 1 && `${t}t`].filter(Boolean)
  return `${w}×${h}${extra.length ? ` · ${extra.join(' ')}` : ''}`
})

async function boot() {
  if (!container.value || api) return
  status.value = 'starting'
  error.value = ''
  try {
    const started = await createImageJ(container.value)
    api = started.api
    dispose = started.dispose
    status.value = 'ready'
  } catch (e) {
    console.warn('[atrium] ImJoy core unavailable, falling back to ?open=', e)
    degraded.value = true
    status.value = 'ready'
    error.value = ''
    await openCurrent()
    bootReject(new Error('ImageJ is in limited mode: its RPC control channel could not start'))
    return
  }
  try {
    if (current.value) await openCurrent(true)
    bootResolve()
  } catch (cause) {
    bootReject(cause instanceof Error ? cause : new Error(String(cause)))
  }
}

async function openCurrent(strict = false) {
  if (!workspace.ready) return
  if (!degraded.value && !api) return

  if (operation) {
    if (strict) throw new Error(`ImageJ is busy with ${operation}`)
    error.value = `ImageJ is busy with ${operation}`
    return
  }
  operation = 'opening image'
  status.value = 'opening'
  error.value = ''
  dimensions.value = null
  elapsed.value = 0
  bytes.value = 0
  clock = setInterval(() => { elapsed.value += 1 }, 1000)
  try {
    if (degraded.value) {
      // One-way: hand the URL over and hope. Reloads the whole app.
      frameSrc.value = current.value
        ? `https://ij.imjoy.io/?open=${encodeURIComponent(String((await props.host.call('prepare', { path: current.value }, { timeoutMs: 600000 })).url))}`
        : 'https://ij.imjoy.io'
    } else if (current.value) {
      // Ask the pod what this actually is before handing it over. ImageJ's
      // refusal is a dialog, not an error, so a file it cannot read would
      // otherwise look identical to one that is merely slow.
      stage.value = 'preparing'
      rewritten.value = null
      const prepared = await props.host.call('prepare', { path: current.value }, { timeoutMs: 600000 })
      if (prepared.converted) {
        rewritten.value = { reason: prepared.reason, downsampled: prepared.downsampled }
      }

      const url = prepared.url
      dimensions.value = await openUrl(
        api!, url, baseName(prepared.path === current.value ? current.value : prepared.path),
        (s, n) => { stage.value = s; if (n) bytes.value = n },
      )
      imageState = await readImageJState(api!)
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    if (strict) throw e
  } finally {
    if (clock) clearInterval(clock)
    clock = null
    stage.value = null
    status.value = 'ready'
    operation = ''
  }
}

function pick(path: string) {
  picking.value = false
  openPath.value = path
  void openCurrent().then(() => props.host.setState({ path }))
}

onMounted(boot)
onBeforeUnmount(() => {
  if (clock) clearInterval(clock)
  dispose?.()
  api = null
})

watch(() => workspace.ready, (ready) => { if (ready && current.value) void openCurrent() })
props.host.onState(async (state) => {
  if (typeof state.path === 'string' && state.path !== current.value) {
    openPath.value = state.path
    // The host acknowledges desktop_update only after the image is visible.
    await openCurrent(true)
  }
})
</script>

<template>
  <div class="imagej">
    <header class="bar">
      <button
        class="open"
        :disabled="!workspace.ready || status === 'starting'"
        :title="workspace.ready ? 'Open a file from the sandbox' : 'Waiting for the pod'"
        @click="picking = true"
      >
        <span aria-hidden="true">＋</span>
        <span>Open from sandbox</span>
      </button>

      <span class="current">{{ label }}</span>
      <span v-if="size" class="dims">{{ size }}</span>
      <span v-if="status === 'opening'" class="busy">{{ progress || 'opening…' }}</span>
      <span v-if="rewritten" class="hint" :title="rewrittenTitle">
        rewritten for ImageJ{{ rewritten.downsampled && rewritten.downsampled > 1
          ? ` · 1/${rewritten.downsampled} scale` : '' }}
      </span>
      <span v-if="heavy" class="hint">
        large images are slow here — Viv handles pyramids better
      </span>
      <span
        v-if="degraded"
        class="degraded"
        title="The ImJoy core could not be fetched, so ImageJ runs without the RPC channel: no confirmation that a file opened, and a second file reloads the app."
      >limited mode</span>
      <span v-if="error" class="failed" :title="error">{{ error }}</span>
    </header>

    <div v-show="!degraded" ref="container" class="stage" />
    <iframe v-if="degraded && frameSrc" class="stage" :src="frameSrc" :title="label" />

    <div v-if="status === 'starting'" class="overlay">
      <p>Starting ImageJ.js…</p>
      <p class="note">
        ImageJ is a Java application compiled to the browser; the first load
        takes a moment.
      </p>
    </div>
    <div v-else-if="status === 'error'" class="overlay failed-overlay">
      <p>{{ error }}</p>
    </div>

    <FilePicker
      v-if="picking"
      :accept="OPENABLE"
      :host="host"
      title="Open in ImageJ.js"
      @pick="pick"
      @close="picking = false"
    />
  </div>
</template>

<style scoped>
.imagej {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--raised);
}

.bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 9px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  flex: none;
  font-size: 11.5px;
}
.open {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--hover);
  color: var(--text);
  font-size: 11.5px;
  cursor: pointer;
  flex: none;
}
.open:hover:not(:disabled) { background: var(--hover); }
.open:disabled { opacity: 0.45; cursor: default; }

.current {
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dims { color: var(--text-faint); font-variant-numeric: tabular-nums; flex: none; }
.busy { color: #d0a029; flex: none; font-variant-numeric: tabular-nums; }
.hint { color: var(--text-faint); flex: none; }
.degraded {
  flex: none;
  padding: 1px 7px;
  border-radius: 9px;
  background: rgba(208, 160, 41, 0.15);
  color: #d0a029;
  cursor: help;
}
.failed {
  margin-left: auto;
  color: #f0a3a2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 45%;
}

.stage {
  position: relative;
  flex: 1;
  min-height: 0;
  background: #f2f2f2;
}
/* ImJoy sizes its plugin container itself; make it fill ours. */
.stage :deep(> div),
.stage :deep(iframe) {
  width: 100%;
  height: 100%;
  border: 0;
}

.overlay {
  position: absolute;
  inset: 32px 0 0 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  background: var(--raised);
  color: var(--text-faint);
  font-size: 12.5px;
  text-align: center;
}
.failed-overlay { color: #f0a3a2; }
.overlay p { margin: 0; max-width: 420px; }
.note { font-size: 11px; opacity: 0.75; }
</style>
