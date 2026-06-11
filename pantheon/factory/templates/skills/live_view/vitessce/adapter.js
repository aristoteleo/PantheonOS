/**
 * Vitessce LiveView adapter — a viewer plugin.
 *
 * This is NOT built into the app. It is an ordinary LiveView component
 * module (the same kind an agent generates): it exports `setup(lv, root)`
 * and is loaded by the generic host `app-host.html`. The only built-in
 * piece of LiveView is the SDK runtime (live-view-sdk.js + app-host.html);
 * every viewer — Vitessce included — is a pluggable module that lives next
 * to its skill file.
 *
 * What it does: (1) load Vitessce, (2) render <Vitessce> with whatever
 * state the SDK hands it, (3) emit Vitessce's state back out. For Vitessce
 * the LiveView "state" IS the Vitessce view config; an agent patch
 * deep-merges into it (e.g. {coordinationSpace:{spatialZoom:{A:5}}}).
 *
 * React resolves via app-host's import map; Vitessce is imported by full
 * URL from unpkg (its genuine browser ESM build). No build step.
 */
import React from 'react'
import { createRoot } from 'react-dom/client'
import { Vitessce } from 'https://unpkg.com/vitessce@latest'

export function setup(lv, root) {
  let reactRoot = null
  // While we apply agent-driven state, Vitessce's onConfigChange fires for
  // our own write — suppress it so it isn't echoed back as a user edit.
  let applyingRemote = false

  let errorCheckTimer = null
  // The uid is what tells Vitessce "this is a new config" (→ re-initialise &
  // re-fetch). We bump it only on a real state change, NEVER on a resize.
  let currentUid = null

  // Vitessce reports an invalid config NOT by throwing or logging — it just
  // renders a <Warning> ("Config validation failed on second pass." etc.).
  // Nothing else would notice, so status would wrongly stay "ready". Detect
  // that rendered warning and surface it as a hard failure to the agent.
  function checkForVitessceError() {
    const text = (root.textContent || '').trim()
    if (/Config (validation|initialization) failed/i.test(text)) {
      lv.fail('Vitessce rejected the config — ' + text.slice(0, 600))
    }
  }

  function renderVitessce(config, bumpUid) {
    if (!config) return
    if (!reactRoot) reactRoot = createRoot(root)
    // Fresh uid only on a real config change (patch/set) so Vitessce detects it.
    // A resize REUSES the uid — a new uid would make Vitessce re-initialise and
    // re-fetch all data, which is the "reloads on window resize" bug.
    if (bumpUid || !currentUid) currentUid = `lv-${Date.now()}`
    const cfg = { ...config, uid: currentUid }
    reactRoot.render(
      React.createElement(Vitessce, {
        config: cfg,
        theme: 'dark',
        height: window.innerHeight,
        onConfigChange: (newConfig) => {
          if (applyingRemote) return      // our own write — don't echo
          lv.emitState(newConfig)         // user interaction → report out
        },
      }),
    )
    // Config validation runs synchronously on render; check shortly after.
    if (errorCheckTimer) clearTimeout(errorCheckTimer)
    errorCheckTimer = setTimeout(checkForVitessceError, 2500)
  }

  // The LiveView "state" is the Vitessce view config. init/patch/set arrive
  // here as the full merged config. `reason === 'emit'` is our own outgoing
  // change — Vitessce already rendered it, so skip the re-render.
  lv.onState((config, info) => {
    if (info && info.reason === 'emit') return
    applyingRemote = true
    try {
      renderVitessce(config, true)   // real state change → new uid
    } finally {
      // release after the render settles (Vitessce's onConfigChange for
      // our write fires on the next tick)
      setTimeout(() => { applyingRemote = false }, 0)
    }
  })

  // Re-render on resize so Vitessce re-lays-out for the new height — debounced,
  // and with the SAME uid so it does NOT re-fetch data.
  let resizeTimer = null
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => { if (lv.state) renderVitessce(lv.state, false) }, 200)
  })

  // app-host calls lv.ready() once setup() resolves.
}
