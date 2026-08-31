/**
 * Volume3D LiveView adapter — volumetric MIP/ISO renderer for an OME-NGFF
 * Zarr pyramid. Ported from the Virtual Embryo `Volume3DViewer.vue`
 * (Three.js raycasting volume shader + zarrita).
 *
 * This is an ordinary LiveView component plugin: it exports `setup(lv, root)`
 * and is loaded by the generic host `app-host.html`. No build step — Three.js
 * and zarrita load from esm.sh as browser ES modules. The three.js example
 * addons are pinned to the SAME three instance via `?deps=three@<ver>` so
 * there is only one THREE in the module graph.
 *
 * ── State (the LiveView "state" object the agent drives) ───────────────────
 *   {
 *     url,            OME-NGFF pyramid group URL (served by the data server)
 *     level,          pyramid level index, or 'auto' (default): coarsest ≤32M voxels
 *     mode,           'mip' | 'iso'        (default 'iso')
 *     threshold,      0..1 ISO iso-surface level                 (default 0.45)
 *     brightness,     0.1..3 upper-window compression            (default 1.2)
 *     flipUp,         boolean — 180° about world X (head-up vs head-down scans)
 *     title,          header label
 *     camera,         {position:[x,y,z], target:[x,y,z], zoom} — round-trips on orbit
 *   }
 *
 * url/level changes rebuild the volume; mode/threshold/brightness/flipUp are
 * applied in place. The camera round-trips (orbit → emitState) so the agent
 * can read / restore the view and screenshots reflect the user's angle.
 */
import * as THREE from 'https://esm.sh/three@0.164.1'
import { OrbitControls } from 'https://esm.sh/three@0.164.1/examples/jsm/controls/OrbitControls.js?deps=three@0.164.1'
import { VolumeRenderShader1 } from 'https://esm.sh/three@0.164.1/examples/jsm/shaders/VolumeShader.js?deps=three@0.164.1'
import * as zarr from 'https://esm.sh/zarrita@0.7.2'

const MAX_VOXELS = 32_000_000 // GPU + network budget; coarsest level under this

// ════════════════════════ OME-NGFF / zarr helpers ═════════════════════════
// Ported from virtualembryo-web/utils/zarr.ts (plain, framework-free).

/** Open a pyramid group and return its multiscale metadata (no chunks). */
async function openNgff(url) {
  const store = new zarr.FetchStore(url)
  const rootGrp = await zarr.open(store, { kind: 'group' })
  const attrs = rootGrp.attrs || {}
  const ms = attrs.multiscales && attrs.multiscales[0]
  if (!ms) throw new Error(`No multiscales metadata at ${url}`)
  return { url, axes: ms.axes || [], datasets: ms.datasets || [] }
}

/** Probe each level's .zarray for its shape, without loading chunks. */
async function fetchLevelShapes(pyramid) {
  const shapes = []
  for (const ds of pyramid.datasets) {
    try {
      const r = await fetch(`${pyramid.url}/${ds.path}/.zarray`)
      if (!r.ok) { shapes.push([]); continue }
      const z = await r.json()
      shapes.push(z.shape || [])
    } catch {
      shapes.push([])
    }
  }
  return shapes
}

/** Coarsest→finest: first level whose total voxel count fits the budget. */
function pickRenderLevel(shapes, maxVoxels = MAX_VOXELS) {
  for (let i = 0; i < shapes.length; i++) {
    const s = shapes[i]
    if (!s.length) continue
    const total = s.reduce((a, b) => a * b, 1)
    if (total <= maxVoxels) return i
  }
  return shapes.length - 1
}

/** Fetch one pyramid level as a dense Float32Array + shape/scale/min/max. */
async function fetchLevel(pyramid, levelIndex) {
  const level = pyramid.datasets[levelIndex]
  if (!level) throw new Error(`Level ${levelIndex} out of range`)
  const store = new zarr.FetchStore(pyramid.url)
  const location = zarr.root(store).resolve(level.path)
  const arr = await zarr.open(location, { kind: 'array' })
  const res = await zarr.get(arr)
  const raw = res.data
  const shape = res.shape
  const f = new Float32Array(raw.length)
  let min = Infinity
  let max = -Infinity
  for (let i = 0; i < raw.length; i++) {
    const v = Number(raw[i])
    f[i] = v
    if (v < min) min = v
    if (v > max) max = v
  }
  const scaleT = (level.coordinateTransformations || []).find((t) => t.type === 'scale')
  const scale = (scaleT && scaleT.scale) || shape.map(() => 1)
  return { data: f, shape, dtype: raw.constructor.name, scale, min, max }
}

/** Float volume → Uint8 with a percentile clip for better contrast. */
function normaliseToU8(data, pLow = 0.005, pHigh = 0.998) {
  const stride = Math.max(1, Math.floor(data.length / 200_000))
  const sample = new Float32Array(Math.ceil(data.length / stride))
  for (let i = 0, j = 0; i < data.length; i += stride, j++) sample[j] = data[i]
  sample.sort()
  const lo = sample[Math.floor(sample.length * pLow)]
  const hi = sample[Math.floor(sample.length * pHigh)]
  const range = Math.max(1e-6, hi - lo)
  const u8 = new Uint8Array(data.length)
  for (let i = 0; i < data.length; i++) {
    const v = (data[i] - lo) / range
    u8[i] = Math.max(0, Math.min(255, Math.round(v * 255)))
  }
  return u8
}

/** 256-stop dark-palette colormap: deep-navy → teal → violet → amber → white. */
function makeViridisLut() {
  const stops = [
    [0.00, [0x05, 0x0a, 0x14]],
    [0.15, [0x13, 0x2f, 0x33]],
    [0.30, [0x2e, 0x8a, 0x93]],
    [0.55, [0xb8, 0xa0, 0xc9]],
    [0.80, [0xe3, 0xb6, 0x8a]],
    [1.00, [0xff, 0xff, 0xff]],
  ]
  const data = new Uint8Array(256 * 4)
  for (let i = 0; i < 256; i++) {
    const t = i / 255
    let a = stops[0]; let b = stops[stops.length - 1]
    for (let s = 0; s < stops.length - 1; s++) {
      if (t >= stops[s][0] && t <= stops[s + 1][0]) { a = stops[s]; b = stops[s + 1]; break }
    }
    const f = (t - a[0]) / Math.max(1e-6, b[0] - a[0])
    data[i * 4 + 0] = Math.round(a[1][0] + (b[1][0] - a[1][0]) * f)
    data[i * 4 + 1] = Math.round(a[1][1] + (b[1][1] - a[1][1]) * f)
    data[i * 4 + 2] = Math.round(a[1][2] + (b[1][2] - a[1][2]) * f)
    data[i * 4 + 3] = 255
  }
  const tex = new THREE.DataTexture(data, 256, 1, THREE.RGBAFormat, THREE.UnsignedByteType)
  tex.needsUpdate = true
  return tex
}

// ════════════════════════════ DOM chrome ══════════════════════════════════
// A small dark frame consistent with the app: badge + title topbar, a compact
// control bar (MIP/ISO, threshold, brightness, flip), the WebGL viewport, and
// a bottom-left HUD. Kept inline so the plugin is a single file.
const CSS = `
.v3d-root{position:absolute;inset:0;display:flex;flex-direction:column;
  background:transparent;color:#c9d1d9;font-family:system-ui,-apple-system,sans-serif;
  overflow:hidden}
.v3d-bar{display:flex;align-items:center;gap:10px;padding:8px 12px;flex:0 0 auto;
  background:rgba(20,24,30,.72);backdrop-filter:blur(8px);
  border-bottom:1px solid rgba(255,255,255,.07);position:sticky;top:0;z-index:3}
.v3d-badge{width:26px;height:26px;border-radius:7px;flex:0 0 auto;display:grid;
  place-items:center;background:linear-gradient(135deg,#1f6f6f,#7b5ea7)}
.v3d-badge svg{width:15px;height:15px;display:block}
.v3d-title{font-size:13px;font-weight:600;color:#e6edf3;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.v3d-spacer{flex:1 1 auto;min-width:8px}
.v3d-seg{display:inline-flex;border:1px solid rgba(255,255,255,.14);border-radius:7px;
  overflow:hidden}
.v3d-seg button{appearance:none;border:0;background:transparent;color:#9aa4b2;
  font-size:11px;font-weight:600;padding:4px 10px;cursor:pointer;letter-spacing:.02em}
.v3d-seg button.on{background:rgba(123,94,167,.32);color:#f0ecf7}
.v3d-ctl{display:flex;align-items:center;gap:6px;font-size:11px;color:#9aa4b2}
.v3d-ctl input[type=range]{width:84px;accent-color:#7b5ea7}
.v3d-btn{appearance:none;border:1px solid rgba(255,255,255,.14);border-radius:7px;
  background:transparent;color:#9aa4b2;font-size:11px;font-weight:600;padding:4px 9px;
  cursor:pointer}
.v3d-btn.on{background:rgba(46,138,147,.28);color:#dff3f4;border-color:transparent}
.v3d-view{position:relative;flex:1 1 auto;min-height:0}
.v3d-view canvas{display:block}
.v3d-hud{position:absolute;left:10px;bottom:10px;padding:4px 9px;border-radius:6px;
  background:rgba(0,0,0,.42);backdrop-filter:blur(6px);font:11px/1.3 ui-monospace,monospace;
  color:#9aa4b2;border:1px solid rgba(255,255,255,.08);pointer-events:none}
.v3d-overlay{position:absolute;inset:0;display:grid;place-items:center;
  background:rgba(8,12,20,.78);backdrop-filter:blur(3px);font-size:13px;color:#8b949e;
  text-align:center;padding:24px;z-index:2}
.v3d-overlay.err{color:#f0857d}
.v3d-overlay.hidden{display:none}
.v3d-dot{display:inline-block;width:8px;height:8px;border-radius:50%;
  background:#2e8a93;margin-right:8px;animation:v3dpulse 1.1s ease-in-out infinite}
@keyframes v3dpulse{0%,100%{opacity:.35}50%{opacity:1}}
`

// A simple wireframe-cube badge (stands in for "volume").
const CUBE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="#eaf2ff"
  stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 2.6 20.5 7v10L12 21.4 3.5 17V7z"/>
  <path d="M3.5 7 12 11.4 20.5 7M12 11.4V21.4" opacity=".7"/></svg>`

// ════════════════════════════ component ═══════════════════════════════════
function __viewerSetup(lv, root) {
  root.innerHTML = ''
  const style = document.createElement('style')
  style.textContent = CSS
  root.appendChild(style)

  const el = document.createElement('div')
  el.className = 'v3d-root'
  el.innerHTML = `
    <div class="v3d-bar">
      <div class="v3d-badge">${CUBE_SVG}</div>
      <div class="v3d-title" data-title>Volume</div>
      <div class="v3d-spacer"></div>
      <div class="v3d-seg" data-seg>
        <button data-mode="mip">MIP</button><button data-mode="iso">ISO</button>
      </div>
      <label class="v3d-ctl" data-thr-wrap>iso
        <input type="range" min="0" max="1" step="0.01" data-thr></label>
      <label class="v3d-ctl">bright
        <input type="range" min="0.1" max="3" step="0.05" data-bri></label>
      <button class="v3d-btn" data-flip>Flip</button>
    </div>
    <div class="v3d-view" data-view>
      <div class="v3d-hud" data-hud style="display:none"></div>
      <div class="v3d-overlay" data-overlay><span><span class="v3d-dot"></span>Loading volume…</span></div>
    </div>`
  root.appendChild(el)

  const view = el.querySelector('[data-view]')
  const overlay = el.querySelector('[data-overlay]')
  const hud = el.querySelector('[data-hud]')
  const titleEl = el.querySelector('[data-title]')
  const seg = el.querySelector('[data-seg]')
  const thrInput = el.querySelector('[data-thr]')
  const thrWrap = el.querySelector('[data-thr-wrap]')
  const briInput = el.querySelector('[data-bri]')
  const flipBtn = el.querySelector('[data-flip]')

  // ── three.js handles (kept across state updates) ──
  let renderer = null
  let scene = null
  let camera = null
  let controls = null
  let mesh = null
  let meshGroup = null
  let material = null
  let animId = 0
  let ro = null

  // ── state bookkeeping ──
  let applied = null        // last full state we rendered from
  let buildToken = 0        // guards against overlapping async rebuilds
  let userDragging = false
  let lastCamKey = ''       // pose currently shown — suppresses echo re-apply
  let camTimer = null

  const showOverlay = (msg, isErr) => {
    overlay.className = 'v3d-overlay' + (isErr ? ' err' : '')
    overlay.innerHTML = isErr
      ? `<span>${msg}</span>`
      : `<span><span class="v3d-dot"></span>${msg}</span>`
  }
  const hideOverlay = () => { overlay.className = 'v3d-overlay hidden' }

  // ───────────────────────────── teardown ─────────────────────────────
  function disposeScene() {
    cancelAnimationFrame(animId); animId = 0
    if (ro) { ro.disconnect(); ro = null }
    if (controls) { controls.dispose(); controls = null }
    if (renderer) {
      renderer.dispose()
      if (renderer.domElement && renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement)
      }
      renderer = null
    }
    if (material) { material.dispose(); material = null }
    if (mesh) { mesh.geometry.dispose(); mesh = null }
    meshGroup = null
    scene = null
  }

  // ─────────────────────── the full build pipeline ────────────────────
  // Faithful port of Volume3DViewer.vue init(): fetch level → percentile
  // normalise → 3-pass box smooth → histogram mode → histology polarity
  // invert → Otsu threshold → foreground bbox crop → Data3DTexture →
  // ortho camera + OrbitControls → VolumeRenderShader1 uniforms → pivoted
  // mesh group (so flip rotates about the embryo).
  async function buildVolume(state) {
    const token = ++buildToken
    showOverlay('Loading volume…', false)
    hud.style.display = 'none'
    try {
      const pyramid = await openNgff(state.url)
      let chosenLevel
      if (state.level === undefined || state.level === null || state.level === 'auto') {
        chosenLevel = pickRenderLevel(await fetchLevelShapes(pyramid))
      } else {
        chosenLevel = state.level
      }
      const { data, shape, scale, dtype } = await fetchLevel(pyramid, chosenLevel)
      if (token !== buildToken) return // superseded by a newer build
      const u8 = normaliseToU8(data, 0.005, 0.998)

      const [sz, sy, sx] = shape       // OME-NGFF order Z, Y, X
      const [vz, vy, vx] = scale
      const sxsy = sx * sy

      // 3-pass separable box smoothing (skipped for very large textures).
      if (u8.length < 8_000_000) {
        const tmp = new Uint8Array(u8.length)
        for (let z = 0; z < sz; z++) for (let y = 0; y < sy; y++) {
          const row = z * sxsy + y * sx
          for (let x = 0; x < sx; x++) {
            const xm = Math.max(0, x - 1); const xp = Math.min(sx - 1, x + 1)
            tmp[row + x] = (u8[row + xm] + u8[row + x] + u8[row + xp]) / 3 | 0
          }
        }
        for (let z = 0; z < sz; z++) {
          const slice = z * sxsy
          for (let y = 0; y < sy; y++) {
            const ym = Math.max(0, y - 1); const yp = Math.min(sy - 1, y + 1)
            for (let x = 0; x < sx; x++) {
              u8[slice + y * sx + x] =
                (tmp[slice + ym * sx + x] + tmp[slice + y * sx + x] + tmp[slice + yp * sx + x]) / 3 | 0
            }
          }
        }
        for (let z = 0; z < sz; z++) {
          const zm = Math.max(0, z - 1) * sxsy; const z0 = z * sxsy
          const zp = Math.min(sz - 1, z + 1) * sxsy
          for (let y = 0; y < sy; y++) {
            const row = y * sx
            for (let x = 0; x < sx; x++) {
              tmp[z0 + row + x] = (u8[zm + row + x] + u8[z0 + row + x] + u8[zp + row + x]) / 3 | 0
            }
          }
        }
        u8.set(tmp)
      }

      // Histogram mode → histology polarity inversion → Otsu threshold.
      const hist = new Uint32Array(256)
      for (let i = 0; i < u8.length; i++) hist[u8[i]]++
      let modeBin = 0; let modeCount = 0
      for (let i = 0; i < 256; i++) if (hist[i] > modeCount) { modeCount = hist[i]; modeBin = i }
      if (modeBin > 127) { // tissue stored dark on bright bg → invert
        for (let i = 0; i < u8.length; i++) u8[i] = 255 - u8[i]
        hist.fill(0)
        for (let i = 0; i < u8.length; i++) hist[u8[i]]++
        modeBin = 0; modeCount = 0
        for (let i = 0; i < 256; i++) if (hist[i] > modeCount) { modeCount = hist[i]; modeBin = i }
      }
      let total = 0; for (let i = 0; i < 256; i++) total += hist[i]
      let sumAll = 0; for (let i = 0; i < 256; i++) sumAll += i * hist[i]
      let otsuT = 0; let otsuVar = 0; let wB = 0; let sumB = 0
      for (let t = 0; t < 256; t++) {
        wB += hist[t]; if (!wB) continue
        const wF = total - wB; if (!wF) break
        sumB += t * hist[t]
        const mB = sumB / wB; const mF = (sumAll - sumB) / wF
        const v = wB * wF * (mB - mF) * (mB - mF)
        if (v > otsuVar) { otsuVar = v; otsuT = t }
      }
      const THRESH = Math.min(254, Math.max(modeBin + 20, otsuT + 10))

      // Foreground bbox (for crop) at u8 ≥ THRESH.
      let n = 0
      let minX = sx + 1; let maxX = -1; let minY = sy + 1; let maxY = -1
      let minZ = sz + 1; let maxZ = -1
      for (let z = 0; z < sz; z++) for (let y = 0; y < sy; y++) {
        const rowOff = z * sxsy + y * sx
        for (let x = 0; x < sx; x++) {
          if (u8[rowOff + x] >= THRESH) {
            n++
            if (x < minX) minX = x; if (x > maxX) maxX = x
            if (y < minY) minY = y; if (y > maxY) maxY = y
            if (z < minZ) minZ = z; if (z > maxZ) maxZ = z
          }
        }
      }
      let cVoxX; let cVoxY; let cVoxZ; let bbVoxX; let bbVoxY; let bbVoxZ
      if (n > 100) {
        const PAD = 0.07
        cVoxX = (minX + maxX) * 0.5; cVoxY = (minY + maxY) * 0.5; cVoxZ = (minZ + maxZ) * 0.5
        bbVoxX = Math.max(2, (maxX - minX) * (1 + PAD * 2) + 4)
        bbVoxY = Math.max(2, (maxY - minY) * (1 + PAD * 2) + 4)
        bbVoxZ = Math.max(2, (maxZ - minZ) * (1 + PAD * 2) + 4)
      } else {
        cVoxX = sx / 2; cVoxY = sy / 2; cVoxZ = sz / 2
        bbVoxX = sx; bbVoxY = sy; bbVoxZ = sz
      }

      // Crop the texture to the embryo bbox (hides scanner artefacts).
      const halfX = bbVoxX / 2; const halfY = bbVoxY / 2; const halfZ = bbVoxZ / 2
      let cx0 = Math.max(0, Math.floor(cVoxX - halfX)); let cx1 = Math.min(sx, Math.ceil(cVoxX + halfX))
      let cy0 = Math.max(0, Math.floor(cVoxY - halfY)); let cy1 = Math.min(sy, Math.ceil(cVoxY + halfY))
      let cz0 = Math.max(0, Math.floor(cVoxZ - halfZ)); let cz1 = Math.min(sz, Math.ceil(cVoxZ + halfZ))
      if (cx1 - cx0 < 2) { cx0 = 0; cx1 = sx }
      if (cy1 - cy0 < 2) { cy0 = 0; cy1 = sy }
      if (cz1 - cz0 < 2) { cz0 = 0; cz1 = sz }
      const crSx = cx1 - cx0; const crSy = cy1 - cy0; const crSz = cz1 - cz0
      const cropped = new Uint8Array(crSx * crSy * crSz)
      for (let z = 0; z < crSz; z++) {
        const srcZ = (cz0 + z) * sxsy; const dstZ = z * crSx * crSy
        for (let y = 0; y < crSy; y++) {
          const srcRow = srcZ + (cy0 + y) * sx; const dstRow = dstZ + y * crSx
          for (let x = 0; x < crSx; x++) cropped[dstRow + x] = u8[srcRow + cx0 + x]
        }
      }
      if (token !== buildToken) return

      // ── build/refresh the renderer ──
      disposeScene()
      const rect = view.getBoundingClientRect()
      const W = Math.max(1, rect.width); const H = Math.max(1, rect.height)
      renderer = new THREE.WebGLRenderer({
        antialias: true, alpha: true,
        preserveDrawingBuffer: true, // so onSnapshot can read the canvas
      })
      renderer.setPixelRatio(window.devicePixelRatio)
      renderer.setSize(W, H)
      renderer.setClearColor(0x050a14, 1)
      view.appendChild(renderer.domElement)
      scene = new THREE.Scene()

      const worldX = sx * vx; const worldY = sy * vy; const worldZ = sz * vz

      const tex = new THREE.Data3DTexture(cropped, crSx, crSy, crSz)
      tex.format = THREE.RedFormat
      tex.type = THREE.UnsignedByteType
      tex.minFilter = THREE.LinearFilter
      tex.magFilter = THREE.LinearFilter
      tex.unpackAlignment = 1
      tex.needsUpdate = true

      const bboxCenter = [
        ((cx0 + cx1) * 0.5 + 0.5) * vx - worldX * 0.5,
        ((cy0 + cy1) * 0.5 + 0.5) * vy - worldY * 0.5,
        ((cz0 + cz1) * 0.5 + 0.5) * vz - worldZ * 0.5,
      ]
      const bboxSize = [crSx * vx, crSy * vy, crSz * vz]
      const embryoMax = Math.max(...bboxSize)

      const aspect = W / H
      const h = embryoMax * 0.85
      camera = new THREE.OrthographicCamera(-h * aspect, h * aspect, h, -h, 0.01, embryoMax * 20)
      const camDist = embryoMax * 4
      camera.position.set(
        bboxCenter[0] + camDist * 0.9,
        bboxCenter[1] + camDist * 0.7,
        bboxCenter[2] + camDist * 0.9,
      )
      camera.up.set(0, 0, 1)
      camera.lookAt(bboxCenter[0], bboxCenter[1], bboxCenter[2])

      controls = new OrbitControls(camera, renderer.domElement)
      controls.enableDamping = true
      controls.dampingFactor = 0.1
      controls.rotateSpeed = 0.6
      controls.minPolarAngle = -Math.PI
      controls.maxPolarAngle = Math.PI * 2
      controls.target.set(bboxCenter[0], bboxCenter[1], bboxCenter[2])
      controls.update()
      controls.addEventListener('start', () => { userDragging = true })
      controls.addEventListener('end', () => { userDragging = false; scheduleCameraReport() })

      const shader = VolumeRenderShader1
      const uniforms = THREE.UniformsUtils.clone(shader.uniforms)
      uniforms.u_data.value = tex
      uniforms.u_size.value.set(crSx, crSy, crSz)
      const displayFloor = Math.min(THRESH, modeBin + 8) / 255
      uniforms.u_clim.value.set(displayFloor, 1.0)
      uniforms.u_cmdata.value = makeViridisLut()
      material = new THREE.ShaderMaterial({
        uniforms,
        vertexShader: shader.vertexShader,
        fragmentShader: shader.fragmentShader,
        side: THREE.BackSide,
      })

      // Proxy box in voxel units anchored at local origin (the shader reads
      // local x,y,z as the texture index), then scaled to world µm.
      const geometry = new THREE.BoxGeometry(crSx, crSy, crSz)
      geometry.translate(crSx / 2 - 0.5, crSy / 2 - 0.5, crSz / 2 - 0.5)
      mesh = new THREE.Mesh(geometry, material)
      mesh.scale.set(vx, vy, vz)
      mesh.position.set(
        cx0 * vx - worldX * 0.5 - bboxCenter[0],
        cy0 * vy - worldY * 0.5 - bboxCenter[1],
        cz0 * vz - worldZ * 0.5 - bboxCenter[2],
      )
      meshGroup = new THREE.Group()
      meshGroup.position.set(bboxCenter[0], bboxCenter[1], bboxCenter[2])
      meshGroup.add(mesh)
      scene.add(meshGroup)
      material.userData.displayFloor = displayFloor // for brightness math

      ro = new ResizeObserver(resize)
      ro.observe(view)

      const animate = () => {
        if (controls) controls.update()
        if (renderer && scene && camera) renderer.render(scene, camera)
        animId = requestAnimationFrame(animate)
      }
      animate()

      // Apply the live-tunable params + any requested camera from the latest
      // state (which may have advanced while we were fetching).
      applyTuning(applied || state)
      if ((applied || state).camera) applyCamera((applied || state).camera)

      hud.textContent = `${shape.join('×')} · ${scale.map((v) => v.toFixed(1)).join('×')} μm · ${dtype}`
      hud.style.display = ''
      hideOverlay()
    } catch (e) {
      if (token !== buildToken) return
      const msg = (e && e.message) || String(e)
      showOverlay(msg, true)
      lv.fail('volume3d: ' + msg)
    }
  }

  // ── live-tunable params (no rebuild) ──
  function applyTuning(state) {
    if (!material) return
    const mode = state.mode === 'mip' ? 'mip' : 'iso'
    material.uniforms.u_renderstyle.value = mode === 'iso' ? 1 : 0
    const thr = state.threshold === undefined ? 0.45 : state.threshold
    material.uniforms.u_renderthreshold.value = Math.max(0.001, Math.min(0.999, thr))
    const lo = material.userData.displayFloor != null ? material.userData.displayFloor
      : material.uniforms.u_clim.value.x
    const b = Math.max(0.1, Math.min(3, state.brightness === undefined ? 1.2 : state.brightness))
    material.uniforms.u_clim.value.set(lo, Math.max(lo + 0.05, 1 / b))
    if (meshGroup) meshGroup.rotation.x = state.flipUp ? Math.PI : 0
  }

  function applyCamera(cam) {
    if (!camera || !controls || !cam) return
    const k = camKey(cam)
    if (k === lastCamKey) return
    if (Array.isArray(cam.position)) camera.position.set(cam.position[0], cam.position[1], cam.position[2])
    if (Array.isArray(cam.target)) controls.target.set(cam.target[0], cam.target[1], cam.target[2])
    if (typeof cam.zoom === 'number' && cam.zoom > 0) { camera.zoom = cam.zoom; camera.updateProjectionMatrix() }
    controls.update()
    lastCamKey = k
  }

  function camKey(cam) {
    if (!cam) return ''
    const p = (cam.position || []).map((v) => Math.round(v)).join(',')
    const t = (cam.target || []).map((v) => Math.round(v)).join(',')
    const z = cam.zoom ? cam.zoom.toFixed(3) : ''
    return `${p}|${t}|${z}`
  }

  // Report the user's camera back out (debounced) so the agent can read /
  // restore the angle and screenshots match what the user sees.
  function scheduleCameraReport() {
    if (camTimer) clearTimeout(camTimer)
    camTimer = setTimeout(() => {
      camTimer = null
      if (!camera || !controls) return
      const cam = {
        position: [camera.position.x, camera.position.y, camera.position.z],
        target: [controls.target.x, controls.target.y, controls.target.z],
        zoom: camera.zoom,
      }
      lastCamKey = camKey(cam) // suppress the echo re-applying our own pose
      lv.setState({ camera: cam })
    }, 250)
  }

  function resize() {
    if (!view || !renderer || !camera) return
    const rect = view.getBoundingClientRect()
    const W = Math.max(1, rect.width); const H = Math.max(1, rect.height)
    renderer.setSize(W, H)
    const aspect = W / H
    const size = camera.top
    camera.left = -size * aspect
    camera.right = size * aspect
    camera.updateProjectionMatrix()
  }

  // ── reflect state into the control bar ──
  function syncUI(state) {
    titleEl.textContent = state.title || 'Volume'
    const mode = state.mode === 'mip' ? 'mip' : 'iso'
    seg.querySelectorAll('button').forEach((b) => {
      b.classList.toggle('on', b.getAttribute('data-mode') === mode)
    })
    thrWrap.style.display = mode === 'iso' ? '' : 'none'
    if (document.activeElement !== thrInput) {
      thrInput.value = String(state.threshold === undefined ? 0.45 : state.threshold)
    }
    if (document.activeElement !== briInput) {
      briInput.value = String(state.brightness === undefined ? 1.2 : state.brightness)
    }
    flipBtn.classList.toggle('on', !!state.flipUp)
  }

  // ── control-bar interactions → single state path (lv.setState) ──
  seg.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-mode]')
    if (btn) lv.setState({ mode: btn.getAttribute('data-mode') })
  })
  thrInput.addEventListener('input', () => lv.setState({ threshold: Number(thrInput.value) }))
  briInput.addEventListener('input', () => lv.setState({ brightness: Number(briInput.value) }))
  flipBtn.addEventListener('click', () => lv.setState({ flipUp: !(applied && applied.flipUp) }))

  // ── the one render path ──
  lv.onState((state) => {
    if (!state || !state.url) return
    const prev = applied
    applied = state
    syncUI(state)
    if (!prev || state.url !== prev.url || String(state.level) !== String(prev.level)) {
      buildVolume(state) // data changed → full rebuild
    } else {
      applyTuning(state)
      if (state.camera && !userDragging) applyCamera(state.camera)
    }
  })

  // ── WebGL snapshot: html2canvas can't read a WebGL canvas; we read ours ──
  lv.onSnapshot(() => {
    if (!renderer || !scene || !camera) return null
    renderer.render(scene, camera)
    return renderer.domElement.toDataURL('image/png')
  })

  // app-host calls lv.ready() once setup() resolves.
}


// ── file-open shim (generated by convert-viewers.py) ─────────────────────────
// The desktop opens files with generic state {url, path, name, type}; the
// viewer's own contract is documented at the top of this file. File-shaped
// state (a `path`, none of the viewer's keys) is mapped before the stock
// adapter sees it; everything else — agent states, demos — passes through.
const __FILE_KEYS = ["mode", "level", "threshold"]
// What the desktop itself puts in a file-open state; everything else in it
// came from the caller and is theirs to keep.
const __FILE_STATE_KEYS = ['url', 'path', 'name', 'type']
async function __fromFile(state) {
  return { url: state.url, title: state.name }
}
export async function setup(lv, root) {
  const __cbs = []
  let __lastFile = null
  let __cur = null
  const wrapped = Object.create(lv)
  // Adapters read lv.state on their own re-renders (Vitessce does on window
  // resize). The SDK's store still holds the RAW state — file-shaped, or
  // stale after menu-driven changes that bypass it — so point the accessor
  // at what the adapter actually saw last.
  Object.defineProperty(wrapped, 'state', { get: () => __cur })
  wrapped.onState = (cb) => {
    __cbs.push(cb)
    lv.onState((state, info) => {
      // A file open is a file open. This used to also require that the state
      // carried NONE of the viewer's own keys — but those keys are exactly
      // what this app declares as `sync` state, so the first time anyone
      // toggled a mode or picked a colour the window could never run its
      // backend `prepare` again. It then fed the raw file envelope to the
      // viewer: spatial3d fetched `_spatial.json` from inside an .h5ad (404),
      // Vitessce reported "Missing version" on an object that was never a
      // config. The mapping is also what re-mints served URLs, so skipping it
      // left those windows pointing at a dead tunnel after every restart.
      const fileShaped = !!(state && state.path && state.url)
      if (!fileShaped) { __cur = state; cb(state, info); return }
      __lastFile = state
      Promise.resolve(__fromFile(state, lv)).then((mapped) => {
        // Anything the caller asked for beyond the file itself — a layout, a
        // colour scheme, desktop_open(path=…, state={…}) — must survive the
        // mapping. Dropping it silently is why "open it radial" came out
        // linear and sent an agent hunting for a bug that was ours.
        // __FILE_KEYS belong to the FILE: they are what the mapping produces,
        // and they carry served URLs only the current pod can mint. Anything
        // else in the state — a layout, a camera, a colour scheme — is the
        // caller's or the session's, and rides on top.
        const extra = {}
        for (const k of Object.keys(state)) {
          if (!__FILE_STATE_KEYS.includes(k) && !__FILE_KEYS.includes(k)) extra[k] = state[k]
        }
        __emitToApp(Object.assign({}, mapped, extra), info)
      }).catch((e) =>
        lv.fail('Could not open ' + (state.name || state.path) + ': ' + ((e && e.message) || e)))
    })
  }
  // Every shim delivery goes through here — and through the SDK's own store.
  // Adapters build their next state from lv.state (mode toggles, sliders); a
  // delivery that bypassed the store left it holding the bare init `{}`, and
  // the first toolbar click re-rendered from nothing ("Provide state.url").
  const __emitToApp = (state, info) => {
    __cur = state
    if (typeof lv.setState === 'function') lv.setState(state)
    for (const cb of __cbs) cb(state, info || { reason: 'set' })
  }
  // Menu actions patch the CURRENT viewer state — the adapter re-renders the
  // way it would for any set.
  const __patch = (p) => {
    if (!__cur) throw new Error('nothing is loaded yet')
    __emitToApp(Object.assign({}, __cur, p))
  }
  void __patch
  // ── dataset catalog menu ──────────────────────────────────────────────
  // The backend curates the entries (Virtual Embryo bucket + a synthetic
  // fallback); the Data menu lists them with the loaded one ticked.
  let __curData = null
  let __datasets = []
  function __pushMenus() {
    if (!lv.window || typeof lv.window.setMenus !== 'function') return
    const items = __datasets.map((d) => ({
      label: d.label, action: 'loadDataset', args: { id: d.id },
      checked: __curData === d.id,
    }))
    if (items.length) items.push({ separator: true })
    items.push({
      label: 'Synthetic example', action: 'loadDataset', args: { id: 'synthetic' },
      checked: __curData === 'synthetic',
    })
    lv.window.setMenus([{ label: 'Data', items }])
  }
  const __applyData = (config, id) => {
    __curData = id || null
    __emitToApp(config)
    __pushMenus()
  }
  const __load = async (id) => {
    const r = await lv.call(id ? 'load_dataset' : 'example', id ? { id } : {}, { timeoutMs: 600000 })
    if (!r || !r.config) throw new Error((r && r.error) || 'no data returned')
    __applyData(r.config, r.id)
    return 'loaded'
  }
  if (typeof lv.call === 'function') {
    lv.call('datasets', {}, { timeoutMs: 120000 }).then((r) => {
      if (r && r.datasets) { __datasets = r.datasets; __pushMenus() }
    }).catch(() => { /* older pod — the static menu still offers the example */ })
  }
  let __autoRan = false
  const __base = wrapped.onState
  wrapped.onState = (cb) => __base((state, info) => {
    if (!__autoRan && state && !state.url && !state.path && typeof lv.call === 'function') {
      __autoRan = true
      __load(null).catch((e) => lv.fail('example: ' + ((e && e.message) || e)))
      return
    }
    cb(state, info)
  })
  if (typeof lv.defineAction === 'function') {
    lv.defineAction('loadExample', () => __load(null))
    lv.defineAction('loadDataset', (a) => __load(a && a.id))
  }
  return __viewerSetup(wrapped, root)
}
