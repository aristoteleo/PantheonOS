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
