# Standalone Apps: one App, full page, without the desktop (2026-11-03)

Status: design. Nothing here is built yet.

The owner opens one of their own Apps (Coding Agents, Agent Canvas, Files, a
Store App, the Pantheon Agent) as a page of its own, signed in to their own
account, with no Atrium desktop around it. "Standalone desktop" already names
`desktop.html` in the UI code; this document says **App page** for the new page.

## Relation to hosting

Hosting (`docs/apps/app-hosting.md`) and the App page are one mechanism for two
audiences. Both open an **App**, not an instance: the frontend comes from the
App's package, and a backend instance is bound only when the App has one.

| | Frontend from | Backend | Credential | Who |
|---|---|---|---|---|
| Desktop window | UI bundle (built-in) / installed package via the node or Workspace | owner's instance by placement (App bus) | owner | owner |
| **App page** | same as the desktop; a frontend-only Store App not installed yet: its Store version, as `frontend` mode serves it | same as the desktop | owner | owner |
| Hosted `frontend` | pinned Store version via the Hub | none | none | visitors |
| Hosted `host` | owner's instance via a visitor grant | owner's instance, `share.methods` only | visitor token | visitors |
| Hosted `install` | — (installs into the visitor's own workspace, then their desktop or App page) | visitor's own | visitor | visitors |

Shared machinery: the app-host iframe and `PackagedApp`/`EmbeddedApp`
(`ui/src/desktop/apps/packaged/`), the App index for names, icons and placement
(`docs/os/desktop-state.md` P2), `startPlacedInstance`/`appLifecycle` to start a
stopped instance, and (below) one host interface with an owner and a visitor
implementation. Coding Agents and Agent Canvas cannot be hosted (they are shell
components that need the owner's App bus and node services), but they work on
an App page, which is the owner with the owner's credential.

## URLs and sign-in

```
/app/<app-id>[?<args>]                     one App, full page (built-in id, or pkg:<id>)
/app/<app-id>?open=<target>[&verb=view]    the same App, opening something (canonical link, below)
/open?target=<target>[&verb=view]          "whatever opens this" — the router picks the App
/                                          Phase 2: the Pantheon Agent (deployment users)
```

`<target>` is a file identity `pantheon-node:///<node>/<path>` (`utils/nodeFiles.ts`,
already how windows name files) or an `https:` URL. App args are the App's own
props, declared per App (Coding Agents: `node`, `project`, `conversation`;
Canvas: `node`, `canvas`; Files: `path`; Agent: `chat`).

Path, not hash, so each App has its own scope (installable per App, below):
nginx `location ^~ /app/ { try_files /app.html =404; }` and `= /open` likewise,
with `desktop.html`'s headers; the same rewrite as a Vite dev plugin; `app.html`
is a fourth `rollupOptions.input` beside `launcher`, `index`, `desktop`, sharing
the desktop's chunks.

Sign-in is `desktop/App.vue`'s gate: `useAuthStore().token` → the App, otherwise
`desktop/views/LoginView.vue` drawn in place, so the URL never changes; OAuth
completes in a popup (`network/oauth.ts`); one Hub session per origin for the
chat page, the desktop and App pages; `onSessionExpired` brings the login back.
No Hub change.

## The App host interface

Today a built-in App reaches the desktop directly. Counted over
`ui/src/desktop/apps/` (non-test): `windows.open` in 13 Apps (agent,
agent-canvas, browser, coding-agents, files, fleet, google-workspace,
llm-playground, model-services, office, settings, store, terminal),
`windows.focus` in 10, `windows.setArgs` 19 calls, `windows.windows`/`byId` in 6,
`windows.close` in 7, `windows.launch` in 3, `setTitle` in 2,
`registerCloseHandler` in 4 (browser, office, stream, xwindow), `showToast` in 2,
`ask` in 5, `promptText` in 2, `chatWithAgent` in 2, `window.open` in 2, and
`setWindowStatus`/`useAppBridge` in 9. Opening a file is re-implemented four
times: `terminal/linkOpener.ts` (`desktopLinkDeps` → `windows.open`, used by
Terminal and Coding Agents' `conversationLinks.ts`), `agent-canvas/runs.ts`
(`openWindow('quick-look' | resolved App | 'files')`), `files/FilesApp.vue`
(`resolveForFile` → `windows.open`), and for packaged Apps
`shell/appNavigation.ts` `navigateToApp` → `windows.launch`.

These become one interface, provided per surface (`provide(appHostKey)`;
`useAppHost()` in Apps; `PackagedApp` maps the iframe bridge onto it):

```ts
interface AppHost {
  kind: 'desktop' | 'page' | 'visitor'
  open(intent: AppIntent): Promise<OpenResult>   // files, URLs, other Apps
  notify(n: { title: string; body?: string; key?: string; action?: () => void }): void
  confirm(req: DialogRequest): Promise<boolean>   // shell/dialog ask
  prompt(req: DialogRequest): Promise<string | null>  // promptText
  setTitle(title: string): void
  setAttention(on: boolean, badge?: number): void   // waiting for approval, unread
  focusSelf(): void
  state: AppStateStore                             // per-App state, below
  view: { args: Record<string, unknown>; setArgs(patch): void }  // this view only
  status(s: 'loading' | 'ready' | 'error', message?: string): void
  onClose(handler: () => void | Promise<void>): () => void
}
type AppIntent =
  | { verb: 'view' | 'edit' | 'open'; file: string; app?: string; context?: { conversation?: string } }
  | { verb: 'open'; url: string; app?: string }
  | { verb: 'open'; app: string; args?: Record<string, unknown> }
```

Implementations: **DesktopHost** (windows store, today's behaviour),
**PageHost** (App page) and **VisitorHost** (hosted links, replacing what
`HostedAppPage.vue` hand-wires today). Mapping:

| Today | DesktopHost | PageHost | VisitorHost |
|---|---|---|---|
| `windows.open(app, {path})`, `linkOpener` / `runs.ts` / `FilesApp` / `navigateToApp` | `open(intent)` → window (as now) | `open(intent)` → routed (below) | `open(intent)` → share-limited (below) |
| `windows.open('fleet' \| 'terminal' \| 'coding-agents')` | window | new tab `/app/<id>?…` | refused |
| `window.open(url)` (`linkOpener` `openUrl`, Settings) | `open({url})` → browser tab | same | same (`noopener`) |
| `chatWithAgent(refs)` (Files, Agent) | `open({app:'agent', args:{references}})` → Agent window | Agent page, new tab | refused |
| `windows.setArgs(own)` for durable state (Coding Agents tabs, Canvas canvases) | `state` | `state` | browser-local state (as `frontend` mode) |
| `windows.setArgs(own)` for view (open tab, viewport) | `view.setArgs` → window args | `view.setArgs` → page-local | page-local |
| `windows.setTitle(own)` | window title | `document.title` + top bar | top bar |
| `windows.focus(own)`, toast actions | raise window | `window.focus()`; hidden tab → browser notification | `window.focus()` |
| `windows.focus/close/byId(other)` (Coding Agents ↔ its Terminal windows) | as now | tabs this page opened (`window.open` handles); else open anew | — |
| `windows.close(own)` | close | "Closed — Open again" | same |
| `showToast` | `notify` → ToastHost | `notify` → ToastHost / Notification | ToastHost |
| `ask`, `promptText` | DialogHost | DialogHost | DialogHost |
| `registerCloseHandler` | as now | `onClose` → `pagehide` | same |
| `setWindowStatus`, `useAppBridge`/`registerWindowBridge` | as now (Agent drives windows) | status in the top bar; not drivable by the Agent's desktop tools (not in the session) | status only |
| `windows.move/resize/minimize/activeSpace`, spaces | desktop-only Apps keep the store | — | — |
| `useWorkspaceStore().ready/workspaceReady` | as now | same store, same connection | — |

Apps whose window *is* their purpose (Browser's xpra windows, `xwindow`,
`stream`, `agent-view`) keep using the windows store and are `desktop-only`;
the interface does not try to cover them.

## Intents

**Declared by the App.** The manifest's `opens` (extensions; already read by
`registry.ts` `appsFor`/`resolveForFile`, with `defaultFor` and the user's
`preferredApp`) stays as shorthand for `view`+`open` of those types. New, optional:

```json
"handles": [
  {"verbs": ["view"], "types": [".pdf"]},
  {"verbs": ["open"], "schemes": ["https"]}
],
"presentation": ["panel", "page"]
```

`presentation`: `panel` — can be embedded inside another App's page (a viewer;
it runs through `EmbeddedApp` as WindowChatPane already embeds Apps beside a
chat); `page` — has its own App page; `desktop-only` — needs the desktop
(`xwindow`, `agent-view`, native-stream Apps until checked). Defaults: built-ins
declare theirs in `registry.ts`; a packaged App with `launcher: false` and only
`opens` (text-viewer, image-viewer, pdf-viewer) defaults to `["panel", "page"]`,
any other packaged App to `["page"]`.

**Routing on an App page** (the App chosen exactly as today: `preferredApp`,
`defaultFor`, packaged before built-in, Quick Look for files outside shared
folders as `linkOpener` does):

1. Target App has `panel` → open in the page's **panel stack**: a right-side
   drawer (resizable, up to 3 stacked, Esc closes the top) holding an
   `EmbeddedApp` (packaged) or a built-in component with a local view id. Each
   panel has **Open in own tab** and **Send to desktop**. Context (the
   conversation, the tab) stays put.
2. Else `page` → new browser tab at `/app/<id>?open=<target>` (Browser,
   Notebook `integrated-notebook`, Office, Files for a folder, another Coding
   Agents).
3. Else `desktop-only` → **Send to your desktop**: `appRpc('desktop-state',
   'intent', {kind: 'open', …})` adds the window to the owner's session; this
   works with no desktop open (the service stores it; the next desktop draws
   it); a notice offers **Open desktop** (`/desktop.html?focus=<win id>`).
4. URLs: as today a URL opens a browser tab; the link menu
   (`linkOpener.menuItems`) also offers "Open in Pantheon Browser" →
   `/app/browser?open=<url>` in a new tab.

**Canonical links.** A link to an App, file or URL is one URL that works
everywhere: `/app/<id>?open=…` or `/open?target=…`. The desktop intercepts these
(same-origin link clicks in Markdown and chat, paste onto the desktop surface,
`desktop.html?open=…` on load, next to the existing `?install=` in
`Desktop.vue`) and opens a window; an App page routes them as above; a
visitor page allows only what the share grants. "Copy link" in the link menu
and the window menu produce this form.

**Visitors** (`VisitorHost`). A visitor may only `view` files under the share's
`share.paths`, read through the visitor grant, in a `panel`, with a viewer the
owner lists in a new `share.viewers` (public frontend-only Store Apps, served
from their Store version as `frontend` mode does). URLs open a plain browser
tab. Never the owner's other Apps, desktop, Agent, or Send to desktop; anything
else answers "Not available in a shared App".

## The App page shell

`src/desktop/standalone/` (entry `main.ts`, root `AppPage.vue`), the parts of
`Desktop.vue` an App needs without the surface:

1. **Connection**: `workspace.connect()` as is (controller, App bus; the
   Workspace joins in the background), `registerCachedAppIndex` and the
   `desktop.apps` event for App ids, icons and placement, desktop-state `get`
   for settings and per-App state. Presence registers a viewport that does not
   render the desktop (`network/presence.ts`).
2. **Hosts**: `DialogHost`, `ToastHost`, `ContextMenu`, `TransferPanel`, drop
   overlay, panel stack; `PageHost` provided.
3. **The App**, full page (`active: true`); packaged ones through `PackagedApp`
   with a `windowHost`, frontend and instance resolved as on the desktop
   (`windowNode.ts`, `startPlacedInstance`), "Waiting for <node>…" while it
   starts.
4. **Top bar** (36 px): icon and title, the App's live menus (`appMenus`),
   node status, Workspace status only for `needsPod` Apps, attention badge,
   **Open on desktop**, account menu (name, budget, recent Apps, sign out).

## Per-App state

Coding Agents (projects, tabs, per-node views) and Agent Canvas (canvases,
`persist.ts`) keep their durable state in window args today, so it belongs to a
window and dies with it. It moves to desktop-state, one key per App:

```
<fid>.owner.app.<app id>    {revision, state}   ≤ 256 KB, CAS on revision
```

Tools `app_state_get(app_id)`, `app_state_put(app_id, state | patch, revision)`,
change event `{type: "desktop.app_state", app_id, revision}`. **Both surfaces use
it**: the desktop window and the App page show the same projects and canvases;
window args keep only the view (open tab, open canvas, viewport), the page keeps
its view in the URL. Migration: when the key is absent, the first reader merges
every window's old args into it with the merge rules `persist.ts` already has
for several viewports, and writes it. Settings gains `pages.recent`
(≤ 20 `{app_id, url, at}`) for the account menu.

So an App page is **not** part of the desktop session: no window record, not
restored by the desktop, not counted against its limits. The same App open on
the desktop and on a page at once is two viewers of one per-App key (CAS +
the change event), plus PTYs and conversations that live on the node and are
followed by both, as two desktop tabs are today.

## Which Apps

| App | Phase | Work |
|---|---|---|
| Coding Agents | 1 | to `useAppHost`: `linkOpener` deps → `open`, terminals → `open({app:'terminal'})`, toasts → `notify`, `ask`/`promptText`; state → per-App key |
| Agent Canvas | 1 | `runs.ts` `openWindow` → `open`; canvases → per-App key; `ask`/`promptText` |
| Text / Image / PDF viewer, Quick Look | 1 | `presentation: panel, page` |
| Pantheon Agent | 2 | below |
| Terminal, Files, Fleet, Model Services, LLM Playground, Store, Settings, Office, Google Workspace, Notebook | 3 | to `useAppHost`; Files/Settings drop desktop-surface parts (desktop-folder icons, wallpaper, Dock) on a page; Store "Open" → `/app/pkg:<id>` |
| Browser | 3 | `page` (streamed Chromium on a Fleet node); its xpra pop-ups stay desktop windows → Send to desktop |
| `xwindow`, `agent-view`, QuPath | — | `desktop-only` |
| Packaged / Store Apps | 1 (mechanism), 3 (checked) | bridge `navigate`/`win` → `AppHost`; `presentation` from the manifest |

## Pantheon Agent at `/` (Phase 2)

Today `/` (index.html: `ChatView` → `ChatLayout`, bound to the ChatRoom from
`/api/chatroom`) sends deployment users to `/desktop.html`
(`network/http/chatroom.ts` `openDesktopInstead()`; both staging hosts serve
`index-*.js` at `/` and `desktop-*.js` at `/desktop.html`). On the desktop the
Agent is the package `pkg:agent`: `AgentEntry.vue` finds the deployment's Agent
(`startupAgent()`) and opens its frontend (`dist-agent-app`: `src/agent/appEntry.ts`
→ `mountAgentFrontend` → `AgentWorkspace.vue`) in the app-host iframe, which
talks to the instance through `host.call` (`AgentAppConnection`, protocol check
`get_agent_app_info`).

**Recommendation: `/` becomes the App page of `pkg:agent`, showing the Agent
App's own frontend, not the legacy ChatLayout.** `openDesktopInstead()` is
replaced by that page on `/` and `/c/:chatId`; AgentEntry's lookup and
starting/stopped/absent states move into `network/agentSetup.ts` for both;
`presetSuccessor` follows restarts and upgrades; the Agent's `setState({chatId})`
updates the URL; `host.apps.open` (`src/agent/navigation.ts`) goes through
`PageHost.open`, so "This host cannot open other Apps yet" goes away; files the
Agent links open in the panel stack. Legacy (non-deployment) users keep
`ChatView`.

Why: ChatLayout binds to the ChatRoom's NATS service, not the Agent App
instance, so it would need a second transport and would bypass the Agent
package's versioning (the frontend ships and upgrades with the Agent release),
its protocol check and storage isolation (`appEntry.ts`), and bring back the
legacy host ports (`legacyAgentHost.ts`) the Agent App does without. The chat
is the same either way: `AgentWorkspace` is pantheon-ui's `LeftSidebar`,
`RightSidebar` and `ChatConversation`.

## Installable (optional)

`/app/<id>/manifest.webmanifest` (`id`, `name`, `start_url`, `scope: /app/<id>`,
`display: standalone`, icons). Built-ins: static, generated from the registry at
build time (½ day). Packaged Apps need their name and icon from the controller's
App index, which nginx cannot read: a Hub endpoint reading it (1–2 days) or
built-ins only. No offline mode (Apps need the bus).

## Phases

**Phase 1 — shell, host interface, intents, Coding Agents, Agent Canvas**
(≈ 9 days). `app.html` + rewrites; `AppPage.vue`; `AppHost` with DesktopHost
and PageHost; intent router, panel stack, Send to desktop, canonical links on
both surfaces; per-App state tools in the controller (Go) and migration; the two
Apps and the three viewers moved to it.

- S1 Signed out, `/app/coding-agents?node=<mac>&conversation=<id>` shows the
  login; after password or OAuth sign-in the same URL shows that conversation.
- S2 With the Workspace stopped, Coding Agents lists the Mac's sessions and
  takes input within 3 s of the page painting.
- S3 In page Coding Agents, clicking a `.py` path in a session opens Text
  Viewer in the side panel, the conversation still visible; a `.pdf` opens PDF
  Viewer stacked on it; **Open in own tab** moves it to `/app/pkg:text-viewer?open=…`.
- S4 Clicking a URL opens a browser tab; "Open in Pantheon Browser" opens
  `/app/browser?open=<url>` in a new tab.
- S5 An intent for `xwindow` (or any `desktop-only` App) offers Send to
  desktop; with no desktop open, the window is in the session afterwards
  (`get`) and appears when the desktop is opened.
- S6 The same `/app/…?open=` link clicked or pasted on the desktop opens a
  window with that file; on the desktop, Coding Agents' path clicks still open
  windows as before.
- S7 Projects and tabs added on the page show in the desktop's Coding Agents
  window within 1 s and the reverse; a canvas made on either shows on the
  other; an existing user's canvases and tabs are in the per-App key after
  migration, and closing the desktop window no longer loses them.
- S8 The desktop session holds no record for an App page (test).
- S9 vitest for the router, hosts, URL parsing and migration (fail without the
  change); Go tests (`-race`) for the per-App state tools; `vue-tsc`; staging
  check of S1–S8.

**Phase 2 — Pantheon Agent at `/`** (≈ 3 days).

- A1 A deployment user at `/` gets the Agent page, not a redirect; `/c/<id>`
  opens that chat; switching chats updates the URL.
- A2 A restart or upgrade shows starting and reconnects without a reload.
- A3 A file the Agent links opens in the panel; "Open App" opens `/app/<id>`.
- A4 A legacy user still gets `ChatView`. A5 Tests as S9.

**Phase 3 — the other Apps, packaged intents, visitors, installable**
(≈ 6 days, + ½–2 days for manifests).

- P1 Every App not `desktop-only` opens at `/app/<id>` and does its main task
  with no desktop open; `desktop-only` ones show Send to desktop.
- P2 A packaged App's `navigate openApp` and file opens route through the
  host on both surfaces; three Store Apps checked (backend, file-opening,
  frontend-only).
- P3 On a hosted link, a file under `share.paths` opens in a `share.viewers`
  panel; opening another App, Send to desktop and a path outside `share.paths`
  are refused (tests, and a staging link).
- P4 A built-in App installs from Chrome's install button and opens at its
  own URL. P5 Tests as S9.

## Open decisions

1. **Path URLs (`/app/<id>`) or hash?** Path: one install scope per App and
   readable links; one nginx and one dev rewrite.
2. **Per-App state for every App, or only Apps that opt in?** Opt-in
   (Coding Agents, Canvas first); others keep window args until they need it.
3. **URL default on an App page: system browser or Pantheon Browser?**
   System browser (as today), Pantheon Browser in the link menu and as a
   setting.
4. **Panel stack depth and width.** Up to 3, 40 % of the page, resizable;
   more opens in a tab.
5. **`/` for deployment users becomes the Agent page (Phase 2)?** Yes; the
   desktop stays at `/desktop.html`, one click away in the top bar.
6. **Visitor viewers**: owner lists them per share (`share.viewers`), not
   every installed viewer.
7. **Installable packaged Apps** (Hub endpoint reading the App index): built-ins
   first, packaged when asked.
