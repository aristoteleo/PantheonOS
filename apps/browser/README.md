# Browser

Browse websites in a shared Chromium session streamed from the workspace to the desktop.

## Using this App

Open **Browser**, enter a URL, and use the address bar and navigation controls as in a regular browser. The browser runs on the workspace machine; the person and agent can inspect and interact with the same page.

Agents use Desktop’s `browser_pages`, `browser_read`, navigation and interaction tools. Address the returned page ID when several tabs are open. Window actions such as `navigate` and `reload` operate on that specific Browser window.

The workspace needs its Chromium/display streaming services and access to the requested website. Browser session state belongs to the workspace, not the local computer’s browser profile.

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
