# Browser

Browse websites in a shared Chromium session streamed from a compatible Fleet node to the desktop.

## Using this App

Open **Browser**, enter a URL, and use the address bar and navigation controls as in a regular browser. Right-click Browser and choose **Open on node…** to choose a backend for this window. **Fleet → App instances → Default backends** sets the default for new windows. The window title and Fleet instance show its node. The browser runs on that selected node; the person and agent can inspect and interact with the same page.

Agents use Desktop’s `browser_pages`, `browser_read`, navigation and interaction tools. Address the returned page ID when several tabs are open. Window actions such as `navigate` and `reload` operate on that specific Browser window.

The selected node needs Linux, Xpra, Xvfb, x11-utils and Python 3.10+. Fleet prepares Chromium and the Python dependencies on installation. macOS and Windows do not yet have a native stream capture provider. Browser profiles are private to the app instance and persist on its node. Choosing a different node does not copy logins or move existing windows.

A window remains bound to its original node and instance across reconnects. Changing the default affects new windows and unbound Agent launches. Closing the last window releases its backend usage lease; Fleet stops an idle instance after the grace period unless Keep running is enabled. Native apps with pending save dialogs refuse shutdown until the dialog is resolved.

Agents may pass `node_id` to `browser_open` or use `app_call` with `window_id`/`binding`. `browser_pages` reports each managed page’s backend. Browser and native input tools route to the same instance the user sees, including native dialogs. An unavailable bound node is reported as unavailable; mutations are never silently rerouted.

## Window actions

Call these through `desktop_call` on this App’s existing window. The runtime’s action schema supplies parameters.

- `navigate`: Navigate this browser window.
- `back`: Go back one page.
- `forward`: Go forward one page.
- `reload`: Reload the current page.
- `newPage`: Show a fresh page in this window.
- `close`: Close this browser window.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

The frontend is supplied by the desktop UI bundle; this directory declares its App integration.
