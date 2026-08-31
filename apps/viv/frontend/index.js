/**
 * Viv LiveView adapter — a viewer plugin for bioimaging data.
 *
 * Renders high-resolution, multiplexed bioimages (OME-TIFF, OME-Zarr) with
 * Viv's PictureInPictureViewer, plus a per-channel controls panel. Like
 * every viewer it is an ordinary setup(lv, root) module loaded by the
 * generic host: React resolves via the host import map; Viv is imported by
 * full URL from esm.sh, with react kept external (single React instance).
 *
 * LiveView "state" for Viv:
 *   {
 *     "url": "https://.../image.ome.tif",  // REQUIRED — cloud or served URL
 *     "type": "ome-tiff" | "ome-zarr",     // optional — inferred from url
 *     "channels": [                        // optional — omit to auto-fill
 *       { "selection": {"c":0,"t":0,"z":0},
 *         "color": [0,0,255], "contrastLimits": [0,5000], "visible": true }
 *     ],
 *     "overview": true                     // optional — overview inset
 *   }
 * The user drives it with the on-screen controls; the agent drives it by
 * setting the whole `channels` array via live_view_update / set_state.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import {
  loadOmeTiff,
  loadOmeZarr,
  getChannelStats,
  PictureInPictureViewer,
  LensExtension,
} from 'https://esm.sh/@hms-dbmi/viv@0.21.0?external=react,react-dom'

const h = React.createElement
const PALETTE = [
  [0, 0, 255], [0, 255, 0], [255, 0, 255], [255, 255, 0],
  [255, 128, 0], [0, 255, 255], [255, 255, 255], [255, 0, 0],
]
const DEFAULT_OVERVIEW = { margin: 25, scale: 0.15, position: 'bottom-left' }
const MAX_DEFAULT_CHANNELS = 6

function dtypeRange(dtype) {
  const d = String(dtype || '').toLowerCase()
  if (d.includes('float')) return [0, 1]
  if (d.includes('8')) return [0, 255]
  return [0, 65535] // uint16 / int16 / uint32 default
}
const toHex = ([r, g, b]) =>
  '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, v | 0))
    .toString(16).padStart(2, '0')).join('')
const fromHex = (hex) => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
]

// ── Per-channel controls panel ──────────────────────────────────────────
function Controls({ channels, domainMax, onChange }) {
  const [open, setOpen] = React.useState(true)
  const patch = (i, p) =>
    onChange(channels.map((c, j) => (j === i ? { ...c, ...p } : c)))

  const panel = {
    position: 'absolute', top: 10, right: 10, zIndex: 10,
    background: 'rgba(13,17,23,.86)', border: '1px solid #30363d',
    borderRadius: 8, color: '#c9d1d9', font: '12px system-ui',
    padding: open ? '8px 10px' : '4px 8px', backdropFilter: 'blur(6px)',
    maxWidth: 270,
  }
  if (!open) {
    return h('div', { style: panel },
      h('button', { onClick: () => setOpen(true), style: btnStyle }, '⚙ Channels'))
  }
  const step = Math.max(1, Math.round(domainMax / 1000))
  return h('div', { style: panel },
    h('div', { style: { display: 'flex', justifyContent: 'space-between',
      alignItems: 'center', marginBottom: 6 } },
      h('strong', null, 'Channels'),
      h('button', { onClick: () => setOpen(false), style: btnStyle }, '×')),
    channels.map((c, i) =>
      h('div', { key: i, style: { display: 'flex', alignItems: 'center',
        gap: 6, margin: '5px 0' } },
        h('input', { type: 'checkbox', checked: c.visible !== false,
          onChange: (e) => patch(i, { visible: e.target.checked }),
          title: 'Visible' }),
        h('input', { type: 'color', value: toHex(c.color || [255, 255, 255]),
          onChange: (e) => patch(i, { color: fromHex(e.target.value) }),
          style: { width: 24, height: 20, padding: 0, border: 'none',
            background: 'none' }, title: 'Color' }),
        h('span', { style: { width: 26, opacity: 0.8 } }, 'C' +
          (c.selection && c.selection.c != null ? c.selection.c : i)),
        h('input', { type: 'range', min: 0, max: domainMax, step,
          value: (c.contrastLimits || [0, domainMax])[0],
          onChange: (e) => patch(i, { contrastLimits:
            [+e.target.value, (c.contrastLimits || [0, domainMax])[1]] }),
          style: { flex: 1, minWidth: 0 }, title: 'Contrast min' }),
        h('input', { type: 'range', min: 0, max: domainMax, step,
          value: (c.contrastLimits || [0, domainMax])[1],
          onChange: (e) => patch(i, { contrastLimits:
            [(c.contrastLimits || [0, domainMax])[0], +e.target.value] }),
          style: { flex: 1, minWidth: 0 }, title: 'Contrast max' }))))
}
const btnStyle = {
  background: 'none', border: '1px solid #30363d', borderRadius: 5,
  color: '#c9d1d9', cursor: 'pointer', font: '12px system-ui', padding: '2px 6px',
}

// ── Viewer + controls ───────────────────────────────────────────────────
function VivApp({ pyramid, state, channels, domainMax, onChange, onView, size }) {
  return h('div', { style: { position: 'relative', width: '100%', height: '100%' } },
    h(PictureInPictureViewer, {
      loader: pyramid,
      selections: channels.map((c) => c.selection),
      contrastLimits: channels.map((c) => c.contrastLimits),
      colors: channels.map((c) => c.color),
      channelsVisible: channels.map((c) => c.visible !== false),
      height: size.h,
      width: size.w,
      // The camera is part of what two people looking at one image should
      // agree on, so it has to leave the component: PictureInPictureViewer
      // keeps it internally and reports nothing unless asked.
      viewStates: state.viewState ? [{ ...state.viewState, id: 'detail' }] : undefined,
      onViewStateChange: ({ viewState, viewId }) => {
        if (viewId === 'detail') onView(viewState)
      },
      overview: DEFAULT_OVERVIEW,
      overviewOn: state.overview !== false,
      extensions: [new LensExtension()],
      // Keep the WebGL backbuffer readable so snapshots can capture it.
      deckProps: { glOptions: { preserveDrawingBuffer: true } },
    }),
    h(Controls, { key: state.panelSeq || 0, channels, domainMax, onChange }))
}

export function setup(lv, root) {
  let pyramid = null
  let loadedUrl = null
  let reactRoot = null

  function showStatus(msg) {
    if (reactRoot) {
      reactRoot.unmount()
      reactRoot = null
    }
    root.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:center;' +
      'height:100%;color:#8b949e;font:13px system-ui;text-align:center;' +
      'padding:24px">' + msg + '</div>'
  }

  async function ensureLoaded(state) {
    if (state.url === loadedUrl && pyramid) return
    showStatus('Loading image…')
    const isZarr =
      String(state.type || '').includes('zarr') || /\.zarr(\/|$)/i.test(state.url)
    const res = isZarr
      ? await loadOmeZarr(state.url, { type: 'multiscales' })
      : await loadOmeTiff(state.url)
    const src = Array.isArray(res) ? res[0] : res
    pyramid = src.data
    loadedUrl = state.url
  }

  function channelSelections() {
    const s0 = pyramid[0]
    const labels = s0.labels || []
    const ci = labels.indexOf('c')
    const n = ci >= 0 ? Math.min(s0.shape[ci], MAX_DEFAULT_CHANNELS) : 1
    const sels = []
    for (let i = 0; i < n; i++) {
      const sel = {}
      for (const l of labels) {
        if (l === 'x' || l === 'y') continue
        sel[l] = l === 'c' ? i : 0
      }
      sels.push(sel)
    }
    return sels
  }

  async function defaultChannels() {
    const sels = channelSelections()
    const statSrc = pyramid[pyramid.length - 1]
    const fallback = dtypeRange(pyramid[0].dtype)
    const channels = []
    for (let i = 0; i < sels.length; i++) {
      let cl = fallback
      try {
        const raster = await statSrc.getRaster({ selection: sels[i] })
        const st = getChannelStats(raster.data)
        if (st && st.contrastLimits) {
          cl = st.contrastLimits
          if (cl[0] === cl[1] && st.domain) cl = st.domain
        }
      } catch (e) {
        /* keep dtype fallback */
      }
      channels.push({
        selection: sels[i],
        color: PALETTE[i % PALETTE.length],
        contrastLimits: cl,
        visible: true,
      })
    }
    return channels
  }

  function renderViewer(state, channels) {
    if (!reactRoot) {
      root.innerHTML = ''
      reactRoot = createRoot(root)
    }
    reactRoot.render(
      h(VivApp, {
        pyramid,
        state,
        channels,
        domainMax: dtypeRange(pyramid[0].dtype)[1],
        size: { w: window.innerWidth, h: window.innerHeight },
        onChange: (next) => lv.setState({ channels: next }),
        // Straight into state, unthrottled: the shell coalesces `stream` keys
        // to ~10 Hz, so the app does not have to know the wire rate.
        onView: (viewState) => lv.setState({ viewState }),
      }),
    )
  }

  lv.onState(async (state) => {
    try {
      // A plain TIFF is this app's own backend's job: prepare() rewrites it
      // as pyramidal OME-TIFF in Viv's supervised process and answers with
      // the URL and channel setup. Guarded on lv.call so this same module
      // still runs under hosts that have no backend bridge (pantheon-ui's
      // LiveView, agent-opened views) — there state arrives with an OME url.
      const needsPrepare = state && state.path && !state.prepared
        && /\.(tif|tiff)$/i.test(state.path) && !/\.ome\.(tif|tiff)$/i.test(state.path)
      if (needsPrepare && typeof lv.call === 'function') {
        showStatus('Preparing ' + (state.name || state.path) + '\u2026')
        const prepared = await lv.call('prepare', { path: state.path }, { timeoutMs: 600000 })
        lv.setState({ ...prepared })
        return
      }
      if (!state || !state.url) {
        showStatus('No image. Set state.url to an OME-TIFF / OME-Zarr URL.')
        return
      }
      await ensureLoaded(state)
      let channels = state.channels
      if (!Array.isArray(channels) || channels.length === 0) {
        channels = await defaultChannels()
        lv.setState({ channels }) // re-fires onState; render happens then
        return
      }
      renderViewer(state, channels)
    } catch (e) {
      showStatus('Failed to load image: ' + ((e && e.message) || e))
      lv.fail('Viv failed: ' + ((e && e.message) || e))
    }
  })

  window.addEventListener('resize', () => {
    const s = lv.state
    if (pyramid && s && Array.isArray(s.channels)) renderViewer(s, s.channels)
  })

  // Snapshot provider — Viv renders to a WebGL canvas html2canvas cannot
  // capture. Read that canvas directly, downscaled (a full-res capture is
  // too large to ship back), so live_view_screenshot returns the real view.
  // Menu items and desktop_call land here alike — one action, three triggers.
  lv.defineAction('showChannels', () => {
    lv.setState({ panelSeq: (lv.state && lv.state.panelSeq || 0) + 1 })
    return { shown: true }
  })

  lv.onSnapshot(() => {
    const canvases = Array.from(root.querySelectorAll('canvas'))
    if (canvases.length === 0) return null
    const src = canvases.reduce((a, b) =>
      a.width * a.height >= b.width * b.height ? a : b,
    )
    try {
      const scale = Math.min(1, 1024 / Math.max(src.width, 1))
      const out = document.createElement('canvas')
      out.width = Math.round(src.width * scale)
      out.height = Math.round(src.height * scale)
      out.getContext('2d').drawImage(src, 0, 0, out.width, out.height)
      return out.toDataURL('image/jpeg', 0.8)
    } catch (e) {
      return null // fall back to the host's html2canvas
    }
  })
}
