# Desktop

## Operate the whole desktop

`desktop_inspect()` reads the current chat's visible Pantheon Desktop: windows,
focus, geometry, loading states, spaces, Launcher, overview and visible controls.
`desktop_control(actions=[...])` operates these through the same shell stores as
the user's controls. For example, focus a window with
`{"type":"focus","window_id":"win-251"}` or open Launcher with
`{"type":"launcher","open":true}`. Visible DOM controls use the returned
`inspection_id` and `target_id`; stale or covered controls fail rather than
clicking a different element. Failed batches report how many actions completed.

`desktop_screenshot()` captures the entire desktop; pass `window_id` to crop one
window. Browser sharing is still required, and only the current Pantheon tab is
accepted. Use `path="spatial3d-ui/before.png"` to save in the current project.
The result always identifies the actual file with `path`, `node_id` and
`image_ref`. Pass **that returned `image_ref`** to `observe_images`; do not infer
filenames or treat a requested destination as an existing file before success.

Global shell controls complement app APIs: use `desktop_read/update/call` for
embedded app state and actions, and `desktop_act` with a native-window export
for native application input. DOM control dispatch is not operating-system
input, and cannot bypass browser permissions or access an iframe's document.

Host the desktop’s windows, installed Apps, shared browser, App backends and local data endpoints.

## Using this App

Desktop is a system service used by the visible desktop shell. It resolves installed Apps, creates and restores windows, routes file opens and window actions, and maintains the Store’s local repositories and version snapshots.

Agents start with `desktop_apps` and `desktop_windows`, then open an App or address an existing window. Read the window state before updating it. Agents manage and develop Apps through the PantheonOS interface (`pantheonos_manage_app`, `pantheonos_develop_app`; docs/agent/pantheonos-app-interface.md), which this backend serves with its `agent_app_*` methods; the `desktop_store_*` and `desktop_app_*` methods serve the UI.

`serve_local_data` exposes files from configured serving roots. A file outside those roots cannot be made readable merely by writing its absolute path into a URL. File and backend operations must preserve the owning node’s identity.

## Agent interface

Available tools: `browser_act`, `browser_click`, `browser_close`, `browser_goto`, `browser_open`, `browser_pages`, `browser_read`, `browser_screenshot`, `browser_scroll`, `browser_type`, `desktop_act`, `desktop_apps`, `desktop_call`, `desktop_open`, `desktop_read`, `desktop_screenshot`, `desktop_set`, `desktop_update`, `desktop_windows`, `manage_endpoints`, `serve_endpoint`, `serve_local_data`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

Backend implementation: [__init__.py](__init__.py).
