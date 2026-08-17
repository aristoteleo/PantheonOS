---
id: desktop
name: Atrium Desktop — driving apps
description: |
  Drive the user's Atrium desktop: open files in installed viewer apps
  (Viv bioimages, Vitessce/Spatial 3D omics, Mol* structures, IGV/Gosling
  genomics, Volume 3D, Cytoscape, MSA, PhyloTree, RDKit), read and steer
  ANY window — including ones the user opened — and call app backends.
tags: [desktop, apps, visualization, interactive, atrium]
---

# The Atrium Desktop

The user works on a desktop of windows. Apps are installed packages; each
claims file types, exposes actions, may run its own Python backend, and
ships a skill documenting its **state contract**. You drive all of it with
five tools — the SAME windows the user sees and clicks.

## The tools

```python
desktop_apps()
# → what is INSTALLED: app_id, name, description, opens, actions, backend,
#   skill path. Use it to name an app explicitly or to see what opens a
#   given file type — never guess an app_id.

desktop_windows()
# → every open window: window_id, app_id, title, path, actions, controllable

desktop_open(path="/abs/path/to/file")          # like a double-click:
# routes by extension, runs the app's own open pipeline (conversion,
# backend prepare). Returns window_id. NEVER serve_local_data a file
# just to view it — desktop_open does everything.

desktop_open(app="viv", state={...})            # open on a state instead;
# each app's skill documents its state shape

desktop_read(window_id)                          # current state, skill-shaped
desktop_update(window_id, patch)                 # deep-merge a state patch
desktop_set(window_id, state)                    # REPLACE the state
desktop_call(window_id, action, args={})         # run a named action
desktop_open(path=..., window_id=...)            # show another file in it
desktop_call(window_id, "$close")                # close the window
desktop_screenshot(window_id)                    # see what it shows

app_call(app_id, method, args={})                # an app's backend method,
# in the app's own process. app_registry() lists live method signatures.
```

Windows the **user** opened are first-class: find them with
`desktop_windows()`, then read/update/call exactly as if you opened them.

## The Browser (a shared, real Chromium)

`browser_open(url)` starts a real Chromium page in the sandbox and shows it
to the user as a Browser window. The page is SHARED: the user sees it live
and can click, type, and log in; you drive the same page with
`browser_goto` / `browser_click` / `browser_type` / `browser_read` /
`browser_screenshot`. When a site needs credentials, open it, ask the user
to sign in in the Browser window, then continue on the now-authenticated
page. The profile (cookies, sessions) persists in the sandbox. Use
`browser_read` (text) or `browser_screenshot` + observe_image (pixels) as
your eyes; prefer leaving pages open for the user over closing them.

## Fix the window you have

Windows are long-lived and they are the USER's. When a view is wrong —
the layout, the channels, the config, even the file — correct it in
place: `desktop_update` to patch, `desktop_set` to replace the whole
state, `desktop_call` for an action, `desktop_open(path=..., window_id=...)`
to show a different file in that window. Open a NEW window only for a
genuinely new thing. Reopening the same file just focuses the window that
already has it (`reused: true`), so a retry cannot litter the desktop.

If a screenshot or an action fails, that is not a reason to open another
window — read the error, fix the state, screenshot again.

## Choosing the app

`desktop_open(path=...)` picks the app that claims the extension — the
same routing a double-click uses. **Name it explicitly** when you want a
particular viewer (the file has more than one candidate, or the user asked
for one): `desktop_open(app="spatial3d", path="cells.h5ad")`. `desktop_apps()`
gives you the exact ids.

## File routing (what opens what)

| Extensions | App |
|---|---|
| .ome.tif/.ome.tiff/.ome.zarr/.zarr/.tif/.tiff | viv (Volume 3D also claims .zarr) |
| .h5ad | vitessce (Spatial 3D as alternative) |
| .pdb/.cif/.mmcif | molstar |
| .nwk/.newick/.tree | phylotree |
| .fasta/.fa/.aln | msa |
| .sdf/.mol/.smi | rdkit |
| .cyjs | cytoscape |

## Each app's contract

Every installed app ships its skill in the workspace:
`.pantheon/apps/<app_id>/skill/SKILL.md` — read it (read_file) before
driving an app with non-trivial state. It documents the state fields,
actions, backend methods, and worked examples. `viv` is the reference
example of the format.

## Example — steer a window the user opened

```python
wins = desktop_windows()["result"]["windows"]
tree = next(w for w in wins if w["app_id"] == "phylotree")
desktop_update(tree["window_id"], {"layout": "radial"})
```

## Example — open and tune a bioimage

```python
w = desktop_open(path="/workspace/scan.tif")["result"]["window_id"]  # viv converts + opens
state = desktop_read(w)["result"]["state"]         # see the auto-filled channels
desktop_update(w, {"channels": [{**state["channels"][0], "color": [255, 0, 0]}]})
```

# Building your own app

When no installed app fits, BUILD one. There are two paths — a quick
bespoke window, and a real installed package.

## A. Bespoke window (fast, no install)

Pass a frontend module SOURCE to `desktop_open(module=…)`. It opens with no
manifest and no install, and is drivable with the same
desktop_read/update/set/call. The module is one function:

```python
desktop_open(module='''
export function setup(app, root) {
  // render only in onState; mutate only through setState — ONE state path.
  app.onState((s) => {
    root.innerHTML = `<div style="font:20px sans-serif;padding:24px">
      count: ${s.n ?? 0}
      <button id="inc">+1</button></div>`
    root.querySelector("#inc").onclick = () => app.setState({ n: (s.n ?? 0) + 1 })
  })
  // an action the AGENT (or a menu) can call — same handler as any UI click
  app.defineAction("bump", () => { const n = (app.state?.n ?? 0) + 1; app.setState({ n }); return n })
  app.ready()
}
''', state={"n": 5}, title="Counter")
# → window_id; then desktop_call(window_id, "bump") or desktop_read/set it.
```

The bridge `app` gives: `onState(cb)` / `setState(patch)` / `emitState(full)`
/ `state`, `defineAction(name, fn)`, `onSnapshot(fn)`, `ready()` / `fail(msg)`,
`fs.read/write/ls(path)` (workspace files), `window.setTitle/close`. It runs
in a sandboxed iframe — bundle any framework you like, but keep the module
self-contained. (This replaces the retired `open_live_view`; do NOT use
`live_view_*` tools.)

## B. Installed package (reusable, claims file types, may have a backend)

Write a package under `.pantheon/apps/<id>/` with ordinary file tools — a
file write IS the install (workspace scope), discovered on the next
`desktop_open`:

```
.pantheon/apps/<id>/
  atrium.json          # manifest (below)
  frontend/index.js    # export function setup(app, root)  — same contract as A
  backend/__init__.py  # optional: def register(ctx): @ctx.method async def …
  skill/SKILL.md       # optional: how you (the agent) drive it later
```

Minimal `atrium.json`:

```json
{
  "id": "my-app",
  "name": "My App",
  "version": "0.1.0",
  "atriumApi": 1,
  "surface": "dom",
  "entry": { "frontend": "frontend/index.js" },
  "launcher": true,
  "icon": { "path": "assets/icon.svg", "tint": "#5b6ee0" },
  "opens": [".myext"],
  "actions": [{ "name": "bump", "description": "increment", "params": {} }]
}
```

Then `desktop_open(app="my-app")` (a just-written app is re-scanned on the
open that misses, so no reconnect needed). A backend method is reached from
the frontend with `app.call("methodName", args)` and from you with
`app_call(app_id="my-app", method="methodName", args={…})`. Prefer path A
for a one-off; path B when the user will reuse it or it needs a backend.
