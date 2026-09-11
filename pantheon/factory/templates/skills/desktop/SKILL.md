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
the desktop tools — the SAME windows the user sees and clicks.

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
`browser_read` (text and interactive elements) or `browser_screenshot` +
`observe_images` (pixels) as
your eyes; prefer leaving pages open for the user over closing them.

After reading a page, use the exact `elements[].selector` for the intended
button or input with `browser_click` / `browser_type`. A text selector may
hit an explanation that mentions "Submit" instead of the Submit button;
do not invent an input's `name` from its label. Re-read after navigation or
DOM changes. The element list covers the main document; use screenshot
observation and coordinate actions for unlisted canvas or embedded content.

When the user names an existing window (for example `#app:win-76`), find
that exact `window_id` with `desktop_windows()`. Pass it to
`browser_open(url, window_id=...)` to navigate its current tab, or as
`page_id` to the other `browser_*` tools. A returned `page_id` identifies
one tab; the desktop `window_id` follows the current tab in that same
native window. If that window is missing or closed, report the failure;
do not silently open a replacement. After acting, read the URL/content
and take `desktop_screenshot` of the requested window before claiming the
result is displayed there. Tool success alone does not establish that.

For browser chrome, native menus and dialogs, use `desktop_screenshot`
and `desktop_act` with native-window pixel coordinates. Browser DOM tools
and `browser_screenshot` use page coordinates instead.

## Native apps, including QuPath

QuPath uses these same `desktop_*` tools. Read the `skill` path returned
by `desktop_windows()` or `desktop_apps()` for its action contract.
`desktop_read` reads the live GUI, `desktop_call` runs declared actions
such as `run_script`, and `desktop_act` operates native controls. Use
`native_windows` from a read/screenshot to target an owned dialog by its
child `window_id`. Check pending/failed states and verify the actual
result before reporting completion; changing launch state does not
modify the open image or its unsaved annotations.

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

Read the app's returned `skill` path (read_file) before
driving an app with non-trivial state. It documents the state fields,
actions, backend methods, and worked examples. Workspace-installed apps
typically use `.pantheon/apps/<app_id>/skill/SKILL.md`; bundled apps can
use an installation path. `viv` is the reference
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
self-contained.

## B. Installed package (reusable, claims file types, may have a backend)

Write a package under `.pantheon/apps/<id>/` with ordinary file tools — a
file write IS the install (workspace scope), discovered on the next
`desktop_open`:

```
.pantheon/apps/<id>/
  app.json             # manifest (below)
  frontend/index.js    # export function setup(app, root)  — same contract as A
  backend/__init__.py  # optional: def register(ctx): @ctx.method async def …
  skill/SKILL.md       # optional: how you (the agent) drive it later
```

Minimal `app.json` (the current manifest protocol; older `atrium.json`
packages still load, but never write new ones):

```json
{
  "id": "my-app",
  "name": "My App",
  "version": "0.1.0",
  "apiVersion": 2,
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

## C. Develop and version an App on its Desktop node

For reusable App development prefer the agent-visible Store tools below. The
Agent worker and Desktop may be different nodes: do not assume an App path
returned by Desktop exists in your shell. These tools edit and run tests on the
node that owns the repository.

- `desktop_store_apps()` → select `app_id`, `repository_id` and `scope`.
- `desktop_app_develop(action="create", app_id="my-app")` → private Git App.
- `desktop_store_manage(action="fork", app_id=..., scope=..., repository_id=...,
  name="Experiment")` → private fork preserving history. Unless the user selected a default explicitly,
  new launches follow the newest personal fork’s latest committed HEAD.
- `desktop_app_develop` supports `files`, `read(path=...)`, `write(files={...})`,
  `diff`, `branch`, `switch`, `merge`, `commit(message=...)` and
  `test(command=["python", "-m", "pytest"])`. Read before editing and pass the
  last `expected_commit` to detect concurrent commits. Paths are relative to
  the repository; test commands execute there with a 120-second limit.
- `desktop_store_manage(action="tag", ..., version="1.1.0")` commits changes and
  creates a local version tag after compatibility validation. `desktop_store_git`
  shows actual branches, tags and merge parents.
- `desktop_store_manage(action="resolve", ..., version="v1.1.0")` → exact revision.
  Pass it to `desktop_open(app=..., revision=...)` or `app_call(..., revision=...)`.
  Test this version without changing the default. Backend management supports
  `start`, `instances` and `stop`; `default` affects only future launches.
  Use `default(..., version="latest")` to follow this repository’s HEAD, or a
  tag/full SHA to pin a version. Choosing Official explicitly keeps it the
  default even when forks exist. Dirty edits never enter a launch snapshot.
- `desktop_app_store(action="search", query=...)` and `inspect(repository_id=...)`
  find public repositories. `fork(repository_id=..., version="1.0.0")` creates
  your private copy. `fetch` updates upstream refs without merging or overwriting
  local branches/tags.

A local commit/tag is private. Publish only when the user asks to release
publicly: `desktop_store_manage(action="prepare", ...)` returns the exact tag
and SHA for review, then `desktop_app_store(action="publish", ..., name=...,
expected_commit=...)` publishes that release to a public, clonable Store Git
repository. Its reachable commit history becomes public too. Other private
branches are excluded. Reuse the same repository UUID for later releases;
never force-move a published tag. A public install or official App must be
forked before editing. Respect explicit launch preferences; ordinary fork commits advance the automatic
latest-commit default without changing existing instances.

Independent DOM Apps and file-based Python backends support pinned versions.
If Store reports a Desktop/Fleet-managed runtime restriction, use that runtime's
upgrade route rather than claiming a manifest fork changed the live runtime.

### Community and upstream contributions

`desktop_app_store(action="community", app_id=...)` lists public repositories
for an App, including its administrator-designated Official upstream if one
exists. Others can install or privately fork a chosen released version.

To contribute when requested: publish a release of the private fork first,
inspect both source and upstream, then `submit(repository_id=source_id,
version=source_version, target_repository_id=upstream_id,
expected_commit=source_sha, expected_base=upstream_sha, title=...,
description=changes_and_tests)`. The review is pinned to both commits.
`contributions(inbox="mine"|"review"|"all", app_id=...)` and
`inspect_contribution(request_id=...)` show state and diffs. Unrelated legacy
repositories must fork the public upstream and apply their edits there first;
do not merge unrelated histories or force-move release tags.

Maintainer workflow: `prepare_merge(request_id=..., version=...)`, inspect the
final candidate diff, and `checkout_review(request_id=...,
expected_commit=candidate_sha)` to obtain a verified private Git checkout on
this Desktop node. Read and test there using that node's file/terminal tools.
No App code is executed by Store validation, and this does not change installed
Apps or defaults. `review(request_id=..., decision="approve"|"reject",
expected_commit=candidate_sha, description=review_and_test_notes)` records the
review. Only with authorization to release upstream, `merge(request_id=...,
expected_commit=candidate_sha)` publishes that exact merge as a new immutable
upstream release. A changed upstream or candidate requires a new review.
`close_contribution(request_id=...)` withdraws an unmerged request.

Official Store releases do not automatically rebuild the PantheonOS bundled
Apps; shipping bundled runtime changes still follows the OS deployment route.
