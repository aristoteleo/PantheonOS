---
id: live_view_app
name: Generate an Agent-Controllable LiveView App
description: |
  Write a custom interactive visualization component that the agent can
  open, drive, and observe — using the LiveView SDK. Covers the SDK API,
  the component contract, how to serve and open it, and how the agent
  drives it via live_view_update / live_view_call.
tags: [live-view, sdk, custom-component, visualization, interactive, generate-app]
---

# Generating an Agent-Controllable LiveView App

When no existing viewer fits, write your own interactive component. With the
**LiveView SDK** the component is controllable-by-construction: the agent
drives it and reads it back, with no extra wiring.

Use this for bespoke dashboards, custom plots, tailored data views. For
heavy domain viewers (spatial omics) prefer the Vitessce skill instead.

## The golden rule: ONE state path

Every state change — a user click, an agent update, an action handler —
goes through **`lv.setState()`** (or `lv.emitState()`). That call does
three things at once:

1. updates `lv.state`,
2. **re-fires `onState` locally** → your UI re-renders immediately,
3. reports the new state outward so the agent can read it.

So you render **only inside `onState`**, and you change state **only via
`lv.setState`**. Never keep your own copy of state and mutate it by hand.

> ⚠️ **The #1 bug:** a click handler that only "reports" the change but
> doesn't go through `setState` — the click does nothing visible. There is
> no separate "report" step. `lv.setState(...)` *is* both the update and
> the report. One call, one path, for clicks and for the agent alike.

## The component contract

A component is a **JS ES module** that exports `setup(lv, root)`:

```js
// my-app.js
export function setup(lv, root) {
  // `root` : the container element to render into
  // `lv`   : the LiveView SDK instance

  // Render is a pure function of state. It fires on the initial state,
  // on every agent update, AND after every lv.setState() you call.
  lv.onState((state) => {
    root.innerHTML = `<button id="b">clicked ${state.n || 0}</button>`
    root.querySelector('#b').onclick =
      () => lv.setState({ n: (lv.state.n || 0) + 1 })  // → re-renders
  })

  // A command the agent can invoke via live_view_call. To make the change
  // visible, the handler ALSO goes through setState.
  lv.defineAction('reset', () => {
    lv.setState({ n: 0 })
    return { ok: true }
  })
}
```

`setup` may be async. The host calls `lv.ready()` once it resolves.

## Three component styles

All three get the same `lv` and the same control loop. Pick by need.

### Vanilla — a `.js` module exporting `setup(lv, root)`

Render into `root` yourself (innerHTML, DOM, SVG, canvas, any library).
Smallest footprint; good for simple or library-driven views. See the
component-contract example above.

### Vue — recommended when you want a framework

Pantheon's UI is Vue, so Vue keeps things consistent. A Vue component is
just a **`setup(lv, root)` module** that mounts a Vue app. `import 'vue'`
resolves to a build with the template compiler, so **template strings work
with no build step** (no transpilation — nothing to go wrong):

```js
// my-app.js
import { createApp, ref } from 'vue'

export function setup(lv, root) {
  createApp({
    setup() {
      // Mirror SDK state into a ref purely so Vue re-renders. onState fires
      // for agent updates AND your own lv.setState() calls — one path.
      const state = ref(lv.state || {})
      lv.onState((s) => { state.value = s || {} })
      // Interactions go through lv.setState — never mutate `state` directly.
      const inc = () => lv.setState({ n: (lv.state.n || 0) + 1 })
      return { state, inc }
    },
    template: `<button @click="inc">clicked {{ state.n || 0 }}</button>`,
  }).mount(root)

  lv.defineAction('reset', () => lv.setState({ n: 0 }))
}
```

> ⚠️ **A Vue `template` string CANNOT contain `<style>` or `<script>`.**
> Vue's runtime compiler silently drops them — you get a console warning
> (`Tags with side effect ... are ignored`) and an **unstyled, broken**
> component. To add CSS, inject a `<style>` element into the document head
> inside `setup()`:
> ```js
> export function setup(lv, root) {
>   const css = document.createElement('style')
>   css.textContent = `.my-app { display:grid; gap:12px } /* … */`
>   document.head.appendChild(css)
>   createApp({ /* … */ }).mount(root)
> }
> ```
> (Vanilla components *may* put `<style>` directly in `root.innerHTML` — it
> works there; only Vue's `template` strips it. For a long stylesheet,
> head-injection is cleanest either way.)

### React / JSX — a `.jsx` (or `.tsx`) module, default-exported component

The host transpiles it in-browser (Sucrase) and mounts it, passing the SDK
instance as the `lv` prop:

```jsx
// my-app.jsx  — JSX works directly, no need to import React
import { useState, useEffect } from 'react'

export default function App({ lv }) {
  // Mirror SDK state into React state purely so React re-renders. onState
  // fires for agent updates AND your own lv.setState() — one path.
  const [state, setState] = useState(lv.state)
  useEffect(() => lv.onState(setState), [])

  if (!state) return <div>Loading…</div>
  return (
    <button onClick={() => lv.setState({ n: (state.n || 0) + 1 })}>
      clicked {state.n || 0}
    </button>
  )
}
```

In Vue and React alike, interactions call **`lv.setState`**, never the
framework's own setter. `lv.setState` re-fires `onState`, which updates the
framework's reactive state for you — that is the single path.

## SDK API (live-view-sdk.js)

| Call | Purpose |
|------|---------|
| `lv.onState((state, info) => …)` | Render callback. Fires on init, on every agent update, and after every `setState`/`emitState`. Always renders the full merged state. `info.reason` ∈ `init`/`patch`/`set`/`emit`. |
| `lv.setState(patch)` | **Primary state-change call.** Deep-merges `patch` into state, re-renders locally, reports outward. |
| `lv.emitState(fullState)` | Like `setState` but replaces the whole state instead of merging. |
| `lv.defineAction(name, fn)` | Expose a command. `fn(args)` (sync/async); its return value goes back to the agent. Call `lv.setState` inside it to update the UI. |
| `lv.state` | The current merged state. |
| `lv.fail(msg)` | Report an unrecoverable error. |

The module is an ES module — it may `import` libraries from a CDN, e.g.
`import Plotly from 'https://esm.sh/plotly.js-dist-min'`.

## Workflow

```
1. Write the component module to the workspace, e.g. workspace/my-app.js
2. serve_local_data("my-app.js")           -> { url }   (CORS-served URL)
3. open_live_view(
     view_type="custom",
     title="My App",
     state={ ...initial app state },
     module_url=<url from step 2>,
   )                                        -> view_id
```

`module_url` is REQUIRED for `view_type="custom"` — it is the served URL of
your component module. `state` is purely your component's own initial
state, delivered to `lv.onState`.

## Driving and observing it

- `live_view_update(view_id, patch)` — deep-merges `patch` into the state;
  the component's `onState` fires with the merged result.
- `live_view_call(view_id, action, args)` — invokes a `defineAction`
  handler; returns its result to you.
- `live_view_get_state(view_id)` — reads the current `state`, `status`, and
  `diagnostics`, **including changes the user made**.
- `live_view_screenshot(view_id)` — renders the component to an image file;
  view it with `observe_images`.

## Verify it actually rendered — do NOT trust `status: ready`

`status: "ready"` only means the module loaded without throwing. A
component can be `ready` and still be **visibly broken** (bad layout,
empty chart, stripped styles) — that does not throw. After opening a view,
and after any non-trivial change, verify properly:

1. **`live_view_get_state(view_id)` → check `diagnostics`.** A non-empty
   `diagnostics` list (console errors / Vue / React warnings captured from
   the component) means something is wrong even when `status` is `ready`.
   Fix the component and reopen.
2. **`live_view_screenshot(view_id)` → `observe_images` the result.**
   Actually *look* at the rendered component. The screenshot is an
   html2canvas DOM render: HTML/SVG is captured faithfully, but **WebGL /
   `<canvas>` content is NOT** (it shows blank) — so for canvas/WebGL-based
   views, rely on diagnostics and the user instead.

**Do NOT "verify" by reading back a value you just set with
`live_view_update`.** `live_view_get_state` returns the backend's mirror —
echoing your own write proves nothing about whether the component rendered
or responded. Genuine verification is diagnostics + screenshot.

## Complete example — a controllable scatter plot

`scatter-app.js` — renders points from state, click selects one, and
exposes a `highlight` action. Note every change routes through `setState`:

```js
export function setup(lv, root) {
  const W = 400, H = 300

  lv.onState((state) => {
    const points = state.points || []
    const selected = state.selected
    root.innerHTML = `
      <svg width="100%" height="100%" viewBox="0 0 ${W} ${H}"
           style="background:#111418">
        ${points.map((p, i) => `
          <circle cx="${p.x}" cy="${p.y}" r="${i === selected ? 9 : 5}"
            fill="${i === selected ? '#58a6ff' : '#8b949e'}"
            data-i="${i}" style="cursor:pointer" />
        `).join('')}
      </svg>`
    root.querySelectorAll('circle').forEach((el) => {
      // user click → setState → re-renders here AND reports to the agent
      el.onclick = () => lv.setState({ selected: Number(el.dataset.i) })
    })
  })

  // agent can call: live_view_call(view_id, "highlight", {index: 2})
  lv.defineAction('highlight', ({ index }) => {
    lv.setState({ selected: index })   // updates the UI, same path
    return { selected: index }
  })
}
```

Open it:
```
serve_local_data("scatter-app.js")  ->  url
open_live_view(
  view_type="custom", title="Scatter",
  state={ points: [{x:50,y:80}, {x:160,y:200}, {x:300,y:120}], selected: 0 },
  module_url=url,
)
```

Then drive it — `live_view_update(view_id, {selected: 2})` moves the
selection; `live_view_get_state(view_id)` shows which point the user clicked.

## Tips

- Render only in `onState`; change state only via `lv.setState`. If a click
  "does nothing", you almost certainly mutated state without `setState`.
- Keep `onState` a pure render of the full state — idempotent, no surprises.
- Put everything the agent needs to control in `state`; expose discrete
  commands as actions, and have those actions call `lv.setState`.
- The component runs in a sandboxed iframe; it can fetch CORS-enabled URLs
  (use `serve_local_data` for workspace files) and import CDN libraries.
