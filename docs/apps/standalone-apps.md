# Standalone Apps: one App, full page, without the desktop (2026-11-03)

Status: Phase 1 built and on staging (2026-11-03): `app.html`, the App host
interface (DesktopHost, PageHost), the intent router and side panel, canonical
links on both surfaces, per-App state (`app_state_get`/`app_state_put`) with
Coding Agents and Agent Canvas moved to it. Open decisions 1–4 and 8 taken as
recommended below. Phase 2 (the Pantheon Agent at `/`, A1–A5) built, decision 5
taken, and signed off on 2026-11-06: see "Phase 2 as built". Phase 3 (P1–P5 and
the Phase 2 follow-ups) built, decisions 6 and 7 taken, and signed off on
staging on 2026-11-07 after six checks (frontend #47, Agent App 0.9.119): see
"Phase 3 as built". Viewers reading a packaged App's private data folder is
deferred by the owner (decision 9). Not in production.

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
No Hub change. A `public` App opens without signing in (see "Who can open an
App link").

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

1. Target App has `panel` → open as a tab in the page's **side panel**
   (below). Context (the conversation, the Coding Agents tab) stays put.
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

**The side panel.** One per App page, on the right, beside the App:

* **Tabs.** Each open file or viewer is a tab: App icon, title (file name),
  close button; a tab whose App is still loading shows its status. Each tab is
  an `EmbeddedApp` (packaged viewers) or a built-in component with a local view
  id (`local-panel-<uuid>`), kept mounted while hidden so switching is instant
  (up to 8 tabs mounted; older hidden ones unmount and remount on select).
  Opening a target already open (same App, same file id) selects that tab
  instead of adding one. Up to 20 tabs; the oldest unpinned one closes beyond
  that. Tab menu: **Open in own tab** (`/app/<id>?open=<target>`, closes the
  panel tab), **Send to desktop**, **Copy link**, Close, Close others.
* **Keys** (while focus is in the page, not inside an editor that takes them):
  `Ctrl/Cmd+\` collapse/expand the panel; `Ctrl+Tab` / `Ctrl+Shift+Tab` next /
  previous tab; `Ctrl/Cmd+W` close the tab when focus is in the panel (the
  browser's own `Cmd+W` is not overridden elsewhere); `Esc` returns focus to
  the App.
* **Collapse.** A toggle in the top bar (and the key) hides the panel without
  closing its tabs; collapsed, the toggle shows a badge with the open-tab
  count. Opening a new target expands it.
* **Width.** A divider drags the width (min 280 px, max 70 % of the page,
  double-click resets to 40 %).
* **Remembered** per App in its per-App state (below), under `page.panel`:
  `{open: bool, width: px, tabs: [{app, target}] (≤ 20), selected}`; a reload
  restores the tabs. Signed-in users remember in their own key; signed-out
  `public` pages and hosted-link visitors in the browser.
* **Narrow screens** (< 900 px wide): the panel is a full-width overlay over
  the App with a back button; the same tabs and keys; collapse hides the
  overlay.

**Canonical links.** A link to an App, file or URL is one URL that works
everywhere: `/app/<id>?open=…` or `/open?target=…`. The desktop intercepts these
(same-origin link clicks in Markdown and chat, paste onto the desktop surface,
`desktop.html?open=…` on load, next to the existing `?install=` in
`Desktop.vue`) and opens a window; an App page routes them as above; a
visitor page allows only what the share grants. "Copy link" in the link menu
and the window menu produce this form.

## Who can open an App link

An `/app/…` link can reach someone other than its owner (pasted in a chat, a
README). The rule is per App class, never per link:

| Class | Which Apps | Signed out | Signed in |
|---|---|---|---|
| `public` | no backend **and** nothing that acts on an account or node: no `call`, no `fs`, empty `placement.requires`, no `share` needed (Game of Life, a calculator, a viewer opening a URL) | usable, frontend from its public Store version (as hosted `frontend` mode), state in the browser | the same, state in their per-App key |
| `account` | a backend, or acts on the user's account or nodes: all built-ins (Coding Agents, Agent Canvas, Terminal, Files, Fleet, Store, Settings, Model Services, LLM Playground, Browser, Agent), viewers opening a node file, any packaged App with a backend or `fs` | a sign-in page | used **as themselves**: their own Fleet, nodes, instance and state |

The class is derived from the manifest (`entry.backend`/`fleetBackend`,
`placement.requires`, use of `fs`), and for built-ins declared in `registry.ts`.
A manifest may only tighten it: `"standalone": {"access": "account"}`; nothing
can loosen an App with a backend to `public`.

**Signed out, `account` App:** the page shows the App's icon and name (from its
public Store listing, or the built-in registry), "Sign in to use <App> with your
own account", and the sign-in form in place. The URL, including `?open=` and
App args, is not touched, so after sign-in (password or OAuth popup) the page
continues with the same App and intent.

**Signed in as someone else:** the App opens on *their* account. A packaged App
they have not installed shows the hosted `install` offer (name, version,
publisher, what it may do) and then returns to the same `/app/…?open=…` URL.
Args that point into the owner's world are not followed: a file id on a node
that is not theirs answers "This file is on a computer you cannot reach", an
unknown conversation or project id opens the App's home. Nothing of the owner's
is reachable from a plain `/app/` link.

**The owner's instance and files, for others**, only through a hosted link
(`/#/a/<id>`) in `host` mode, limited to the App's `share.methods` and
`share.paths` (`VisitorHost`). There a visitor may only `view` files under
`share.paths`, read through the visitor grant, as a side-panel tab, with a
viewer the owner lists in a new `share.viewers` (public Store Apps, served from
their Store version as `frontend` mode does). URLs open a plain browser tab.
Never the owner's other Apps, desktop, Agent, or Send to desktop; anything else
answers "Not available in a shared App". Built-ins have no `share` section and
cannot be hosted; another person uses them on their own account.

## The App page shell

`src/desktop/standalone/` (entry `main.ts`, root `AppPage.vue`), the parts of
`Desktop.vue` an App needs without the surface:

1. **Connection**: `workspace.connect()` as is (controller, App bus; the
   Workspace joins in the background), `registerCachedAppIndex` and the
   `desktop.apps` event for App ids, icons and placement, desktop-state `get`
   for settings and per-App state. Presence registers a viewport that does not
   render the desktop (`network/presence.ts`).
2. **Hosts**: `DialogHost`, `ToastHost`, `ContextMenu`, `TransferPanel`, drop
   overlay, side panel; `PageHost` provided.
3. **The App**, full page (`active: true`); packaged ones through `PackagedApp`
   with a `windowHost`, frontend and instance resolved as on the desktop
   (`windowNode.ts`, `startPlacedInstance`), "Waiting for <node>…" while it
   starts.
4. **Top bar** (36 px): icon and title, the App's live menus (`appMenus`),
   node status, Workspace status only for `needsPod` Apps, attention badge,
   the side-panel toggle (with its tab count when collapsed),
   **Open on desktop**, account menu (name, budget, recent Apps, sign out).

## Per-App state

Coding Agents (projects, tabs, per-node views) and Agent Canvas (canvases,
`persist.ts`) keep their durable state in window args today, so it belongs to a
window and dies with it. It moves to desktop-state, one key per App:

```
<fid>.owner.app.<app id>    {revision, state}   ≤ 256 KB, CAS on revision
```

(A packaged App's `pkg:<id>` is the key `app.pkg.<id>`: a colon is not a key
character, and built-in ids hold no dot.)

Tools `app_state_get(app_id)`, `app_state_put(app_id, state | patch, revision)`,
change event `{type: "desktop.app_state", app_id, revision}`. **Both surfaces use
it**: the desktop window and the App page show the same projects and canvases;
window args keep only the view (open tab, open canvas, viewport), the page keeps
its view in the URL and its side panel under `page.panel` in the same key
(every App page has a key for that, small; moving an App's durable state out
of window args is per App, Coding Agents and Canvas first). Migration: when the key is absent, the first reader merges
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
Agent links open as side-panel tabs. Legacy (non-deployment) users keep
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
and PageHost; intent router, tabbed side panel, Send to desktop, canonical links on
both surfaces; per-App state tools in the controller (Go) and migration; the two
Apps and the three viewers moved to it.

- S1 Signed out, `/app/coding-agents?node=<mac>&conversation=<id>` shows the
  login; after password or OAuth sign-in the same URL shows that conversation.
- S2 With the Workspace stopped, Coding Agents lists the Mac's sessions and
  takes input within 3 s of the page painting.
- S3 In page Coding Agents, clicking a `.py` path in a session opens Text
  Viewer as a side-panel tab, the conversation still visible; a `.pdf` opens
  PDF Viewer as a second tab; clicking the `.py` path again selects its tab
  (no third tab); **Open in own tab** moves it to `/app/pkg:text-viewer?open=…`
  and closes the panel tab.
- S3a Tabs: each shows the App icon and file name and closes with its button
  or `Ctrl/Cmd+W` (focus in the panel); `Ctrl+Tab`/`Ctrl+Shift+Tab` cycle them;
  switching back to a tab keeps its scroll position (still mounted).
- S3b Collapse: the top-bar toggle and `Ctrl/Cmd+\` hide and show the panel
  without closing tabs; collapsed, the toggle shows the tab count (`2`);
  opening another file expands it.
- S3c Width: dragging the divider resizes within 280 px–70 %; double-click
  resets to 40 %.
- S3d Remembered: after a reload, the panel's open/collapsed state, width,
  tabs and selected tab are as they were, per App (Coding Agents and Canvas
  remember separately), from the per-App key.
- S3e Narrow (< 900 px): the panel opens as a full-width overlay with a back
  button; tabs, keys and collapse work the same.
- S4 Clicking a URL opens a browser tab; "Open in Pantheon Browser" sends a
  Browser window with that URL to the desktop, saying whether it is in an
  open desktop tab or shows when the desktop is opened. (`/app/browser` is a
  Phase 3 page; until then it offers Send to desktop.)
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
  check of S1–S8 (S1 also with Google OAuth).
- S10 Signed out, `/app/agent-canvas` and `/app/pkg:<App with a backend>` show
  the sign-in page with the App's name and icon; after sign-in the same URL
  (and its `?open=`) continues; a `public` App at `/app/pkg:<id>` opens with no
  sign-in.
- S11 Signed in as another user, the owner's `/app/coding-agents?node=<owner's
  node>&conversation=<id>` opens Coding Agents on the other user's own nodes,
  shows none of the owner's sessions, and says the node cannot be reached
  (test with two staging accounts).
- S12 A deep `/app/<id>?…` URL loaded directly or reloaded serves the App
  page (no 404, no redirect loop) on both staging hosts — frontend nginx
  rewrite and ingress paths checked.
- S13 Top bar: App menus work, node status correct, the account menu lists
  recent Apps (`pages.recent`), "Open on desktop" opens `/desktop.html` with
  the same App open (a window via desktop-state, its view args carried).
- S14 No desktop regression: opening windows, Coding Agents and Agent Canvas
  on the desktop behave as before; the existing vitest suites of the touched
  areas pass.
- S15 Page Coding Agents is fully functional: live session states,
  opening/attaching terminals (as its own tabs), model picker, Agents page,
  node switch.
- S16 First paint of an App page ≤ 2 s on a warm cache; light and dark themes
  correct.
- S17 Another signed-in user opening `/app/pkg:<App with a backend they
  haven't installed>` gets the install offer, then returns to the same URL.

Deferred to Phase 3 (visitors), decided 2026-11-03: a `public` App opening
signed out (S10's last clause). Its frontend has to come from its public Store version with no share
and no sign-in, which the Hub serves today only through a share link
(`/api/app-shares/{id}/package/…`); it needs a Hub endpoint for a public Store
version's files. Until then a signed-out visitor of any `/app/pkg:` link gets
the sign-in page, which is the safe direction.

**Phase 2 — Pantheon Agent at `/`** (≈ 3 days).

- A1 A deployment user at `/` gets the Agent page, not a redirect; `/c/<id>`
  opens that chat; switching chats updates the URL.
- A2 A restart or upgrade shows starting and reconnects without a reload.
- A3 A file the Agent links opens in the panel; "Open App" opens `/app/<id>`.
- A4 A legacy user still gets `ChatView`. A5 Tests as S9.

### Phase 2 as built (2026-11-06)

**What `/` draws.** index.html's entry (`ui/src/main.ts`) loads one of two
surfaces, each with its own styles: the **Agent page** (`standalone/rootPage.ts`:
`PageApp` with `root`, an App page of `pkg:agent`) or the **chat page**
(`legacyMain.ts`: `ChatView`, hash routes). The Agent page is drawn for a root
chat URL (`/`, `/index.html`, `/c/<chat id>`, with no hash route other than `#/`
or `#/service/…`) when this browser knows the signed-in account is a deployment
account (`rootSurface.ts`, localStorage `pantheon.rootSurface`, per user id). The
Hub says so only in `/api/chatroom` (`app_deployment`), so the first visit of an
account in a browser starts on the chat page; when its `/api/chatroom` answers
`app_deployment`, the page replaces its URL with the same root URL without the
hash route and loads it again (`openAgentPageInstead`, which replaces
`openDesktopInstead`): the URL stays `/` (or `/c/<id>`), no redirect, one extra
load once per account and browser. While it waits for that answer the chat page
is not drawn (`holdChatPage`: `#app` hidden until the answer is legacy, the page
goes to one of its own routes such as `#/login`, or 3 s at most), and the entry
applies the theme before choosing (`main.ts`), so the first visit shows the
page's background in the right theme and then the Agent page, not a dark fade
and the chat page's chrome. A legacy answer remembers `chat`; the Agent
page told by its connection that the account is legacy switches back the same
way. The chat page's other routes (`#/login`, `#/admin`, …) stay the chat page
for everyone. No nginx or ingress change: `location /` already falls back to
index.html for `/c/<id>`, and both staging hosts route `/` to the frontend.

**The Agent page.** `AppPage` with `agentPage` (the root, `/app/pkg:agent`,
`/app/agent`) mounts `standalone/AgentSurface.vue` in place of a registry App:
the owner's deployment instance found by `createAgentLookup()`
(`network/agentSetup.ts`: `looking | ready | starting | stopped | absent`, read
again every 4 s while starting, five failed reads before `absent`, following this
browser's first setup), shown through `PackagedApp` (app id `agent`, bound with
`appInstance`), so the Agent App's own frontend, versioned with its release. The
desktop's launcher (`AgentEntry.vue`) uses the same lookup and opens a window
with the binding. The view follows the Agent App's own entry in the deployment
(`agentView`), not the deployment's readiness: its intent `stopped` (Fleet's
per-App Stop, or the whole deployment stopped) is **stopped**; intent `running`
with a ready instance is **ready**, without one **starting** (or **stopped** with
the reason while an App needs the owner's attention); a deployment without an
Agent App, or no deployment, is **absent**. **Start Agent** (`startAgent`)
starts just the Agent App when the owner stopped it alone, and every App when
the whole deployment is stopped. A deployment account with no Agent yet is set
up as the desktop does (`autoSetUpAgent`). The page's buttons use the page's
own tokens (`styles/tokens.css`; a test checks every colour variable the page
components and `PackagedApp` read). The Agent page reads a stopped Agent again
every 15 s (`stoppedPollMs`), so a start made in Fleet or on the desktop brings
it back there too; the desktop launcher reads it only when opened or started.

**URL.** The page opens on the URL's chat (`initialState.chatId`); the Agent's
`setState({chatId})` reaches the page as its durable `savedState.chatId` (its
manifest's `persistState`) and is written by `PageHost` with the new `pageUrl`
option: `/c/<id>` at the root (`/` for no chat), `?chat=<id>` on
`/app/pkg:agent`. History is replaced, not pushed, as on every App page: Back
leaves Pantheon rather than returning to the previous chat. This is kept on
purpose (a chat switch is not a navigation the browser should replay). The
chat shown is passed back as `savedState`, so a reconnecting frame opens on it.

**Restart and upgrade.** An App page now holds a lease on its App's instance
(`retainAppWindow`, as `WindowFrame` does; before, a page never learned that its
instance ended). A failed renewal reads the node; `PackagedApp` then follows the
deployment to the next instance ("This App was updated or restarted.
Reconnecting…" → "Starting on your workspace…" → the Agent), no reload; it also
reads the next instance's manifest from its node when the Workspace's Desktop App
restarts with the same release change. A backend stopped for good
(`PackagedWindowHost.backendStopped`) makes the page show its stopped state and
open the next instance once it runs. A crash of the Agent process alone keeps
its instance and is not visible at all.

**Opening Apps and files.** On a page, `PackagedApp` routes a packaged App's
navigation through `PageHost` (`shell/appNavigation.ts` `navigateOnPage`):
`openApp` opens the App's own page `/app/<id>` in a tab (intent `page: true`;
an App whose page is Phase 3 shows its Send to desktop there), never a desktop
window. The App SDK (`app-host.html`, both copies) has `apps.openFile(path,
name)` and `apps.canOpenFiles`, granted by the boot capability `fileOpening: 1`,
which only page hosts send; the Agent frontend (`agent/hostFiles.ts`) then
registers a host file opener, so files the Agent links (`openFileSmart`: links in
answers, chips, output files) open as side-panel tabs, a relative path being the
Workspace's as on the desktop; a refused open falls back to the chat's own
preview. The desktop host grants `fileOpening` too (since frontend #36): a file
linked in the desktop's Agent window opens as a viewer window
(`openLinkedFile` → `DesktopHost.open`), as it did before the Agent became a
packaged App; other Apps still launch windows. Where no host takes a file (an
older desktop, a type no App opens), the chat opens it in the clicked surface's
own panel (`useOpenFile`): the Agent window has its own panel state, and a file
tab opened only in the page store stayed out of sight there.
The top bar of the Agent page has **Desktop** (`/desktop.html`, kept at narrow
widths) instead of Share and Open on desktop.

**Releases.** Frontend (UI `codex/agent-app-extraction`; #37 `ea04fc1b` after
the first check) and Agent App 0.9.111 (0.9.112 after the first check: the
chat's file panel and translations). 0.9.111 brought the SDK's `openFile` and
the Agent frontend's file opener (runtime `c576bc22`); no controller or Hub code
change. An older Agent release keeps working on the new frontend: its SDK
ignores `fileOpening`, so its files open in its own preview.

**Staging verification (2026-11-06,** frontend `4224bdd5`, Agent App 0.9.111,
accounts acct3 and acct4**).**

- A1: a fresh browser at `/`: chat page for ~0.5 s, then the same URL loaded
  again as the Agent page (navigation type `reload`, URL `/`); later visits draw
  it directly. Switching chats writes `/c/<id>` about 150 ms after the click;
  reloading `/c/<id>` shows that chat. `/` and `/c/<id>` serve index.html on
  both staging hosts.
- A2: release switches 0.9.111 → 0.9.110 → 0.9.111 with the page open:
  Reconnecting… → Starting… → the Agent in 31–35 s, no reload (a page marker
  survived), same URL.
- A3: the Agent's answer linking `p2_demo.py` opened it as a Text Viewer tab in
  the side panel; Fleet in the Agent's sidebar opened `/app/fleet` in a new tab.
- A4: a legacy (`test` role) account at `/` gets `ChatView` (`#/`), nothing
  remembered.
- A5: vitest for the lookup, root surface choice and switching, routing
  (`page`), `pageUrl`, page navigation and the SDK's `openFile`, the Agent's file
  opener, `AgentSurface` (URL sync, restart following, stopped) and
  `PackagedApp` (page host, upgrade race, `backendStopped`); all fail at the
  previous UI commit. vue-tsc passes; the touched suites pass except seven
  failures in `AppNodePicker.test.ts` and `desktop/App.test.ts` that fail the
  same way before the change.
- Fixes after the first independent check (frontend #37 `ea04fc1b`, Agent
  App 0.9.112 on acct3 and acct4, hub pinned to 0.9.112): with only the Agent
  App stopped (Fleet's per-App Stop), the page showed "Starting your Agent…"
  forever (the lookup read the deployment's readiness); Start Agent and "Set up
  my Agent" were near-invisible (`--action`/`--accent`, undefined on App pages);
  the retry button read `common.retry`; a file linked in the desktop's Agent
  window had no visible effect (the window's own panel stayed collapsed, and the
  desktop host did not take files); the first visit faded to dark and flashed the
  chat page. Verified live on acct4: Agent App alone stopped → stopped view in
  12 s, also after a reload, Start Agent visible (dark on light, light on dark),
  one revision change (just the Agent App), Agent back in 14 s on the same
  `/c/<id>`; whole deployment stopped → stopped view, Start Agent from the
  desktop launcher brought every App back; the desktop launcher shows "The Agent
  is stopped" with Start Agent in both cases; release switch 0.9.111 → 0.9.112
  with the page open: no reload, same URL; `/c/<unknown id>` → "Could not load
  this conversation" with **Retry**; a pointer click on `p2_demo.py` in the
  desktop's Agent window opened a Text Viewer window with the file; on the
  Agent page the same link still opens a side-panel tab; a fresh browser's first
  visit keeps `#app` hidden on the light background (no dark fade, no chat-page
  text) until the in-place reload to the Agent page. Tests: lookup by the Agent
  App's state (Agent alone stopped, whole deployment stopped, starting, absent,
  stopped polling), page colour tokens, translation keys, desktop file opening,
  the surface's own panel, the first-visit hold; all new ones fail without their
  fix. vue-tsc passes; no new failures in the touched suites compared with
  `4224bdd5` (one timing test in `agent-canvas/linkRoute.test.ts` failed once
  under load and passes alone).
- Also: `/desktop.html` and its Agent window unchanged; first contentful paint
  of `/c/<id>` 96 ms on a warm cache; light and dark themes correct (top bar and
  Agent frame); Phase 1 pages (`/app/coding-agents`, `/app/agent-canvas`) work.

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
- P3a Signed out, a `public` App at `/app/pkg:<id>` opens with no sign-in,
  its frontend from its public Store version (moved here from S10; needs the
  Hub endpoint above).
- P4 A built-in App installs from Chrome's install button and opens at its
  own URL. P5 Tests as S9.

### Phase 3 as built (2026-11-06)

**P1, the other Apps.** Every built-in that is not `desktop-only` now has a page
(`host/ownTab.ts` `PAGE_READY`): Terminal, Files, Fleet, Settings, Store, Model
Services, LLM Playground, Office, Google Workspace and Browser, besides Coding
Agents, Agent Canvas and Team Chat; Jupyter (`pkg:integrated-notebook`) is a
packaged App and needed no change. Each App asks its host instead of the windows
store, as the mapping table says (`useAppHost()`; without a host, in a component
test, it does what it did before), and keeps the desktop's behaviour there:

- Terminal: its session and computer in the page's URL (`ptySession`,
  `nodeId`); other Apps through `host.open`; shells are not closed when the page
  goes away (`closedForGood`). The per-node session map is not in the URL, so a
  reload brings back the current node's shell only.
- Files: opens, "Open with", Open in Terminal and Manage computers through the
  host; the folder shown is the page's `path`; Chat with Agent opens the Agent's
  page; file drops are taken by the page itself. Files has no desktop-surface
  parts of its own to drop.
- Fleet: a node's Files, an instance's Open and the rest through the host; on a
  page a window badge shows that window on the desktop (`showOnDesktop`), and
  Close window is not offered.
- Settings: on a page, the cards that configure the desktop surface (login
  sounds, resolution, wallpaper, menu bar clock, Dock, desktop icons) are not
  shown and the section is named Appearance; the section is in the URL.
- Store: **Open** of an installed App opens `/app/pkg:<id>` in a tab (the
  desktop still launches its window); the Store's own navigation is in memory
  only.
- Model Services and LLM Playground: the Playground, Fleet, Settings and the
  Agent open through the host; "Use in chat" opens the Agent page with a notice
  to choose the model there.
- Office: the `?open=` document, its session in the URL, close back to Office's
  home; "Continue in background" is not offered on a page.
- Google Workspace: files open in the Browser's page (`/app/browser?open=<url>`).
- Browser: on a page it asks the Browser App's instance for a Chromium page
  with no desktop window (`page_id` in the URL). A reload, or the same link in
  a new tab, asks that instance again for the page by its id (the URL keeps the
  id, not the instance binding, so a page never takes the desktop's legacy
  path through the Agent's own page list); a page the instance no longer holds
  is replaced by a fresh one at the page's `open` URL. Chromium's own dialogs
  (file chooser, print) are offered with **Send to desktop**. A page cannot
  tell a reload from a closed tab, so it never closes its Chromium page on the
  way out: while it shows the page it renews a note of it in the browser
  (`browser/pageLeases.ts`, every minute), and the next Browser page started
  in that browser closes those nobody showed for 10 minutes, on their own
  instance (pages only; desktop windows and the Agent's pages are not
  touched).
- `desktop-only` Apps (`xwindow`, `agent-view`, native-stream Apps such as
  QuPath) show **Send to desktop** and **Open desktop**.

A node's root folder keeps its slash when routed (`pantheon-node:///<node>/`),
which Fleet's "Files" for a node needs.

**P2, packaged Apps' navigation on both surfaces.** `PackagedApp` sends a
packaged App's `navigate` to its surface's host on the desktop as on a page
(`navigateOnPage`): `openApp` is `{verb: 'open', app, page: true}`, which a page
opens as the App's page in a tab and the desktop as the App's window (the
existing one, as the launcher does: `DesktopHost` `windows.launch`); `openFile`
is a file view (side-panel tab, or a viewer window). Checked with three Store
Apps: Hello Files (a backend; it opens files and other Apps), MSA Viewer
(frontend only, a viewer that opens `.fasta`), Game of Life (frontend only).

**P3, hosted links.** `VisitorHost` (`host/visitorHost.ts`, used by the visitor
page `views/HostedAppPage.vue`) routes what a hosted App opens for its
visitors. A file whose path is under the App's `share.paths` (an absolute path
on the App's own origin, with no `.`/`..`/empty or encoded segments, never
`/_fleet/`, `/__fleet/` or `/rpc`) opens read-only in a side-panel tab, with a
viewer the owner listed for the link (decision 6, `share.viewers`): the viewer
runs from its public Store version in the sandboxed app host (an opaque origin,
no backend, no `fs`, no navigation) and is handed the file read through the
visitor grant (as a `data:` URL, up to 8 MB). Another App, the desktop (Send to
desktop), the site's own pages, a file outside `share.paths`, editing, and a
file no listed viewer opens are refused with "Not available in a shared App".
The App is told it may open files (`fileOpening`) only when the link has both
shared paths and viewers. The Share dialog lists the installed viewers (packaged
Apps with `panel` and no backend) for a Mine link of an App with `share.paths`;
the Hub keeps them with the link's target (no migration), pinned to their
current public version, never sends them to the controller, and accepts only
public Store Apps with no backend (Text Viewer, which declares `caps.fs`, is
refused with that sentence).

**P3a, a `public` App signed out.** `/app/pkg:<id>` signed out asks the Hub for
the App's public Store version (`GET /api/public-apps/<ref>`: Store id, name or
installed App id) and, when it answers, runs its frontend from
`/api/public-apps/files/<package id>/<version>/…` in the sandboxed app host
(`allow-scripts allow-downloads`, as a packaged App's frame on the desktop:
an App may hand its visitor a file it made, and a download from an opaque
origin reaches nothing else) with its state in the browser, under a bar that offers
sign-in (the URL is kept); otherwise, or until the Hub answers, the sign-in page
(nothing flashes). A signed-in person who has not installed a public App gets,
next to the install offer, **Use it in this browser** (the same frontend; its
state stays in the browser, not in a per-App key).

*Never a private package.* Every public route (summary, files, viewers on a
visit, the manifest and icon below) goes through one Hub function,
`public_version()` (`pantheon_hub/api/public_apps.py`): a Store package of type
`app`, `is_public`, moderation neither pending nor rejected, a version with an
App release, and, except for the manifest and icon, a manifest of class
`public` (no `backend`, `fleetBackend` or `nativeDriver`, even empty; `caps.fs`
empty; `placement.requires` only `dom`; not `standalone.access: account`;
a packaged frontend). Name and App id resolution skip packages that are not
public, visibility and moderation are read from the database on every request,
and every refusal is the same 404 as an unknown App, so a private package's
existence does not leak. Hub tests cover private, pending and rejected packages
on the summary and on the files (with a correct version and path), each
account-class manifest field, path traversal, and the function on its own.

**P4, installable.** Every built-in with a page has
`/app/<id>/manifest.webmanifest`, generated at build time (a Vite plugin) from a
plain list (`standalone/installable.ts`, tested against `registry.ts`), with
192/512 px and maskable PNG icons in `/app-icons/` (`scripts/make-app-icons.mjs`;
`pantheon-192/512.png` as the fallback); `id`, `start_url` and `scope` are
`/app/<id>`, `display: standalone`. nginx serves the manifest file inside
`location ^~ /app/` and every other `/app/…` URL is still `app.html`. Packaged
Apps (decision 7, done: about half a day): the page links
`/api/public-apps/<ref>/manifest.webmanifest`, from the App's public Store
listing (any class; `id`, `start_url` and scope `/app/pkg:<App id>`, the
manifest id, whichever ref asked, so a Store name and the App id are one
install; its release icon); a package not in
the public Store has no manifest and is not installable. No service worker:
Chromium needs none for installing.

**Phase 2 follow-ups.**

1. A whole-deployment stop with the Agent page open: lease renewals go through
   the `fleet` App, which stops too. After 30 s of failed renewals on a
   deployment (`UNOBSERVED_MS`), the window asks the Hub's deployment: when it
   lists this instance under an App whose intent is `stopped`, the backend has
   stopped and the page shows "Your Agent is stopped"; a running App, an
   instance not listed, or a failed read keep retrying (a blip never stops it).
2. A legacy account whose `/api/chatroom` answers 5xx or nothing: "chat" is
   remembered for 10 minutes (only when nothing is known for that user), so
   later visits draw the chat page at once; a 4xx is not remembered, and a real
   answer replaces it (a deployment still switches to the Agent page in place).
3. **Start Agent** shows "Starting your Agent…" from the click until the lookup
   reports starting or ready, or the start fails (stopped again, with the
   error); the desktop launcher shares it.

**Releases.** Frontend #38–#40 (UI `codex/agent-app-extraction`, #40 =
`66d01e9d`); Hub `58d8e2e` (no migration). After the first check: frontend
#41 (`2c943953`) and Hub `d233195` (no migration). No Agent App, agents image,
controller or node change: the App SDK's `apps.open` and `apps.openFile` were
already in Agent App 0.9.112's app host. After the second check: frontend #42
(`9eef7734`) and #43 (`96523eb6`), Agent App 0.9.113 (its frontend writes the
chat at once); Hub, agents image, controller and node unchanged. After the
third check: frontend #44 (`e1d70b54`) and Agent App 0.9.114 (the Agent's
frontend and the runtime's App SDK); Hub, agents image, controller and node
unchanged. After the fourth check: frontend #45 (`5e616a5c`) and #46
(`9aadd0ce`), Agent App 0.9.115-0.9.118 (0.9.118 live: the Agent's frontend,
and its manifest persists `drafts`); Hub, agents image, controller and node
unchanged. After the fifth check: frontend #47 (`cba6981e`) and Agent App
0.9.119 (the Agent's frontend, and the Desktop's Browser launch, runtime
`43eb6da9`); Hub, agents image, controller and node unchanged.

Open on desktop, as built after the fourth check: a window showing the same
thing is used (Terminal, Coding Agents, Office, Browser, by what they show);
a file or URL page uses the window showing that file; Team Chat's open window
takes the page's view and follows it, and is said to be in front only once it
reports showing that view; every other App's open window (Fleet, Settings,
Model Services, LLM Playground, Store, Google Workspace, Agent Canvas,
packaged Apps) comes forward as the person left it, args untouched, and the
toast says so ("as you left it there; this page's view was not moved into
it"). With none open, a new window opens on the page's view. A Team Chat
window tells its own writes of its args from a view handed over by a number
it writes with each (`teamChatWrite`), so the desktop session's echo of an
earlier write is not followed (after the fifth check). A Browser page offers
Open on desktop once it has its Chromium page.

Cold load of the Agent page (`/`), measured: with the Agent's computer up,
the Agent is usable at 4-7 s. With the Workspace paused, the Agent's computer
is down too and the Agent waits for it: usable at 30-35 s, and up to 160 s
right after an Agent App release (the Agent waited for its old computer).
Meanwhile the page says "Waiting for your Workspace to start" (the controller
reports the Agent as waiting for its node), then "Starting your Agent on your
Workspace".

**Staging verification (2026-11-06, frontend #39–#40, hub `58d8e2e`, account
acct4).**

- P1: Terminal typed on the agent node and on the Workspace (computer switch);
  Files opened `p2_demo.py` as a side-panel Text Viewer tab and kept the folder
  in the URL; Fleet listed nodes and App instances and its node "Files" opened
  `/app/files` in a tab; Settings without the desktop cards; Store installed a
  Store App and **Open** went to `/app/pkg:hello-files`; LLM Playground ran a
  prompt (`deepseek-v4.1-flash`); Model Services listed the models; Google
  Workspace's Docs opened `/app/browser?open=https://docs.google.com/document/`,
  which streamed Chromium on that page; Jupyter opened an `.ipynb` from `?open=`
  and ran a cell (42); `/app/xwindow` offers Send to desktop. Office reached
  "Office editing is not configured on this server yet", the same as an Office
  window on the desktop (staging has no document server).
- P2 (corrected after the first check: the tabs opened, but their viewers
  could not read the files, see "Fixes after the first check"): Hello Files on
  its page opened `notes.md` and `demo.fasta` as side-panel
  tabs (kept after a reload) and Fleet as `/app/fleet` in a tab; in a desktop
  window it opened `notes.md` as a viewer window and Fleet as a window, the same
  Fleet window again on a second click. Game of Life and MSA Viewer ran from
  their Store versions.
- P3: a Mine link of Hello Files with MSA Viewer listed: an anonymous visitor
  opened `/package/files/demo.fasta` in an MSA Viewer tab; `/package/app.json`
  (not shared), `notes.md` (no viewer), another App (Fleet, Files, Terminal,
  `xwindow`), `/desktop.html`, `../` and `/_fleet/fs` were refused; making the
  link with Text Viewer was refused by the Hub with its sentence.
- P3a: signed out, `/app/pkg:game-of-life` and `/app/pkg:pantheon-msa` ran with
  no sign-in; `/app/pkg:hello-files` (a backend) showed the sign-in page; the
  Hub answered 404 for `hello-host`, `hello-files` and an unknown id.
- P4: Chromium's `Page.getInstallabilityErrors` returned none for
  `/app/terminal`, `/app/files`, `/app/coding-agents` and `/app/pkg:game-of-life`
  (manifest found, scope `/app/<id>`); headless Chromium shows no install
  button, so the click itself was not made.
- Follow-ups: with `/` open, stopping every App of the deployment showed the
  stopped view after 45 s (before: never); Start Agent showed the starting view
  at once (20 ms) and the Agent was back in 18 s; a legacy account with a 503
  `/api/chatroom` waited 3.5 s once, then 0.13 s on the next visits.
- No regression seen: `/` (Agent page), `/desktop.html` and its windows,
  `/app/coding-agents`, `/app/agent-canvas`, `/app/team-chat`, the invite pages.
- P5: vitest for the routing, the hosts (DesktopHost `page`, VisitorHost,
  PackagedApp on both surfaces), each App on a page, the public App page, the
  install offer, the manifests and the follow-ups; the new ones fail without
  their change. vue-tsc passes; the touched suites fail only where they failed
  before (AppNodePicker, `desktop/App.test`, `registry.packaged`, StoreApp,
  appCapabilities). Hub tests for the public routes, viewers and manifest.

Open on desktop (after the third check): the window used is one showing the
page's very thing (a Terminal's shell on its computer, a coding agent's
conversation, an Office session, a Browser's Chromium page), else a new one;
the page says "in front" only once that window shows what was handed over. A
Browser page handed to a desktop window is that window's from then on: the
page stops showing it.

Left as it is: a Browser page closed keeps its Chromium page on the node until
another Browser page is started in that browser (10 minutes or more later);
Terminal's other nodes' sessions and the Store's navigation are not in the URL;
a signed-in person's public App keeps its state in the browser; packaged Apps not
in the public Store are not installable; viewers must not declare `caps.fs`.

#### Fixes after the first check (2026-11-07)

The first independent check found one Medium problem and a few small ones.
Fixed in frontend #41 (UI `2c943953`) and Hub `d233195`; no Agent App, agents
image, controller or node change (the Browser App's instance already attached
a page by its id).

1. **A Browser page failed after a reload** ("No bound Browser page"). The
   page's URL kept `page_id` but not the instance binding, so a reload took the
   desktop's legacy path (a page id with no binding) and asked the Agent's own
   page list, which never had it. Meanwhile nothing retained the Browser
   instance, which stops 60 s after its last window, taking its Chromium pages
   with it. Now a page always goes through the Browser App's instance (it is
   retained, and asked for the page by id); a page the instance no longer
   holds (an exact "no such" answer) is replaced at once by a fresh one at the
   page's `open` URL. Closed pages are let go after 10 minutes as described
   under P1 (Browser). The two Chromium pages the check left were gone with
   the instance (it stopped 60 s after the check's tabs, and the Workspace was
   paused).
2. **A packaged App's own files cannot be read by viewers** (Hello Files'
   `demo.fasta` on the page and on the desktop: "outside the LiveView data
   server roots"). Not fixed: an App with a backend runs as its own instance,
   whose workspace is its private data folder
   (`…/fleet-node/apps/<deployment>/data/<instance>/workspace`). Viewers read a
   path through the Desktop App's data server, whose roots are the user's
   workspace, the Desktop App's own folder and the App catalogs, never another
   instance's data folder. Serving the App's folder would take either a root
   per installed App on the Desktop's server (wider than the App's own folder
   for every viewer) or reading through the App's own `fs` scope (only for
   Apps that declare `caps.fs`, text only today, and handing the viewer the
   file's content instead of a path, on both surfaces). Until then an App
   should link files in the user's workspace (relative paths are the
   Workspace's), or share them for visitors under `share.paths`. The P2
   verification below is corrected: the tabs opened, their viewers could not
   read the files.
3. A hosted link's (and a signed-out public App's) bridge `win` request, such
   as `openImage`, is answered at once with "Not available in a shared App"
   (signed out: "Sign in to use this from your own account"), instead of
   waiting for its timeout.
4. **Open on desktop** from a page opened with `?open=` carries that file (or
   the Browser's URL) into the desktop window, using a window already showing
   that file, else a new one (the App's front window is reused only when the
   page shows no file).
5. One install identity: the App's own id (its manifest id). Signed in,
   `/app/pkg:pantheon-msa` already went on at `/app/pkg:msa`; signed out the
   page now moves there too once the Hub names the App (query and hash kept,
   manifest link replaced), and the Hub's Web App manifest uses the App id for
   `id`, `start_url` and scope whichever ref asked. The public App frame keeps
   `allow-downloads` (see P3a). The `xwindow` page is titled **Sandbox Window**.

Tests (each fails without its change): vitest `BrowserApp.reload.test.ts` (the
real `useStreamBackend`, only the network edge faked: the Agent's toolset
answers "No bound Browser page" for a bare id, the Browser instance behind
`desktop_stream_call` holds the page or not), `pageLeases.test.ts`,
`HostedAppPage.test.ts` and `publicApp.test.ts` (`win`), `PageTopBar.test.ts`
(Open on desktop with `?open=`), `installable.test.ts` (one manifest link
after the move; the `xwindow` name), `publicApp.test.ts` (canonical path);
Hub `test_public_apps.py` (manifest id by package id, name and App id);
pytest `test_stream_fleet.py` (a fresh Agent toolset reattaches through the
instance binding, routed to a real node toolset). vue-tsc passes; the wide
vitest run (`src/desktop src/views src/components src/stores src/composables`)
fails the same 20 tests in 12 files as at `66d01e9d`; the Hub suite fails the
same tests as at `58d8e2e`.

Verified on staging (frontend #41, both hosts `index-CmE7wNAw.js`,
`desktop-B48bR4lW.js`, `app-DXRge41t.js`; Hub `d233195`; acct4):

- `/app/browser?open=https://example.com/`: a reload of a page whose instance
  had stopped opened a fresh page at the URL; reloading again, and the same
  link in a new tab, attached to the same page (`page_id` unchanged, Chromium
  streaming). A page's note aged past 10 minutes and its tab closed: the next
  Browser page closed its Chromium page (its link then opened a fresh one).
- A Mine link of Hello Files with MSA Viewer: `win` `openImage` answered in
  0 ms with the refusal; the visitor still opened `demo.fasta` in an MSA
  Viewer tab and was refused `app.json`; revoked, "This link was withdrawn".
- `/app/pkg:text-viewer?open=<p3_demo.ipynb>` → Open on desktop: a Text Viewer
  window showing `p3_demo.ipynb`.
- `/app/pkg:pantheon-msa` signed out and signed in both end at `/app/pkg:msa`
  with manifest `/api/public-apps/msa/manifest.webmanifest`, whose `id` is
  `/app/pkg:msa` (also asked as `pantheon-msa`); `/app/xwindow` is titled
  Sandbox Window.
- No regression: `/`, `/desktop.html`, `/app/coding-agents`, `/app/team-chat`,
  `/app/terminal`, `/app/files` (first paint 40–92 ms), the shared link above.

#### Fixes after the second check (2026-11-07)

The second independent check found two Medium problems (one older than this
phase) and a few small ones. Fixed in frontend #42–#43 (UI `9eef7734`,
`af45f938`, `96523eb6`) and Agent App 0.9.113 (its frontend; runtime
unchanged); Hub unchanged (`d233195`), agents image, controller and node
unchanged.

1. **Open on desktop from a Browser page opened a dead window** ("No bound
   Browser page"). `desktopView()` sends the page's plain view args, and the
   instance binding is not one of them, so the desktop window had a page id
   and no binding: the legacy path, whose page list never had that page. Now
   the Browser's hand-over carries the page id with the binding of the
   Browser instance holding it, the page's current URL and `recover_missing`:
   the window attaches to the same Chromium page; a page gone by then opens
   fresh at that URL (once; then the window's own rules). The page marks that
   Chromium page as handed over, so no later Browser page reclaims it.
   The other Apps' Open on desktop carry what their windows need as plain
   args: Terminal its `ptySession`/`ptyNodeId`/`nodeId` (the window attaches
   the same PTY session), Coding Agents `nodeId`/`conversationId`, Team Chat
   its conversation and thread args, Jupyter and Files the file or folder,
   Fleet its tab and node. Only the Browser and Office bind an instance in
   their view; Office opens its session by id through the deployment's
   instance (not testable on staging: no document server).
2. **`/c/<id>` named the previous chat right after a switch** (older than this
   phase). Not a stale write: the Agent's chat write (`setState({chatId})`)
   went through the App SDK's 150 ms commit debounce before the page could
   write `/c/<id>`, so the URL read right after a click (Check 2's reads)
   still named the chat before. The Agent now writes its chat at once
   (`debounceMs: 0`, as the Notebook does for its file); the URL follows in
   about 15 ms (one frame-to-page message), measured live, against about
   180 ms before.
3. **A Browser page on a paused Workspace stayed on "desktop is not running
   yet"**. That answer means the Workspace's desktop is still starting: the
   page now keeps connecting and asks again every 5 s (up to 5 minutes), and
   opens its page by itself once the desktop answers. The page id reaches the
   URL when the page is bound (8 s after "Connecting to Browser…" in the
   retest); the 90 s seen in the check was the second page creation after a
   manual Try again, which no longer happens.
4. **A file in a packaged App's own storage** showed the data server's
   internal paths and a Retry. It now says "This file is in the App's own
   storage, which viewers can't open yet." with no Retry (and no retry on
   activation). Reading it is deferred, see the owner's decision below.
5. **Signed in, `/app/pkg:<store name>` moved to the App id only sometimes.**
   The move depended on which answered first (the Store listing, the
   registry, an App resolved under the Store name). It now depends only on
   what is installed: it moves unless a package is installed under that very
   id, waits only until the account's App index is known, keeps the query and
   the hash, and a busy Store (429) is asked again instead of read as
   "unlisted".
6. **"It is in front in your desktop tab" when it was not.** The open desktop
   tab now answers twice: at once that it is there (so no second desktop tab
   opens), then whether it really shows the window. A window of an App that
   tab does not know (installed after it loaded) makes it read its Apps again
   first. The page says "in front" only on that confirmation, within 8 s;
   otherwise "Your open desktop tab has not shown it yet. If it does not
   appear there, reload that tab."
7. A Browser page gives its claim on the Browser instance back on `pagehide`
   (and takes it again if the browser restores the page from its
   back-forward cache). Best effort, and not effective on a closed tab yet:
   the release goes through the Fleet App's gateway (a deployment read, a
   connection, then the call), which does not leave before the page is gone.
   Measured live, the instance still stopped about 6 minutes after its last
   tab closed (the claim's 5-minute expiry, then 60 s). Releasing on close
   would take a single request the browser sends while unloading
   (`fetch` with `keepalive` to an endpoint ready beforehand); left for later.

**Owner's decision (2026-11-06).** Letting viewers read a packaged App's
private data folder is deferred until an App needs it; until then the viewer
says so plainly (item 4). An App should link files in the user's workspace,
or share them for visitors under `share.paths`.

Tests (each fails without its change, checked by reverting each source
file): `BrowserApp.handover.test.ts` (the desktop window attaches through the
binding, recovers a gone page at the current URL, a window of its own still
asks; a page whose desktop is starting opens by itself),
`rootChatUrl.sync.test.ts` (switches A→B→A and reads `/c/<id>` 10 ms after
each switch, through the shipped App SDK, the real PackagedApp bridge, the
Agent page's mapping and the page host's URL writer),
`PackagedApp.test.ts` and `appStorageFile.test.ts` (plain message, no
Retry), `appIdentity.test.ts` (the move), `desktopTab.test.ts` and
`pageHost.test.ts` (shown only when confirmed), `pageLeases.test.ts` (a
handed-over page is never reclaimed), `useStreamBackend.test.ts` (claim given
back on pagehide, a desktop window keeps its own). vue-tsc passes; the wide
vitest run (`src/desktop src/views src/components src/stores src/composables
src/agent`) fails the same 20 tests in 12 files as at `2c943953`; the Hub
is unchanged and its suite fails the same tests as at `d233195`.

Verified on staging (frontend #42 then #43, both hosts `index-BpDBoEDz.js`,
`desktop-C4hf-o5A.js`, `app-CIvzmfE0.js`; Agent App 0.9.113 on acct3 and
acct4; acct4), each item reproduced first on #41 / 0.9.112:

- Browser: `/app/browser?open=https://example.com/` → Open on desktop with
  the desktop open: "It is in front in your desktop tab" (confirmed in
  0.6–0.9 s), and the desktop window streamed the same Chromium page (same
  `page_id`, the Browser instance's binding). A window handed a page id the
  instance no longer held (`deadbeef0001`) opened a fresh page at its URL
  (`https://example.org/`) by itself. On #43 the window's `recover_missing`
  is cleared once attached.
- `/c/<id>`: from `/c/<A>`, switching B, A, B, A: the URL named the chat shown
  14–18 ms after each click (before: about 180 ms, and a read right after the
  click named the previous chat).
- Paused Workspace: `/app/browser?open=…` showed only progress ("Preparing
  the selected backend node…", "Starting backend…", "Connecting to
  Browser…"), no error and no Try again; the page id reached the URL 125 s
  after the load (8 s after "Connecting to Browser…"); before, "desktop is
  not running yet" stayed for 4 minutes until Try again.
- Hello Files page: its `demo.fasta` and `notes.md` tabs say "This file is in
  the App's own storage, which viewers can't open yet.", with no Retry and no
  internal path.
- Signed in, `/app/pkg:pantheon-msa?x=1#h` ended at `/app/pkg:msa?x=1#h` in 6
  of 6 loads (3 in fresh browser contexts, about 0.5 s), manifest link
  `/api/public-apps/msa/manifest.webmanifest`; signed out 3 of 3, the same.
- Open on desktop's message: not reproduced before the fix (Game of Life moved
  to Trash and installed again from the desktop Store window after that
  desktop tab loaded, then Open on desktop from its page: the window showed);
  the honest message is covered by the tests above.
- No regression: `/` and `/c/<id>` (first paint 64–252 ms), `/desktop.html`
  and its windows, a desktop Browser window, `/app/browser`,
  `/app/coding-agents`, `/app/team-chat`, `/app/terminal`, `/app/files`
  (44–88 ms), and a Mine link of Hello Files with MSA Viewer (an anonymous
  visitor opened `demo.fasta` in an MSA Viewer tab; revoked, "This link was
  withdrawn").

#### Fixes after the third check (2026-11-07)

The third independent check found two Medium problems and four Low ones.
Fixed in frontend #44 (UI `2fd74b80`, `d8fad372`, `e1d70b54`) and Agent App
0.9.114 (its frontend and the runtime's App SDK, runtime `b12231b1`); Hub,
agents image, controller and node unchanged.

1. **Terminal's Open on desktop came up on the wrong shell.** A Terminal page
   on the Workspace has no `nodeId` in its URL (the Workspace is the empty
   node), and Open on desktop reused the Terminal's front window with the
   page's args laid over the window's own: the window kept its own `nodeId`
   (the agent computer) and showed that computer's shell. Now Open on desktop
   names the thing the page shows and uses only a window showing that very
   thing, else a new one (`pageHost.ts` `desktopReuse`, `HAND_OVER`):
   Terminal by `nodeId`, `ptySession` and `ptyNodeId` (an absent value is the
   Workspace), Coding Agents by node and conversation, Office by its session,
   the Browser by its Chromium page. Apps whose front window is reused (Team
   Chat) get every view key of theirs, empty where the page has none, so the
   window keeps nothing of its own view. The other Apps that reuse the front
   window (Fleet, Settings, Model Services, LLM Playground, Store, Google
   Workspace, Agent Canvas) read their view args when the window opens; for
   them the window comes forward as the person left it (unchanged).
   "In front" is now confirmed against what was handed over: the page sends
   the window's expected args with its focus request, and the desktop tab
   answers "shown" only once that window's args carry them (within 6 s), not
   merely once a window of that id exists.
2. **`/c/<id>` named the previous chat (intermittent).** Not reproduced: on
   #43 / 0.9.113, a cold load of `/` on a paused Workspace (as in the check)
   and 14 switches over 2.5 minutes moved the URL 50-60 ms after each click;
   on #44 / 0.9.114 the same (below). A cached older Agent frontend is ruled
   out: the instance serves `/package/` from a folder per release (named by
   the release's hash, which is in the file's ETag) with `private, no-cache`,
   there is no service worker, and the App SDK's page is loaded under a URL
   unique to each view. What can still hold the write is in the frame: the
   SDK posted even an immediate commit (`debounceMs: 0`) from a timer, which
   a later debounced commit cleared and replaced with its own 150 ms, and
   posted nothing while a change from the host (an action, a state message)
   was being applied, until that change finished; both fit "corrected itself
   while idle". The SDK (both copies: the UI's `public/app-host.html` and the
   runtime's) now posts an immediate commit as soon as its render is done,
   with no timer and not held by a change in progress. The instance's
   frontend entry is also loaded as `index.js?v=<release>`, so even a stale
   copy held by a browser or a proxy cannot stand in for a newer release.
3. **L1: a Browser page and its desktop window fought over one Chromium
   page's size.** The handed-over page is the desktop window's now: the page
   tab stops streaming it at once (and after a reload in that browser) and
   says "This page is open in a Browser window on your desktop now. That
   window sets its size, so it is not shown here too.", with **Show it on the
   desktop** and **Open a new page here** (a fresh Chromium page at the
   current URL). Another tab already showing the same page is not told.
4. **L2: Open on desktop twice from one Browser page made two windows.** The
   window showing that Chromium page is used again (item 1, by `page_id`).
5. **L3: one chat's suggestions under another.** Suggestions are fetched a
   few seconds after a reply ends; a switch meanwhile put them under the next
   chat. They are now kept with the chat they were made for
   (`chatSuggestions.ts`): fetched for a chat no longer shown, they are
   dropped, and only the chat on screen's are shown.
6. **L4: CORS error to the Hub's `/api/users/me/quotas` from the Agent App's
   origin.** The chat input asked the Hub for quotas from the Agent App's
   frame, which has no Hub session (the call could only fail). The Agent App
   does not ask: its model service enforces the budget, as the conversation's
   own check already said. The Hub's CORS list is unchanged.

Tests (each fails without its change, checked by reverting each source file
to `96523eb6`): `sendToDesktop.test.ts` (a Workspace Terminal page opens a
window on its own shell, the same shell again reuses it, a Browser page twice
is one window, Team Chat's whole view), `desktopTab.test.ts` (the expected
args travel, a window on another shell is not "shown"), `pageLeases.test.ts`
(the page lets a handed-over page go, also after a reload),
`appHostImmediate.test.ts` (the shipped SDK posts an immediate commit with no
timer, past a later commit and during a running action) and pytest
`test_portable_app.py` (the runtime's copy), `chatSuggestions.test.ts`,
`ChatInput.agentApp.test.ts`, `versionedEntry.test.ts`. vue-tsc passes; the
wide vitest run (`src/desktop src/views src/components src/stores
src/composables src/agent`) fails the same 20 tests in 12 files as at
`96523eb6`.

Verified on staging (frontend #44, both hosts `index-C3U_VbOS.js`,
`app-5XTn5IVx.js`; Agent App 0.9.114 on acct3 and acct4; acct4):

- Terminal: a Terminal window open on the agent computer (shell with
  `FIX3-AGENT-1`); `/app/terminal?ptySession=<id>` on the Workspace (no
  `nodeId`, as in the check), marker typed, Open on desktop: a new Terminal
  window on the Workspace showing that shell (`root@modal`, markers 43 and
  44), "in front" confirmed in 0.3 s; Open on desktop again brought the same
  window forward (no new one).
- Browser: `/app/browser?open=https://example.com/` → Open on desktop: the
  desktop window streamed the page (confirmed in 0.8 s) and the page tab
  showed the note instead of the stream; **Show it on the desktop** and Open
  on desktop again: still one Browser window; the page tab reloaded: the note
  again, and the desktop window kept Chromium's toolbar at its own size.
- `/c/<id>`: a cold load of `/` (fresh browser context, Agent page ready after
  the Workspace start), 14 switches 3 s apart: the URL named the chat shown
  50-60 ms after each click, the first click from `/` included; the frame
  loaded `index.js?v=<release hash>` and the SDK with the immediate post; no
  CORS error in the console (L4).
- Suggestions: after a reply in one chat and a switch, the other chat showed
  none of them; the first chat's (about its "OK") stayed under it. The race
  itself is timing-bound and covered by the test above.
- No regression: `/desktop.html` and its windows, `/app/team-chat` → Open on
  desktop (in front), `/app/terminal` on the agent computer, `/c/<id>`.

#### Fixes after the fourth check (2026-11-07)

The fourth independent check found two Medium problems and four Low ones.
Fixed in frontend #45 (UI `ecf2c78d`…`5e616a5c`) and #46 (`9aadd0ce`), and
Agent App 0.9.115-0.9.118 (the Agent's frontend; 0.9.118 also its manifest,
runtime `fd4000a5`). Hub, agents image, controller and node unchanged.

1. **M1: Team Chat's Open on desktop said "in front" while the open window
   kept its old view.** Open on desktop wrote the page's view into the front
   Team Chat window's args (a desktop reload then showed it), but Team Chat
   read its address only when it opened, and "in front" checked only that
   the window existed. Now Team Chat follows its args: a change it did not
   write itself (it remembers the address it last wrote) is opened as an
   address (a conversation with its thread, Activity, Tasks, Search with its
   words, an agent profile; a thread or panel the new view has not is
   closed), and the wrapper switches space when `space` changes. It reports
   the view it shows (`host/shownView.ts`), and the desktop tab answers
   "shown" for Team Chat only once that reported view carries the handed-over
   `space`, `conv`, `thread`, `agent`, `view` and `q` (`desktopShows`), the
   same check Terminal uses on its args. The other Apps that reuse their front
   window read their view args only when the window opens; rather than teach
   each to follow, Open on desktop now brings their open window forward
   unchanged (`reuse: 'front'`, args not laid over, so a later desktop reload
   does not change it either) and says it is shown "as you left it". A page
   with no window of that App open still gets a new window on its view.
2. **M2: the Agent page reloaded itself when the Workspace joined.** Joining
   the Workspace bumps the workspace epoch, and `PackagedApp` re-prepared every
   App frame on an epoch bump ("Resolving installation…", then "Connecting to
   n_…"), though the Agent runs on its own computer and its instance had not
   changed. Now an epoch bump first asks the App's node about the bound
   instance: still `ready` (or `recovered`) with the same App, release and
   generation on the same node, the frame (ready or initializing) is kept;
   replaced, stopped, or not readable, it reloads as before. Safety net:
   unsent drafts are kept per chat (`chatDrafts.ts`): a switch keeps each
   chat's draft with it (the input used to carry text into the next chat), a
   sent message clears it. The Agent's frame gets an origin per connection, so
   its own storage does not outlive a reload: the Agent hands its drafts to its
   host as durable state (`drafts`, now in its manifest's `persistState` with
   `chatId`), the Agent page keeps them in the tab's `sessionStorage` and gives
   them back to a frame loaded again. (A desktop Agent window keeps them in its
   window args, like `chatId`.)
3. **L1: the previous chat's suggestions flashed under the next chat, and a
   chat switched back to showed none.** The suggestion list left with its
   transition after a switch, and the chat-switch watcher closed the
   suggestions after the id had changed, i.e. the next chat's. Suggestions are
   now kept per chat (`chatSuggestions.ts`): the list is keyed by the chat
   shown (removed at once), a switch closes nothing, not being eligible yet (a
   chat still loading) hides them instead of dropping them, and a revert drops
   them only once the message they follow was seen in that chat's list and is
   gone (a chat read again after a switch may show it under another id).
4. **L2: `/app/coding-agents?node=<the Agent's computer>` showed the Workspace
   but kept `node=`.** The page keeps naming a chosen computer while another
   stands in for it, for a computer that is away. A computer the Fleet lists
   that cannot run coding agents (a `pod`, a delegated node, one online
   without the runtime) is no such choice: the computer shown is the view and
   the URL (so Open on desktop uses the Workspace window already there).
5. **L3: the Share dialog's row said "not opened yet" after the visit.**
   Visits are recorded for Mine links (`POST /api/app-shares/<id>/visit`;
   the list counts visitors); the dialog read the list only when it opened.
   It now reads it again when the tab comes back to the front and every 20 s
   while shown.
6. **L4: four 404s on each cold load of `/`.** Two were the page asking the
   Store for `pkg:agent` (its name and icon), which is the deployment's own
   App: it is now named from the built-in registry. One was the Agent's
   welcome mark, a CSS `url()` into `public/` that the build leaves as
   `/public/pantheon.png`, which the Agent's origin does not serve: it is a
   bundled asset now. The fourth, `/package/demo.json` on the Agent's origin,
   is `PackagedApp`'s probe for a bundled demo (a 404 means none); it is
   asked only when the frame has no starting state (`/` with no chat and no
   drafts), and is left as is. On a cold load with the Workspace paused the
   console also shows 409s from `POST /api/fleet/apps/connect`: a side panel
   Text Viewer tab (Workspace-hosted) retrying until the Workspace is up.

Check 3's M2 (`/c/<id>` naming the previous chat, first root session only):
its kept script (`check3/sw.py`) reads `location.href` through `evaluate`,
which is not affected by the stale `page.url` found in check 4; the first
session's script was not kept, so whether it read `page.url` cannot be
confirmed. It was not reproduced since (fix 3, check 4).

Tests (each fails without its change, checked by reverting the source file):
`TeamChatApp.test.ts` (a window follows Tasks, a conversation, a thread;
its own navigation is not followed back; the view reported),
`sendToDesktop.test.ts` (`front` keeps the window's args and says so; Team
Chat's expected view travels), `PackagedApp.epoch.test.ts` (the frame is kept
for the same instance; reloaded for a new generation, release, a stopped or
unreadable one), `ChatInput.drafts.test.ts`, `chatDrafts.test.ts`,
`AgentSurface.test.ts` (drafts kept across a remount, cleared when sent),
`chatSuggestions.test.ts`, `ChatConversation.suggestions.test.ts`,
`CodingAgentsApp.away.test.ts`, `ShareDialog.test.ts` (counts read again),
`appIdentity.test.ts`, `WelcomeScreen.assets.test.ts`; pytest
`test_agent_release.py` (persistState). vue-tsc passes; the wide vitest run
fails the same 20 tests in 12 files as before.

Verified on staging (frontend #45/#46, both hosts; Agent App 0.9.115-0.9.118
on acct3 and acct4; acct4):

- Team Chat: page on #general, Open on desktop: window on #general; page on
  Tasks (`?view=tasks`), Open on desktop: "in front" in 0.3 s and the same
  window showed Tasks; page on #general again: the window went back to
  #general; one Team Chat window throughout.
- Fleet: Open on desktop with no Fleet window: a new one, "in front"; again:
  "Its window is in front in your desktop tab, as you left it there; this
  page's view was not moved into it."
- Cold load of `/` in a fresh browser context with the Workspace paused: the
  Agent was usable at 4-7 s, a draft typed into a chat; the Workspace joined
  at 59 s (#45) and 117 s (#46): no reload (the frame stayed, its text never
  went to "Resolving installation…"), the draft stayed, `/c/<id>` kept; a page
  reload then showed the draft again (0.9.118). Drafts in two chats survived
  a reload, each in its own chat.
- Suggestions: after a reply, a switch showed none of them under the other
  chat from the first sample (40 ms), and the chat switched back to showed its
  own again, twice (0.9.117).
- `/app/coding-agents?node=<agent computer>` → `?node=<Workspace>`, Workspace
  shown; Open on desktop twice: one Coding Agents window.
- Share dialog: a Mine link (Hello Files with MSA Viewer) made, visited by an
  anonymous visitor: the open dialog's row went to "opened by 1 person" without
  reopening it; revoked: the visitor sees "This link was withdrawn".
- Cold load of `/`: no request to `/api/store/packages/agent` or
  `/public/pantheon.png`; a reload of `/c/<id>` had no 4xx at all.

#### Fixes after the fifth check (2026-11-07)

The fifth independent check found one High problem, one Medium and five Low
ones. Fixed in frontend #47 (UI `6493c78b`…`cba6981e`) and Agent App 0.9.119
(the Agent's frontend; the Desktop's Browser launch, runtime `43eb6da9`). Hub,
agents image, controller and node unchanged.

1. **H1: a desktop Team Chat window went back to Tasks, Activity or Search when
   a conversation was opened from there** (every time, also in a new window;
   introduced by fix 4's args watcher). Two causes. A desktop window's args
   go to the desktop session as JSON, which drops an `undefined` value: the
   window cleared `view` locally, the session kept `view: tasks`, and its
   echo put Tasks back. The desktop host now sends a cleared key as `null`.
   And Team Chat compared incoming args with the address it last wrote only;
   one move makes several writes (the panel closed, then the conversation
   opened), and the echo of an earlier write, arriving after a later one, was
   taken for a view handed over. Each write of a desktop window now carries a
   number (`teamChatWrite`, left out of a page's URL); args that come back
   with a number this window wrote, naming the address it wrote with it, are
   its own. Open on desktop changes the address and leaves the number, so a
   view handed over is still followed.
2. **M1: a sent message came back as the chat's draft after a reload.** The
   App SDK merges a state patch key by key, so a chat left out of the Agent's
   `drafts` kept its old draft in the host's state (and in the page's tab
   storage, or a desktop window's args). The Agent now sends a chat whose
   draft is gone (sent, or cleared by hand) as `null`, and the Agent page keeps
   only real drafts (`agentPageDrafts.ts`).
3. **L1: the doc's "usable at 4-7 s" holds only while the Agent's computer is
   up.** Measured numbers are under "Phase 3 as built". While the Agent waits
   for its computer, the page now says "Waiting for your Workspace to start —
   your Agent opens once it is up" instead of "Starting your Agent".
4. **L2: Open on desktop on a Browser page before it had its Chromium page
   made empty Browser windows, one per click.** The button is disabled
   ("Wait until the page has opened") until the page has its page and
   instance. The first Browser page after a Workspace resume failed with
   "chromium unavailable: The native browser tab observer is unavailable",
   and a blank "about:blank" Chromium window with "Restore pages?" stayed on
   the desktop: Chromium was launched, but its tab-observer extension took
   longer than the 5 s the launch waited (a resume's boot storm, a profile on
   the network volume); the launch failed for good (a sticky error until the
   instance restarted) and left its Chromium running and unparked. The launch
   now waits up to 30 s, and if the observer is still late it closes that
   Chromium and launches once more instead of failing for good. Chromium runs
   with `--hide-crash-restore-bubble` (a paused sandbox leaves the profile
   marked as crashed).
5. **L3: `/app/coding-agents?node=<the Agent's computer>` said "Agents on
   n_6c22171e… can use PantheonOS".** The note names the computer by its name,
   "your Workspace", or "this computer"; never its id.
6. **L4: after a revoke, a visitor tab already open went blank.** The visitor
   page reads the link again while the App is shown (when the tab comes back
   to the front, and every 20 s) and shows why it closed ("This link was
   withdrawn by the person who shared it").
7. **L5: in one 40 ms sample, the next chat's suggestions showed under the
   previous chat's messages.** Suggestions are shown only once the message
   list has rendered that chat's own messages (`chatSuggestions.ts`
   `rendered`, set after the DOM update).

Tests (each fails without its change, checked by reverting the source):
`TeamChatApp.test.ts` (the real windows store and desktop host, with a
session that keeps args as JSON and echoes each write late: Tasks, Activity
or Search to #general or another channel, 6 moves, no bounce; a hand-over of
Tasks, a conversation and a thread still followed, and the window's own move
after it kept; fails without the `null` or without the write numbers),
`agentDrafts.sync.test.ts` (the chat input's drafts through the shipped App
SDK and `PackagedApp` to the Agent page's tab storage and to a desktop
window's args: sent and hand-cleared drafts gone after a reload, a new one
kept), `chatSuggestions.test.ts`, `ChatConversation.suggestions.test.ts`,
`computerWords.test.ts`, `HostedAppPage.test.ts` (withdrawn without a
reload), `PageTopBar.test.ts` (Browser hand-over waits for its page),
`AgentSurface.test.ts` and `agentSetup.waits.test.ts` (the waiting text);
pytest `test_browser_profile_ownership.py` (a late observer closes its
Chromium, launches again, no sticky error, the restore flag). vue-tsc passes;
the wide vitest run fails the same 20 tests in 12 files as before.

Verified on staging (frontend #47, Agent App 0.9.119 on acct4, acct4):

- H1: a Team Chat window opened from the desktop icon: Tasks, Activity and
  Search, each to #general and to #c5check, three rounds (18 moves), sampled
  every 80 ms for 2.5 s: no bounce. A desktop reload showed the last
  conversation (the session holds no stale `view`). Hand-over from the page:
  Tasks, #general, Activity and #c5check each shown by the same window, "in
  front" at 0.3-1.1 s; the window's own move afterwards stayed. A thread
  hand-over was not tried live (a channel has no message box until an agent
  exists); the round-trip test covers it.
- M1 on `/`: a message sent, then a reload: input empty, no kept draft; a
  draft cleared by hand (Meta+A, Backspace), then a reload: empty; a draft
  typed, then a reload: back. The same three in a desktop Agent window with
  desktop reloads.
- L1: cold load of `/` with the Workspace paused, right after the 0.9.119
  roll: "Waiting for your Workspace to start" at 3-19 s, "Starting your Agent"
  until 32 s, usable at 35 s.
- L2: the first `/app/browser?open=https://example.com/` after the resume
  opened without an error; Open on desktop was disabled for 23 s until the page
  was bound (two clicks then made no window); once bound, one Browser window
  on example.com, "in front", no "Restore pages?".
- L3: `/app/coding-agents?node=<agent computer>` showed the Workspace, no raw
  id on the page (the note itself shows once per computer and had been shown
  in check 5).
- L4: a Mine link of Hello Files opened by an anonymous visitor, then revoked:
  the open tab showed "This link was withdrawn…" 2 s later, no reload.
- L5: after a reply, 8 switches between two chats sampled every 10 ms: no
  suggestion shown under the other chat's messages.

## The Agent page before the Workspace (2026-11-11)

The owner asked for the Agent page to be usable as soon as the Agent itself is
up, as the old ChatRoom was, not after the whole Workspace has resumed.

**Where things run.** The Agent App runs on the owner's always-on Agent
computer (the `chatroom-<user>` pod, node `agent-<user>`). Its bindings are
`allocator` and `model_services`, both hosted by the controller. Files, Shell,
Desktop, Web, Notebook and Evolution run on the Workspace (a Modal sandbox),
which pauses when idle and takes about a minute to resume.

**Measured on staging before the change (acct4, Agent App 0.9.122).**

| Case | Agent ready | Conversation shown | Workspace Apps ready |
|---|---|---|---|
| Agent computer up, Workspace paused | before load | 4.7-5.3 s | 59 s |
| Agent computer down too, Workspace paused | 26 s | 30 s | 59 s |

- The Agent's readiness does not wait for the Workspace: the controller starts
  it as soon as its own computer is up, and its tools bind on first use
  (`DependencyInstanceProvisioner`), never at start. So with the Agent
  computer up the page already opens the chat list and conversation at once.
- What fails is the first message that needs the Workspace. Asked to run a
  shell command 5 s after load, the Agent's shell binding failed at once
  ("shell is not available yet ... Try again shortly") six times in 23 s,
  and the Agent answered at 28 s that the command never ran; the Workspace
  was up at 59 s.
- With the Agent computer down (after a node replacement, a hub-side stop or
  an explicit Agent pause) the page said "Waiting for your Workspace to
  start" for 17 s while it was the Agent's own computer that was coming
  back, then "Starting your Agent on your Workspace…": the Agent does not
  run on the Workspace.
- The same day the Agent did not start at all on several accounts, for two
  platform faults fixed alongside: the controller's dependency journal was
  full (every grant refused), and a node had lost track of a running Agent
  backend, so every new one failed on its data lock. See "Fixes" below.

**Design.**

1. *The page opens on the Agent alone.* `/` shows the Agent's frontend as
   soon as the deployment's `agent` App is ready, whatever the Workspace's
   state (unchanged; this is already so). Loading the page still starts a
   paused Workspace (`GET /api/chatroom` wakes every node of the session).
2. *A tool that needs the Workspace waits for it, in the chat.* When a
   binding's provider App is meant to run but is not ready yet (not placed,
   or placed and still starting), the controller's allocator answers exactly
   `dependency provider not running yet` before journaling anything (any
   other failure stays the generic one). The Agent's allocated toolset then waits for it:
   it asks again every 3 s for up to 5 minutes, and the chat shows the
   activity "Waiting for your Workspace to start" under the message, then
   "Running tools" once it runs. The model sees nothing but the tool's
   eventual result, and the wait does not count toward the 60 s after which
   a foreground tool call moves to the background. Stop ends the wait. If
   the Workspace is still not up after 5 minutes (paused and nobody started
   it), the tool fails with "your Workspace did not start within 5 minutes
   (it may be paused: open the Agent page or the desktop to start it)";
   nothing ran. Grants, journals and sessions are only made once the
   provider runs (a refused bind writes no record).
3. *The page never says the Agent runs on the Workspace.* While the Agent
   App starts: "Starting your Agent…"; while it waits for its own computer:
   "Starting your Agent — its computer is coming back (this can take a
   minute)…".

**Acceptance criteria (staging, acct4).**

- A1: Agent computer up, Workspace paused: the page shows the conversation
  (chat list and message input) within 5 s of load, p50 and max over at
  least 5 loads.
- A2: a message sent right after load that needs the shell gets its real
  result once the Workspace is up, with "Waiting for your Workspace to
  start" shown while it waits; no failed tool calls in between.
- A3: loading the page with the Workspace paused starts it.
- A4: no page text says the Agent starts on the Workspace; with the Agent
  computer down the page says its computer is coming back.
- A5: no regression: desktop Agent window, Team Chat Pantheon Agent turns,
  Coding Agents.

**As built (2026-11-11).** PantheonOS `b868ceae` (Agent wait, allocator
answer), `ff4b8740` (a placed provider still starting counts as not running
yet: the first live try ended the wait when the shell App started, and three
binds failed before it served), UI `812fce57` (page and Agent window copy, the
"Waiting for your Workspace to start" activity). Agent App 0.9.123, frontend
#50, controller `a96695d7`.

Fixes alongside, on the two faults that kept the Agent from starting that day:

- *Dependency journal* (`os/internal/appgateway/dependency_store.go`): records
  whose grant expired more than a day ago are pruned at load, every 10 minutes
  on a write, and at once when full (the retention is derived in the code
  from how the allocator, the reconciler and model access retry an
  operation); each owner has a share of 2048 records and 256 live grants, so
  an owner that fills its share is refused alone and the gateway logs it. On
  staging the journal went from 477 to 351 records at the first load.
- *Stray App processes on a node* (`os/internal/lifecycle`): a process's
  identity is its Linux start time in clock ticks (the creation time derived
  from the boot time shifts when the clock is stepped, which made a node take
  a live Agent backend for exited, stop it without a signal and start another
  beside it); a stop kills the process group after its grace; processes that
  write to an instance's component log but belong to nothing the ledger runs
  are ended (SIGTERM, then SIGKILL) before a start, after a stop, on
  reconcile, at node start and every minute. Node release
  `fleet-v0.5.1-staging.22`.

**Staging verification (acct4, 2026-11-11).**

- A1 PASS: Agent computer up, Workspace paused before each of 6 loads: chat
  list and input at 4.80, 3.62, 4.31, 3.71, 3.99, 4.17 s (p50 4.08 s, max
  4.80 s); the Agent frame at 2.9-4.1 s.
- A2 PASS: "Use your shell to run: echo WS-OK-42", sent 6.4 s after load:
  "Waiting for your Workspace to start" under the message from 16.8 s to
  about 42 s, then one shell call that ran, answer `WS-OK-42` at 46.5 s. Before:
  six failed calls and "the command never ran" at 28 s.
- A3 PASS: every load started the paused Workspace (billing 31-57 s after
  load, its Apps ready 59-76 s).
- A4 PASS: Agent computer down too: "Starting your Agent…" until the Agent
  was ready at 26 s, no Workspace wording; usable at 31 s (30 s before: the
  time is the Agent's own computer coming back, unchanged).
- A5 PASS: desktop Agent window opened in 4 s and answered "OK" in 9 s;
  Coding Agents listed the Workspace and its sessions; a new Team Chat
  Pantheon Agent (Workspace, Fast) mentioned in #general answered "OK" in
  about 35 s (then deleted with its folder).
- B live: a process planted in the Agent pod writing to a stopped Agent
  instance's log was ended by the node 22 s later.
- Rollout: Agent App 0.9.123 to all 45 staging owners (44 changed, 0
  errors); agents image sha-6b81ada pinned and prewarmed (k8s, Modal).
- Left: an Agent computer that is down still takes about 30 s (its pod comes
  back, then the Agent starts); the allocator's own binding journal also only
  grows (about 47 records a day on acct4, 4096 per owner).

## Choosing the computer in the composer (2026-11-11)

The owner asked for the Agent page to be usable while the Workspace is not
started, and for the composer's computer chip to become a real picker that
also guides an owner with no computer to add one.

**What was there.**

- The chip next to the model tier is `ChatNodeSwitcher` (UI). It showed only
  inside an existing conversation, and only once the Agent answered
  `get_chat_node`. That call first asked where the Agent's Files App runs
  (`agent_nodes.default_node`), which binds Files and so waited for a paused
  Workspace (up to 10 s, then the chip stayed hidden). It showed the raw
  node name (`workspace-377a8b5…`) and no state.
- A conversation's computer is `chat_node` in its memory (none = the
  Workspace, where the Agent's Files and Shell Apps run), passed to the core
  file tools and `run_command` as the context variable `chat_node`; one call
  can name another with its `node` argument (`agent_nodes.route`).
- `route` also resolved the Workspace's node before it looked at the named
  node: the check's call with node `本地` waited 29.5 s for the Workspace,
  then failed "not one of your nodes". The first turn's environment note
  waited up to 5 s the same way, so a first message that needs no tool
  waited for a paused Workspace too.

**Design.**

1. *Nothing waits for the Workspace to know which computer is which.* The
   Workspace's node is the node the Agent's Files App is bound to, or, before
   it is bound, the one node of the owner's fleet with the `fs:workspace`
   capability (the sandbox node). `get_chat_node`, `set_chat_node`, the
   environment note, sub-agent notes and `route` use that; only a tool that
   actually runs on the Workspace waits for it (the existing wait with
   "Waiting for your Workspace to start").
2. *A named computer is checked before anything waits.* `route` looks the
   name up in the fleet list first (node id, name, name without `.local`,
   case-insensitive): an unknown name fails at once with the names it can
   use, an offline computer fails at once.
3. *The Agent lists the computers in under a second, with or without a
   conversation.* `get_chat_node(chat_id)` returns the conversation's
   computer and every computer of the owner except the Agent's own: for each
   its id, a readable name (`Workspace` for the Workspace node; a machine's
   name without `.local`), kind (`workspace` or `computer`), fleet status
   (online, busy, offline), OS, and whether files and commands work there.
   With an empty `chat_id` (the new-chat screen) it answers the same list and
   the default, from any open conversation's agent (or one built once for
   listing).
4. *The picker* (`ComputerPicker`, replacing `ChatNodeSwitcher`) is always in
   the composer's bottom row for a core-tool Agent, on the new-chat screen
   too. The chip shows a status dot and the computer's name. The menu lists
   the Workspace first, then the owner's computers by name, each with its
   state: Ready, Starting, Paused (with Start) or Offline. Choosing one sets
   the conversation's computer; on the new-chat screen the choice is kept
   and applied when the first message creates the conversation, before it is
   sent. Offline computers and computers without files and commands cannot
   be chosen.
5. *Workspace state and Start come from the page's host.* The Agent's
   frontend runs in a packaged-App frame without a Hub session, so the App
   host interface gains a `computers` capability, given to the Agent App
   (`agent`) only: `workspace` (ready, starting, paused, or none, read from
   the Hub's pod status, which starts nothing), `startWorkspace` (the same
   resume Team Chat uses) and `addComputer`. The Workspace node online in the
   fleet list is Ready whatever the host says.
6. *Adding a computer.* "Add a computer" in the menu asks the host to show
   the Fleet App's "Add a node" panel (`FleetAddNode`: OS choice, one-use
   join command from `fleetJoinCommand`, the node appearing when it joins)
   over the page or the desktop. The join token never enters the App's
   frame: it adds a machine to the owner's fleet. On a host without the
   capability the entry opens the Fleet App.
7. *No computer available* (no Workspace and no computer online): the chip
   reads "No computer" and the menu says that tools need a computer, with
   "Start Workspace" when there is a paused one and "Add a computer".
8. *The chat itself never waits for the Workspace.* Loading, reading history,
   sending, and a turn that needs no computer go ahead while it is paused or
   starting; the picker never blocks the composer.

**Acceptance criteria (staging, acct4).**

- C1: the picker shows on the new-chat screen and in an existing
  conversation within the page's 5 s load, with the Workspace paused.
- C2: it lists the Workspace and every fleet node of the account (a test
  node joined to acct4), each with the right state: the Workspace paused,
  starting and ready; the node ready and, after it stops, offline.
- C3: choosing the node makes the next tool call run there (`hostname` /
  `uname` output of the node, not of the Workspace); choosing the Workspace
  again runs it on the Workspace.
- C4: Workspace paused: a question that needs no tool is answered without
  waiting for the Workspace (no "Waiting for your Workspace to start"); a
  tool on the Workspace shows that line, waits, then runs once.
- C5: a tool call naming an unknown computer fails within a second, without
  waiting for the Workspace.
- C6: no computers (Workspace paused, test node stopped): the chip reads "No
  computer"; "Add a computer" shows the join command for this account and the
  staging controller (`https://fleet-staging.aristoteleo.com`).
- C7: the desktop's Agent window has the same picker.
- C8: no regression: Team Chat Pantheon Agent turns, Coding Agents.

### The App page's top bar

Asked alongside: a darker, more modern bar on the Agent page and every
`/app/<id>` page; the "● Connected" text replaced with something useful (the
owner's computers); "Desktop" renamed.

- *Style.* 40 px, flat, near-black (`#0b0d12`, the Agent page's darkest
  surface) with a 1 px bottom line; pill-shaped buttons (26-28 px) with hover
  and focus rings; the light theme keeps the layout on a near-white bar
  (component tokens `--bar-*`). Below 700 px the computer name and text
  buttons other than OS Mode go; below 420 px OS Mode keeps its icon only.
- *Computers instead of "Connected"* (`FleetIndicator`): a pill with one dot
  per computer (the Workspace and the owner's own, five at most, then +N) and
  "N/M online", green when one is ready, amber while one starts, grey when none
  is up. Its menu lists them in the composer picker's rows and words
  (`fleetComputers.ts` builds them with the picker's `computerRows` from the
  Fleet's node list and the Hub's Workspace state), with Start for a paused
  Workspace, "Add a computer" (the same Fleet "Add a node" panel) and "Open
  Fleet". When the page's connection is down the pill turns red and says
  "Reconnecting…"; otherwise the connection is not mentioned. The computer an
  App shows (a viewer's file) is still named beside it.
- *OS Mode.* "Desktop" is "OS Mode", with a grid icon and the tooltip "Open
  the full Pantheon OS desktop"; "Open on desktop" is "Open in OS Mode", and
  the account menu's "Open desktop" is "Open OS Mode".

**Acceptance criteria.**

- T1: at 1440 px and 390 px the bar is 40 px, dark, nothing overflows the
  page width; light theme still readable.
- T2: the pill shows the Workspace and every fleet computer of the account
  with the right states and count; its menu starts a paused Workspace, opens
  Add a computer with the join command, links to Fleet; "Connected" is gone;
  a lost connection shows red "Reconnecting…".
- T3: the Agent page and `/app/<id>` pages say "OS Mode" (no "Desktop"
  button), linking to `/desktop.html`.

### As built, and the fixes after Fast Agent check 1 (2026-11-11)

PantheonOS `a9106b78` (design), `6332093e` (reaper, journal), `f85e2a49`
(Agent: no waiting to know the Workspace, `get_chat_node('')`, `route` checks
first, a run's latest status kept for a page opened mid-run; Team Chat agent
delete), `c402a32e` (listing team), `04868d75` (CI race in a node test),
`018d509b` (the runtime's copy of the App host SDK gets `computers`: the
Agent's isolated origin serves that copy), `839dc20a` (an unknown node's error
does not offer the Agent's own computer). UI `1e03ddb3`, `995be4f1` (top bar),
`ff1d5643`, `a3a07a68` (`pkg:agent` and `agent` get `computers`; the
Workspace shows before the Agent answers), `e35261d8` (summary step "Waiting
for Workspace", phone menu). Live on staging: controller `2274780c`, node
`fleet-v0.5.1-staging.24`, agents image `sha-04868d7`, Agent App 0.9.128 on all
45 owners, frontend #54 (`a3a07a68`).

Fixes after the check:

- *A4.* The Agent window while its Agent's instance comes back says
  "Starting your Agent…" (it said "Starting on your workspace…"). Measured
  with the Agent computer deleted and gone (acct4): page load to pod created
  1.7 s, pod ready 13.4 s, node back and the Agent App recovering 18 s, ready
  28 s, conversation usable 35.9 s; nothing waits on the Workspace. The
  check's 48-58 s came from loading while the deleted pod was still
  terminating (31 s grace: the page first bound to the dying instance). The
  page now asks the Hub every second (was 3 s) for the first 90 s.
- *Low 1.* A run's latest status is kept with the in-flight message
  (`event_store`), so a reload or a second tab says "Waiting for your
  Workspace to start" (2.2 s after a reload mid-wait, measured).
- *Low 2.* The running step reads "Waiting for Workspace" in the message's
  summary and the timeline while its tool waits.
- *Low 3.* `route` looks a node up first: `本地` failed in 10 ms with the
  computers to use (Workspace paused).
- *Low 4.* The stray reaper checks each process's start time again before
  SIGTERM and SIGKILL; a process whose start time cannot be read is never
  signalled. Ledger records without a start time keep the creation-time check.
- *Low 5.* An owner at its journal share loses its own tombstones over an
  hour old at once (more than three times the longest retry that needs
  them); the log says how many records are live grants and how many recent
  tombstones.
- *Low 6.* Deleting a Team Chat agent deletes its Coding Agents conversation
  and, for a Pantheon agent, the Agent conversation it continued (`pantheon
  agent chat --json --chat ID --delete` on its computer); best effort, a
  computer that is away keeps them. acct4's leftover test chats were deleted.

**Staging verification (acct4).**

- C1 PASS: picker on the new-chat screen 4.2-6.6 s after load (0.1 s after
  the message input; the first load after an Agent restart is the slowest), in
  an existing conversation 4.3 s; Workspace paused.
- C2 PASS: picker and top bar list "Workspace · Paused (Start)" and the test
  node "acct4-picker · macOS · Ready"; the Workspace "Starting…" while it
  resumed and "Ready" once up.
- C3 PASS: with acct4-picker chosen `hostname; uname -s` gave
  `Weizes-MacBook-Pro.local / Darwin`; Workspace chosen again gave
  `modal / Linux`.
- C4 PASS: Workspace paused, "17 times 3" answered 7.1 s after sending with no
  Workspace line; a shell command showed "Waiting for your Workspace to start"
  and "Waiting for Workspace" on its step, then ran once (output `modal`).
- C5 PASS: tool result for node `本地` 10 ms after the call.
- C6 PASS: test node stopped, Workspace paused: chip "No computer", the menu
  offers Start Workspace and Add a computer; the Fleet "Add a node" panel
  shows `curl … fleet-v0.5.1-staging.24/install.sh | sh -s -- --controller
  'https://fleet-staging.aristoteleo.com' --join-token '…'`.
- C7 PASS: the desktop's Agent window shows the picker (Workspace · Ready).
- C8 PASS: a Team Chat Pantheon agent on the Workspace answered "OK" in 36.7 s;
  deleting it reported `conversation_deleted: true` and left no Agent
  conversation; Coding Agents listed the Workspace and the Pantheon Agent.
- T1 PASS: 40 px dark bar, no horizontal overflow at 1440 and 390 px
  (`scrollWidth` = width). T2 PASS: "1/2 online", rows and Start as above.
  T3 PASS: "OS Mode" with the grid icon, `/desktop.html`.

## The Agent on its own computer, and kept warm (2026-11-11)

The owner asked why opening `/` still shows "Starting your Agent…" for a long
time: the Agent should open fast and never wait for the Workspace.

**What happens on a visit today (staging, before the change).** The owner's
Agent App runs on their Agent computer, the `chatroom-<user>` pod (node kind
`pod`, node `agent-<user>`); Files, Shell, Desktop, Notebook, Web and
Evolution run on the Workspace (a Modal sandbox, kind `sandbox`). On sign-in
and on `GET /api/chatroom` the Hub creates a missing pod; the node joins the
controller, which then installs (when the release is new to the node),
prepares and starts the Agent App, one reconcile pass per step.

Three things make the wait long:

1. *The Agent computer is usually gone.* Of 45 staging owners only 4 had a
   running pod. The brain pool never reclaims an idle pod, but nothing brings
   one back either once it is gone: the cluster's only node was replaced on
   11-05 (bare pods die with it; the health checker restores only owners
   whose row still said running), the capacity-pressure reclaim sheds the
   least recently active pods when the node fills, and a full release (the
   desktop's Sandbox panel *Pause*, `POST /api/chatroom/release`) stops the
   pod with the Workspace. The sign-in warm-up started only the Workspace
   (the Modal pool), not the pod.
2. *The Agent is placed on the Workspace for some owners.* Its placement is
   empty, so the planner ranks nodes by capability count: the pod (`proc`)
   wins while it is online, but when the pod is gone and the Workspace is up
   the Agent fails over to the Workspace, and from then on its data lives
   there. Five owners had their Agent's data on the Workspace node; their
   Agent waited for the Workspace (60-120 s) on every visit, and a move back
   home could copy an older state the pod kept from before the failover.
3. *A cold pod is slow to give a ready Agent.* Measured for admin
   (pod absent, 11-11 18:52): page load and pod created 0.3 s; volume attach
   and container start 10 s (pod running at 11 s); node online 12 s; the
   controller's first operation 2.5 s later (it waits for its 5 s tick); then
   install (18.7 s, the release was new to that pod), copy data, prepare,
   start, each one or two seconds after the previous ended (a pass follows
   progress after 1 s, an operation still running waits for the next tick);
   the Agent backend's start to ready 2.5 s; the page reads the deployment
   every 4 s while the Agent starts.

**Design.**

1. *The Agent runs only on its own computer.* On a hosted topology (a
   `runner` brain of kind `pod`), the Hub sets the Agent App's placement to
   `{"prefer": ["pod"], "avoid": ["sandbox", "machine", "frontend"]}` whenever
   it saves an owner's deployment (setup, rollout, the follower), so no
   release has to carry it. While the pod is away the controller says
   `waiting for node of kind pod to come online (…)` and the page says the
   Agent's computer is coming back; it never fails over to the Workspace or
   to the owner's own machines. Local mode has no Hub and keeps its own
   placement (one machine takes everything).
2. *Existing deployments move home.* An admin pass
   (`POST /api/admin/agent-setup/placement`, dry run by default) changes only
   the Agent's placement on every deployment (release and pins stay) and lists
   whose Agent is on a Workspace. Such an Agent stops there and moves to its
   pod with its data: the planner carries the state from the node that holds
   its newest data (the recorded data node), not an older revision the pod
   kept. With `start_moving` the pass starts those owners' pod and Workspace
   at once so the move happens now, not on their next visit.
3. *The Agent computer is there before the page asks.* Sign-in warms the
   topology (the pod first, the Workspace beside it) instead of only the
   Workspace; `/` still calls `GET /api/chatroom` at load, before the Agent
   lookup answers.
4. *A pause stops the Workspace, not the Agent.* `POST /api/chatroom/release`
   (the desktop's Sandbox panel *Pause*, scripts) stops the topology's other
   nodes and keeps the Agent computer; nothing stays marked paused, so the
   next entry starts the Workspace again as it did after a full release. The
   Agent stops only on its own (`nodes/agent/pause`, Stop Agent). Observed on
   staging the same evening: a cleanup's release deleted acct4's pod and the
   next load took 36.8 s to a usable chat.
5. *Kept warm while the owner comes back.* The Hub keeps an owner's pod
   while they have been active within `AGENT_POD_IDLE_HOURS` (default 168 h:
   a week). Activity is the page's own (load, engaged and pulse heartbeats,
   synced to the DB every minute); a pod whose health record says it runs a
   task is skipped. Past that the pod is released (checked every 10 minutes from the
   DB, so a Hub restart does not forget anyone); the next visit or sign-in
   creates it again. The capacity-pressure reclaim stays as it is. Cost: a pod
   requests 20m CPU and 600Mi (it uses 0.4-0.85 GB); the staging node (16 GB,
   4 vCPU) holds about 16 next to the platform's own pods, so a week of
   warmth fits the owners who actually come back, and the pressure reclaim
   sheds the longest-idle first beyond that.
6. *A cold pod gives a ready Agent sooner.* The controller passes a fleet's
   deployments as soon as one of its nodes connects (its `/join` or `/token`;
   again 1, 2 and 4 s later while it registers) instead of on the next 5 s
   tick; a pass that made progress is followed after 250 ms (was 1 s); while
   an App waits on an operation its node carries out (install, copy, start),
   the deployment is passed every 400 ms for the first two minutes of that
   state. The page reads the deployment every second for the first 90 s of a
   start (was every 4 s). What remains is the volume attach (about 10 s on
   DigitalOcean) and the Agent backend's own start (about 2.5 s).
7. *No host-drawn copy of the Agent while it starts.* The page keeps its
   "Starting your Agent…" panel with the top bar; it does not draw a chat list,
   conversation or composer of its own. The Agent's frontend is the Agent
   App's, served by the running instance from its own isolated origin
   (`/package/…` of the instance, storage keyed to that origin), so nothing of
   it can render before the instance runs. A copy drawn by the page would be a
   second Agent UI outside the Agent's package (not versioned with it), and its
   cached chat titles and messages would live in the Hub's origin, outside the
   Agent's isolated storage. With the pod kept warm the real Agent is up in a
   few seconds on the visits that matter; a cold pod is the rare case.

**Acceptance criteria (staging).**

- A: warm pod, Workspace paused: chat usable within 5 s of load (p50 and max
  over at least 5 loads).
- B: pod deleted, Workspace paused: the page shows within 5 s and the Agent
  answers a no-tool message within 20 s of load (p50 and max over at least 3
  runs; if 20 s cannot be reached, the measured floor and why).
- C: after the placement pass, no owner's Agent is placed on a Workspace node
  (all deployments listed).
- D: no regression: the desktop's Agent window, Team Chat Pantheon Agent
  turns, Coding Agents, the computer picker, the System Apps follower.

**As built (2026-11-11).** PantheonOS `c33a304b` (controller: kick on
`/join` and `/token`, `Next` 250 ms, `Busy` 400 ms, the avoid-kinds reason,
moving home from the recorded data node) and `9f99f8e7` (a stale
assignment to an avoided node is dropped); hub `44354db` (placement on setup,
`keep_agent_home`, the admin pass, sign-in warms the topology, the resident
idle limit) and `a0e8c89` (the Workspace-only release); UI `85783078` (the lookup reads
every second for 90 s). No Agent App or node release was needed. Live on
staging: controller (build of PantheonOS `codex/cold-agent`), hub image
`455d33f2`, frontend #56 (`302bf17e`); the placement pass ran over all 45
owners (45 changed, 0 errors, 5 started to move).

**Staging verification (acct4 unless named; times from page load).**

| | Before | After |
|---|---|---|
| A: pod up, Workspace paused, chat usable (5 loads) | 3.5-4.5 s (and 36.8 s on the load after a cleanup's release had deleted the pod) | 3.5-4.6 s, p50 3.9 s |
| B: pod deleted, Workspace paused: page shown | 1.6-2.9 s | 1.5-1.8 s |
| B: Agent ready (controller) | 19.0, 25.6, 28.5 s | 16.1, 21.6, 24.0 s |
| B: chat usable | 22.5, 31.9, 32.8 s | 20.8, 26.3, 28.2 s |
| B: answer to a one-word request | 32.7, 41.2, 42.7 s (p50 41.2) | 33.0, 38.6, 38.9 s (p50 38.6) |
| Agent on the Workspace, pod gone, Workspace paused | usable 69.3 s, answer 86.0 s (Agent ready at 58.8 s, after the Workspace) | no Agent is placed there any more |

- Cold pod phases after the change (3 runs): pod created 1.3-1.5 s, running
  6.7-16.2 s (the volume attach dominates and varies), node back and the
  first operation within 0.4 s of it, the Agent's start operation 3.5-4.1 s
  (backend start to ready 2.7-3.2 s), ready noticed 1-1.5 s later, the Agent
  frame 2-2.5 s after that, chat usable 2 s later. Before, the pod-running to
  Agent-ready span was 10.6-13.3 s; after, 7.8-9.4 s.
- B's 20 s answer is not reached: the floor is the pod (7-16 s) plus the
  Agent's start (about 8 s) plus the page (about 4 s) plus the first answer,
  which alone takes 7.8-10.4 s with a warm Agent (two loads: usable at 3.8-4.2
  s, answer at 12.2-15.2 s). The design therefore keeps pods warm so the cold
  path is rare.
- C PASS: all 45 deployments carry the Agent placement; none has its Agent on
  a Workspace node (`coldagent/runs/survey-after.txt`). The four owners whose
  Agent had failed over moved home with their data (`import_data` on their
  pod); one whose Agent never ran was placed on its pod. Agents already on
  their pod were not restarted (the owner's stayed ready).
- D PASS: desktop Agent window open in 8 s, answered "OK" in 9 s; its computer
  picker lists "Workspace · Cloud workspace · Ready"; Coding Agents lists the
  Workspace, the Pantheon Agent and its sessions; a Team Chat Pantheon agent on
  the Workspace answered "OK" 31.1 s after the mention and was deleted with
  its conversation; the System Apps follower kept moving owners (five verified
  ready on 0.9.131 after the pass) and acct4 stayed up to date.
- Left: an Agent whose pod still holds a used instance of the same revision
  restarts that older state when it moves home (the node will not overwrite a
  used instance's state); it was hit only by the reproduction on acct4 (one
  test chat stays in the Workspace's stopped instance). The first answer
  latency (8-10 s for a one-word reply) is the Agent's own and was not
  changed here.

## Fixes after combined check 1 (2026-11-11)

An independent check of the picker, top bar, auto-update and the Agent kept
warm (acct4) found one High, two Medium and several Low issues.

**What was wrong.**

- *H1: a message lost after the Workspace pauses.* In a conversation whose
  shell was bound (it ran a command), every run first probed the shell
  (`_prepare_shell`: `true`, or `cd` into a Team Chat agent's folder) and
  waited up to 120 s for it. With the Workspace paused the allocator answered
  "dependency provider not running yet" every 3 s, the chat said "Waiting for
  model" for 120 s, and the run then returned an error before the user's
  message was stored: the message vanished on the next reload. This broke
  design point 8 ("the chat itself never waits for the Workspace").
- *M1: a tool waiting on a paused Workspace had no way out.* The line
  "Waiting for your Workspace to start" offered nothing; nothing started the
  Workspace (a node-level pause is not undone by the page), and the wait only
  ended after 5 minutes.
- *M2: the Agent page's warm load* was usable at p50 3.8 s but up to 6.3 s,
  and the picker came 5.0-6.0 s after load (C1 says within 5 s).
- Lows: the top bar ignored the light theme; its online count lagged the
  composer picker by about 30 s; a stopped computer vanished instead of
  showing Offline (C2); the Store detail page kept "v1.1.0 is available" after
  the App updated itself; the desktop Agent window showed a raw node id
  ("Backend: n_6c22…") and a restored window for a deleted conversation said
  "Could not load this conversation"; `/app/team-chat` at 390 px had no OS
  Mode icon; the controller logged about 5,000 lines an hour of "maintenance
  deferred: nats: no responders" and its host had no journald size cap (1.8 GB
  of journal, disk 78 %).

**Design.**

1. *A run never waits for its shell.* The run only asks the shell binding to
   check itself before its next command (`AllocatedToolProvider.prepare`):
   the first command of the run (or any later one, if the check failed) runs
   the probe first, with the existing wait for a starting Workspace, then the
   command. A run that needs no command never touches the Workspace.
2. *A message is never dropped.* A run that fails before it starts (its team
   cannot be built, its note cannot be made) stores the user's message in the
   conversation, ends the chat with the reason shown as an error, and answers
   `kept: true`; the user resends to retry.
3. *The wait has a way out and an end.* While a tool waits for the Workspace,
   the chat's wait line has a "Start Workspace" button (the page's host
   `computers` capability), and the page starts a paused Workspace by itself
   once when a wait begins. The wait still ends after 5 minutes (a cold
   Workspace took 3 minutes to come up on staging) with "your Workspace did
   not start within 5 minutes (it may be paused: use Start Workspace …).
   Nothing was executed."; the probe of design point 1 is not retried past
   that.
4. *The warm page and the picker within 5 s* (see M2's findings below).
5. Lows: the top bar's light theme works; the pill and the composer picker
   read the same computer list (one source, refreshed together); a stopped
   computer stays listed as Offline; the Store detail page drops "is
   available" once the App is on that version; the desktop Agent window names
   the computer, and a restored window whose conversation is gone opens a new
   conversation; `/app/<id>` at 390 px keeps the OS Mode icon; the
   controller's "maintenance deferred" line is logged once per owner and kind
   when it starts or changes, then once an hour while it lasts; the controller host's
   journald is capped (`SystemMaxUse=500M`).

**Acceptance criteria (staging, acct4).**

- F1 (H1): in a conversation that ran a shell command, with the Workspace
  paused (`budget.running` empty), a plain question is answered within 15 s of
  sending, with no "Waiting for your Workspace to start"; the message and the
  answer are in the conversation file after a reload. Then a shell command in
  that conversation shows the wait line, the Workspace starts, and the command
  runs once in the right folder.
- F2 (H1): a run that fails before it starts keeps the user's message in the
  conversation and shows an error (unit test; the live path has no
  pre-run failure to provoke).
- F3 (M1): Workspace paused at the node level: a shell command shows "Waiting
  for your Workspace to start" with a "Start Workspace" button; the Workspace
  is started without a click (auto-start) and the command runs once; a wait
  that cannot end (unit test with a short wait) ends the tool with the clear
  error, nothing run.
- F4 (M2): warm pod, Workspace paused: chat usable within 5 s (p50 and max
  over at least 5 loads), and the picker within 5 s of load in each.
- F5 (Lows): each Low above checked once on staging (light theme bar colour,
  pill count equals the picker's after a state change within 5 s, Offline row
  for a stopped node, Store detail after an auto-update, desktop Agent window
  label and a restored dead conversation, 390 px OS Mode icon, controller log
  rate under 100 lines an hour for that message, `journalctl --disk-usage`
  under the cap).
- F6: no regression: C1-C8, T1-T3, desktop Agent window, Team Chat Pantheon
  Agent turn, Coding Agents.
- Out of scope: the cold path B (pod deleted, about 40 s to the first answer)
  stays as documented above; that is the owner's decision.

### As built, and staging verification (2026-11-12)

**What else was found while fixing.**

- *H1 had a second cause in the page.* Once the Workspace had been ready, the
  page sent the Agent's calls through the Workspace's Desktop service
  (`PackagedApp.handleCall`, `rpc_transport: fleet`). After a pause every call
  went to a paused service ("desktop is not running yet") and the frame's
  messages never reached the Agent, in any conversation, until a reload. The
  Agent's calls now always go to its own instance; other Apps fall back to
  their own connection when the Desktop service is not running.
- *A cold Workspace took about three minutes* on staging (Modal sandbox
  creation 2.5-4 min, image prewarmed), so the provider wait stays 5 minutes.
- *A deleted conversation* was reported to the App as "Request failed with
  status code 400": the host now passes an App's own refusal (`{error}`) on,
  and the Agent says `Chat '<id>' not found` when a history is opened.

**Code.** PantheonOS `019582ed` (run without the shell wait, kept message,
provider wait), `b9680568` (offline known nodes, deferral log, history not
found), `59875ab7` (wait stays 5 min), `ff34d1ee` and the hourly reminder
(deferral log per owner and kind); UI `ae0dbe36`, `8246598f` (the Agent never through the Desktop
service), `9b57fa95`/`dbf09ad4` (OS Mode indicator, Agent window tab),
`67d27cea`, `bcb4e512` (deleted conversation), `77d36b82` (test). Live on
staging: controller `52eaf1b6`, journald cap on the controller host, Agent App
0.9.134 (followed by acct4 through the System Apps follower), frontend #62.
Hub image and agents image unchanged.

**Verification (acct4).**

- F1 PASS: chat that ran `echo G1-OK-42; pwd`, Workspace paused (`running`
  empty), "What is 17 times 3?" answered `51` 7.0 s after sending, no wait
  line; after a reload the question and the answer are in the conversation.
- F2 PASS (unit): `test_a_run_that_cannot_start_keeps_the_users_message`.
- F3 PASS: node-paused Workspace, shell command in the same page: "Waiting for
  your Workspace to start" at 9 s, the page started the Workspace by itself at
  the same second (Hub: `waking node 'workspace'`), the command ran once
  (36.4 s) and answered `G2-OK-49`. "Start Workspace" showed beside the wait
  line in the earlier runs (13-21 s). The 5-minute end is unit-tested
  (`ProviderNotStarted`, no retry past it).
- F4: warm pod, Workspace paused. A returning browser (cache, root
  remembered), 6 loads: usable 2.2-2.7 s (p50 2.5 s), picker 2.3-2.8 s; the
  first load in that browser 4.7 s. A new browser every load (no cache, first
  visit), 11 loads: usable p50 3.25 s, picker 3.0-3.7 s for 9 of 11; two loads
  5.5 and 6.3 s, both spent downloading the Agent's 1.6 MB (gzip) GUI from a
  new instance origin (1.5 s for one file). Before: page 1.4 s, usable p50
  3.8 s. Taken: a first visit asks the Hub before drawing either page (page
  shown 0.8 s, was 1.4 s), deployment reads reused for 700 ms, the Agent's GUI
  loads beside its stylesheet.
- F5: top bar in light theme `rgb(251, 251, 253)` (was `#0b0d12`); OS Mode icon
  on `/app/team-chat` at 390 px; a stopped test node listed "Offline" in the
  pill and the picker, both "0/2 online"; the desktop Agent window's badge
  "Agent computer"; a restored window on a deleted conversation opened a new
  one; Store detail line followed by a unit test (read every 15 s while
  pending); controller deferral lines: 5,734 in the hour before; per owner and kind
  every 10 minutes gave 32 lines in 17 minutes, so the reminder is hourly
  (controller `52eaf1b6`); journal 480.6 MB (cap 500 MB), disk 73 %.
- F6: Team Chat Pantheon agent on the Workspace answered "OK" 36.9 s after the
  mention and was deleted with its conversation; Coding Agents lists the
  Workspace and the Pantheon Agent; the picker and pill (C1, C2) as above.
- O1 PASS: the desktop Agent window on a conversation, App > Open in New Tab
  opened `/c/ab41df62-…` showing that conversation.
- O2/O3 PASS at 1440 and 390 px, dark and light: pill with dots (label hidden
  below 520 px), no "Connected", no horizontal overflow; popover lists
  "Workspace · Starting…" with Details (tasks, uptime, Restart, Pause, Copy
  diagnostics, no empty strip) and the test node; O4: same rows and count as
  the App page's pill.

### Follow-ups asked during the fixes (2026-11-12)

**The Agent window opens the Agent page.** The desktop's Agent window had no
"Open in New Tab": `hasOwnTab` only knew packaged Apps and the ready built-ins.
The Agent (`agent`, `pkg:agent`) now has its own tab, and its URL is the
Agent page: `/c/<chat id>` of the conversation the window shows (its `chatId`
argument or saved state, `rootSurface.rootChatUrl`), else `/`. The App menu,
the Dock and the Launchpad offer it as for other Apps; the App page's "All
Apps" list leaves the Agent out (its page is the Agent page itself).

- O1: the Agent window's App menu (and its Dock icon) shows "Open in New Tab";
  it opens the Agent page on the same conversation.

**OS Mode's menu bar uses the App page's computers indicator.** The desktop's
menu bar still had the green "Connected" pill whose popover ("Cloud sandbox")
showed the Workspace's CPU and memory, path, tasks, uptime and restarts,
Restart / Pause (or Resume), Copy diagnostics, with an empty header strip at
its top. It is replaced by the same `FleetIndicator` the App page's top bar
uses (one component, one data source: the Fleet's node list and the page's
Workspace state in `hostComputers.ts`): node dots and "N/M online", a popover
listing the Workspace and the owner's computers with their states (Offline
included), Start for a paused Workspace, Add a computer and Open Fleet. The
Workspace row has "Details", which opens the former panel's content in place
(meters, path, tasks, uptime, restarts, error, Restart / Pause or Resume,
Copy diagnostics, the note), so nothing of it is lost. While the desktop's
Workspace starts, restarts or stops, the pill says so as the old one did
("Workspace starting · 3/9"), and a lost connection is red "Reconnecting…".

- O2: OS Mode at 1440 and 390 px, dark and light theme: the pill shows dots and
  "N/M online" (the label is dropped below 520 px, dots stay), no
  "Connected" text, nothing overflows the menu bar.
- O3: its popover lists the Workspace and the account's computers with their
  states, Add a computer and Open Fleet; the Workspace's Details show the
  meters, path, tasks, uptime, Restart, Pause and Copy diagnostics, with no
  empty strip at the top.
- O4: the App page's pill and OS Mode's pill read the same data (same count
  after a state change).

## Fixes after combined check 2 (2026-11-12)

**What was wrong.**

- *H: Start and auto-start did nothing in a page that had seen the Workspace
  up.* After a pause made elsewhere (a node pause, OS Mode's Pause in another
  tab), the page's Workspace store still said ready. The host's `computers`
  capability asked that store first: `workspace()` answered ready or starting,
  `startWorkspace()` returned at once without asking the Hub to resume, and
  the wait line's auto-start saw "not paused". The tool waited its 5 minutes
  and did not run (2/2); a reload fixed it.
- *M: OS Mode said "Workspace starting…"* (pill and the "Your workspace is
  starting…" card) for a paused Workspace indefinitely, with no Start, while
  the Agent page said "Paused · Start".
- *M: a first visit in a new browser* was over 5 s in 2 of 10 loads, spent
  downloading the Agent's 1.6 MB (gzip) first-paint bundle.
- Lows: the new-chat chip said "Workspace" or "No computer" by timing for the
  same paused Workspace; Workspace Details showed an uptime for a paused
  Workspace; the controller logged "dependency provider not running yet"
  every 3 s while a tool waited; the Store detail line stayed on the old
  version after an update it never saw pending; Coding Agents' computer menu
  left out a paused Workspace.

**Design.**

1. *The Hub decides paused.* `hostComputers.workspaceState()` and
   `startWorkspace()` read the Hub's pod status every time: paused (the
   sandbox released or its node paused) is paused whatever the page's store
   says, and Start always asks the Hub to resume it then; the store only adds
   that a running Workspace is connected. The wait line asks again every 15 s
   while a tool waits (and starts a paused Workspace, at most once a minute),
   the picker every 10 s, the pill every 10 s.
2. *OS Mode reads the same.* The pill drops "Workspace starting…" and its row
   says Paused with Start when the Hub says paused; the setup card says "Your
   Workspace is paused" with Start Workspace instead of "starting"; Workspace
   Details shows no tasks or uptime of the last run, and Resume.
3. *A smaller first paint.* highlight.js with 22 common languages (was all
   190, 1.9 MB), Mermaid and the Team canvas (Vue Flow) loaded when first
   used: the Agent's first-paint bundle 1.57 MB to 1.0 MB gzip.
4. Lows: a paused Workspace is the chip's computer ("Workspace · Paused"; tools
   start it), "No computer" only when there is nothing to start; Coding
   Agents lists the paused Workspace with Start; the controller logs a
   provider-not-running refusal once per owner and method every 5 minutes; the
   Store detail reads its line again on the Store's update notice and every
   minute.

**Acceptance criteria (staging, acct4).**

- G1 (H): an Agent page opened with the Workspace running; the Workspace
  paused from outside (node pause, and the release OS Mode's Pause makes);
  without reloading, a shell command: the page starts the Workspace by itself
  (Hub logs a wake within seconds of the wait line) and the command runs once.
  Start clicked in that state makes a Hub request.
- G2 (M): OS Mode with the Workspace node-paused: pill count (not "starting"),
  popover "Workspace · Paused" with Start, card "Your Workspace is paused";
  the Agent page agrees.
- G3 (M): 10 first-visit loads (new browser each), warm pod, Workspace paused:
  usable within 5 s each.
- G4 (Lows): chip "Workspace · Paused" on 10/10 new-chat loads with the
  Workspace paused; Details without uptime when paused; controller "not running
  yet" lines at most one per 5 minutes per owner and method during a wait;
  Store detail updates by itself within a minute of the update toast (browser);
  Coding Agents lists "Workspace · Paused".

### As built, and staging verification (combined check 2 fixes, 2026-11-12)

**Found while fixing.** The desktop's Pause (`POST /api/chatroom/release`) keeps
the Agent's pod and stops the Workspace without marking it paused: the Hub's
pod status then says `has_assignment: true` with the Workspace node
`NotFound`. That counts as paused too, and a resume of anything but a paused
node asks the Hub's entry (`GET /api/chatroom`) to bring it back.

**Code.** PantheonOS `d01b3881` (criteria), `5ba373e6` (controller log); UI
`ac877922` (Hub decides paused, wait-line recheck, OS Mode card/pill/Details,
chip, Coding Agents, Store notices, smaller first paint), `27024c5e` and
`a4fcddbd` (released or stopped Workspace), `3aadf1ad` (chip before the page
answers). Live on staging: controller `2a2061a0`, Agent App 0.9.136, frontend
#65. Hub and agents images unchanged.

**Verification (acct4).**

- G1 PASS, a page opened with the Workspace up, then paused from outside, no
  reload: node pause, wait line at 13.1 s and 9.0 s (two runs), the Hub woke
  the Workspace in the same second (`waking node 'workspace'`), the command
  ran once and answered at 38.2 s both times; the desktop's Pause (release):
  wait line at 8.0 s, the page's `GET /api/chatroom` 0.8 s later, answered at
  50.2 s. Start in the picker of a long-open page with the Workspace paused:
  the Hub woke it 0.7 s after the click.
- G2 PASS: OS Mode, Workspace node-paused: pill "0/1 online", card "Your
  Workspace is paused" with Start Workspace, popover "Workspace · Paused ·
  Start · Details", Details "The Workspace is paused." with Resume and Copy
  diagnostics (no tasks, no uptime).
- G3 PASS: 10 first-visit loads (new browser each), warm pod, Workspace
  paused: usable 2.62-4.15 s (p50 2.81 s), picker 2.68-4.26 s; the Agent's
  first-paint bundle 1.57 MB to 1.0 MB gzip.
- G4: chip "Workspace · Paused" 10/10 new-chat loads after 3 s (once "No
  computer" for the first moment before the page answered, fixed in
  `3aadf1ad`); Coding Agents' computer menu "Workspace · Cloud workspace ·
  Paused · Start"; controller "not running yet" lines: one per wait (2 in 30
  minutes with two waits); Store detail open on a test App across its
  auto-update (1.0.0 to 1.1.0): "v1.1.0 is available" 42 s after the publish,
  then "Up to date (v1.1.0)" in the same 3 s sample as the toast "… updated to
  v1.1.0" (402 s); after `3aadf1ad`, 5 more new-chat loads: chip "Workspace"
  for a moment, then "Workspace · Paused", never "No computer".
- In OS Mode with the Workspace paused, the card's Start Workspace woke it
  (Hub `waking node 'workspace'` within a second of the click).

## Follow-ups after combined check 3 (2026-11-12)

**What was wrong.** An idle Agent page undid an OS Mode Pause (1 of 3 tries;
check 2 saw it too): about 27 s after the Pause, the page's own connection
(the desktop's Workspace store, which the App page shares) reconnected and
called `GET /api/chatroom`, which wakes every node, and the Workspace started
again. The same store asked `/api/chatroom` again about 90 s after a tool had
started the Workspace (tunnel refresh). Lows: Coding Agents' paused Workspace
row drew no label and a full-width Start, and its trigger said "No computer
runs coding agents"; OS Mode's Store said "desktop is not running yet; it
starts with your Agent deployment" for a paused Workspace; the chip painted
"Workspace" first and "Workspace · Paused" a moment later (7/20).

**Rule.** Nothing starts the Workspace but the user's Start or a tool that
needs it. Being open, reconnecting, polling or looking things up never does.

**Design.**

1. *The Agent page holds the Workspace back for good*
   (`workspace.keepWithoutWorkspace`): its connection is the App bus and the
   Agent's own instance; its reconnects, liveness and lookups never ask
   `/api/chatroom` (the store's `getMyChatroom` refuses on a held page), and
   it sends no sign-in prewarm. Loading the Agent page no longer starts a
   paused Workspace (this replaces A3); the first tool that needs it starts
   it (the wait line's auto-start) or the user's Start does.
2. *Explicit wakes only.* Start and a waiting tool call `Hub.wakeWorkspace()`
   (or the node restart for a paused node), the only Workspace wake allowed
   from a held page.
3. *The Agent's own computer without the Workspace.* When the Agent's pod is
   away (the lookup says its computer is coming back), the page asks
   `GET /api/chatroom?nodes=agent` (Hub): the topology's service pod only,
   the Workspace untouched; at most every 30 s.
4. *The first visit's surface* is decided from the Agent deployment (a read)
   instead of `/api/chatroom`.
5. Lows: Coding Agents shows "Workspace · Paused" in its trigger, a labelled
   row with a small Start, and a "Your Workspace is paused" pane with Start
   Workspace; Workspace Apps that cannot answer while it is paused say "Your
   Workspace is paused. Start it with Start Workspace …", and the Store shows
   one "Your Workspace is paused" notice with Start Workspace; the chip says
   "Checking…" (neutral dot) until the page has said how the Workspace is.

**Acceptance criteria (staging, acct4).**

- P1: OS Mode Pause (release) in one tab with an idle Agent page open in
  another (loaded while the Workspace ran): the Workspace stays paused for
  at least 5 minutes, three times; no `GET /api/chatroom` from the page.
- P2: G1 still holds: a tool in that page after the pause starts the
  Workspace by itself and runs once; Start in the picker wakes it.
- P3: no further `/api/chatroom` from the page in the 3 minutes after a
  tool started the Workspace.
- P4: Agent pod deleted, Workspace paused: loading the Agent page brings the
  Agent back (`?nodes=agent`) and the Workspace stays paused.
- P5 (Lows): Coding Agents trigger "Workspace · Paused", labelled row with a
  small Start; the Store's paused notice with Start; chip never "Workspace"
  before "Workspace · Paused" (10 loads).

### As built, and staging verification (combined check 3 follow-ups, 2026-11-12)

**Found while fixing.** A page that bound to the Agent's instance just before
its pod was recreated waited for "another" instance forever: the node starts
the same instance again (a new generation), and `presetSuccessor` only
accepted a different instance id. A newer generation of the same instance is
now its successor (P4 failed 2 of 4 tries before, 0 of 3 after).

**Code.** PantheonOS `66242576` (criteria) and this section; Hub `8f331aa`
(`GET /api/chatroom?nodes=agent`); UI `60b71a24`, `73da7e0c`, `615ef48c`,
`a1cddfca` (the Agent's computer asked for at load), `752662e3` (successor
generation). Live on staging: hub image `2865d698`, Agent App 0.9.137,
frontend #68. Controller unchanged (`2a2061a0`).

**Verification (acct4).**

- P1 PASS 3/3: an Agent page loaded with the Workspace running and left idle,
  OS Mode's Pause (the Workspace Details' Pause) in another tab, that tab
  closed: the Workspace stayed paused 7.4, 7.4 and 7.2 minutes (checked every
  ~27 s); the Agent page sent no `/api/chatroom` in all three.
- P2 PASS: in that page, a shell command: wait line at 10.1 s, the page's one
  explicit wake (`GET /api/chatroom`, Hub `waking node 'workspace'`) at the
  same second, answered at 46.5 s, ran once.
- P3 PASS: no further `/api/chatroom` from the page in the 3.5 minutes after.
- P4 PASS: Agent pod deleted, Workspace paused, page loaded: pod created 1.4 s
  after load (`?nodes=agent`), chat usable at 16.4, 32.3, 18.0 s (three runs;
  was 28-36 s), Workspace stayed paused, 0 Workspace wakes.
- P5 PASS: Coding Agents trigger "Workspace · Paused", row "Workspace · Cloud
  workspace · Paused · Start" (label 138 px, Start 48 px), pane "Your
  Workspace is paused" with Start Workspace; OS Mode Store: one notice "Your
  Workspace is paused …" with Start Workspace, no "desktop is not running";
  chip on 10 new-chat loads: "Workspace · Paused" (6) or "Checking…" then
  "Workspace · Paused" (4), never plain "Workspace".
- Left by design: opening OS Mode (the desktop, the Workspace's own UI)
  still connects to the Workspace and asks the Hub for it, as before; a
  node-level pause is not undone by that load.

## Fixes after combined check 4 (2026-11-13)

**What was wrong.**

- *H: the Agent page's own entry started a Workspace the desktop's Pause had
  stopped.* `GET /api/chatroom?nodes=agent` assigned only the Agent's pod,
  but its tail then called the topology's `update_activity`, which kicks the
  other nodes. The desktop's Pause is a release: it leaves no pause mark, so
  the kick created the Workspace again about a second after every Agent page
  load (4/4). A node-level pause is marked and was not woken, which is what
  the follow-up's P4 and its unit test (a mocked `update_activity`) covered.
- *M: an open Agent page failed with "Request failed with status code 409"
  after its pod was recreated.* The node starts the same instance again at a
  new generation; the page kept connecting with the old one, which the Hub
  refuses (409), and Retry sent it again. Restored OS Mode Agent windows did
  the same on every load.
- Lows: Workspace Details showed the Agent pod's age as the Workspace's
  uptime; OS Mode showed three paused messages at once (the card, the
  Store's notice and a red line in Files); Coding Agents' trigger cut
  "Workspace · Paused" to "Workspace · Paus…"; the chip said "Workspace" while
  the page had no answer, and for about 24 s after a pause made elsewhere
  (the Fleet keeps the node listed online that long).

**Design.**

1. `?nodes=agent` records the activity on the Agent's own pod pool only; the
   topology's activity (which kicks every node) stays with the plain entry.
2. A connect refused with 409 for this exact instance asks the deployment
   for the instance's current generation (for up to 45 s while the new one
   is not listed yet) and connects again with it; the window keeps the new
   generation, so restored windows do not start stale.
3. Lows: the Workspace's uptime is its own node's age from the Hub's status;
   on the desktop the card is the one paused message (the Store's notice only
   on its App page; Files shows a quiet line, not a red error); Coding Agents
   shows "Workspace" and a "Paused" tag that is never cut; the chip believes
   the Hub's paused over the Fleet's list, and keeps "Checking…" while the
   page has no answer.

**Acceptance criteria (staging, acct4).**

- Q1 (H): OS Mode Pause (release), wait until nothing runs, open `/` in a
  fresh tab, three times: the Workspace stays paused (no "waking node
  'workspace'", `running` empty for 3 minutes); a Hub test drives the real
  `TopologyPool.update_activity`.
- Q2 (M): an open Agent page, the Agent pod deleted: the page reconnects by
  itself (no 409 card), or its Retry works; an OS Mode desktop with a
  restored Agent window loads without the 409 card after a pod recreation.
- Q3 (Lows): Details uptime matches the Workspace node's age; OS Mode with a
  paused Workspace and the Store and Files open shows one paused message (the
  card); Coding Agents trigger not truncated; chip "Workspace · Paused" within
  10 s of an outside pause.

### As built, and staging verification (combined check 4 fixes, 2026-11-13)

**Code.** PantheonOS `7e9a5294` (criteria) and this section; Hub `bd5d1bc`
(`?nodes=agent` records activity on the service pod only; the test drives the
real `TopologyPool.update_activity` and fails on the old code); UI `b2d59224`
(409 generation follow, uptime, one paused message, Coding Agents tag, chip),
`603ef81f` and `441aa829` (no "starting" notices on the desktop, in Coding
Agents or the Store while the Hub says paused). Live on staging: hub image
`b6aaba5f`, Agent App 0.9.138 (built on runtime `c1e9f015`, the full-duplex
proxy fixes included), frontend #71. Controller unchanged (`9701b190`).

**Verification (acct4).**

- Q1 PASS 3/3: the Workspace started, OS Mode Pause (release), nothing running,
  `/` loaded in a fresh browser (usable 2.8, 4.3, 3.1 s): `running` stayed
  empty for the 3 minutes after each load (27/27 reads).
- Q2 PASS: an Agent page and an OS Mode desktop with an Agent window open, the
  Agent pod deleted: over the next 200 s neither showed a 409; the page then
  answered a one-word message in 7.1 s; a fresh OS Mode load afterwards showed
  the Agent window without the 409 card.
- Q3 PASS: Details uptime "5 min" with the Workspace node 324 s old (the Agent
  pod was 260 s); OS Mode with the Workspace paused and the Store open: one
  message, the card "Your Workspace is paused" (no Store notice, no "Starting
  your workspace…"); Coding Agents trigger "Workspace" with a "Paused" tag, not
  cut; chip "Workspace · Paused" 4.1 s after an outside pause.

## Fixes after combined check 5 (2026-11-13)

**What was wrong.**

- *H: a message lost after two Agent pod recreations in a row.* After the
  first recreation the page followed the new generation on its connect (the
  check-4 fix). After the second one its gateway connection was still valid,
  so no connect happened: every call went to the old generation, the node
  rejected it (502, "node rejected App service") for good, and a message sat on
  "Waiting for model" with the failure only in App messages.
- *M: deleting a chat with the Workspace paused did nothing, silently.* The
  Agent refused (`success: false` inside a 200: the controller answers a
  retirement `retiring` while the Workspace's providers are away, and the
  Agent required `retired`), and the chat list ignored the refusal.
- Lows: a red "Your Workspace is paused…" line in the Store beside the card;
  Coding Agents cut the name to "Worksp…" beside its Paused tag; the chip
  kept "Paused" with Start for ~12 s after Start; the paused card could sit
  under a window.

**Design.**

1. *Calls follow the generation too.* A call the gateway answers 502 asks the
   deployment for the instance's current generation (up to 10 s); when it is
   newer, the page connects at it and sends the call again once (the node
   rejected it before the App saw it). A 502 from the same generation is not
   resent. A new generation may sit behind a new gateway origin: the call
   accepts it, and the App's frame is then reloaded on the current instance,
   but only once no call is in flight (a reload must not cut a send off).
2. *A send never fails silently.* A message that did not reach the Agent says
   so in its own conversation ("Your message was not sent … send it again"),
   ends "Waiting for model", and offers its text back to the composer.
3. *Deleting a chat with the Workspace paused works.* A `retiring` receipt is
   accepted: the controller holds the retirement in its journal and finishes
   it when the Workspace is back; the conversation goes now. Any refusal the
   Agent still returns is shown in the chat list, never reported as done.
4. Lows: the Store's errors that only say the Workspace is paused are not
   shown in red (the card or the notice says it, with Start); Coding Agents
   keeps "Workspace" and the Paused tag whole; for 45 s after its own Start the
   page reads a "paused" answer as starting; the card sits above every window.

**Acceptance criteria (staging, acct4).**

- R1 (H): an open Agent page; the Agent pod deleted, ready, deleted again 10 s
  later; a message sent then is answered (no 502 for good), twice; a unit test
  covers two recreations in a row.
- R2 (H): a send that fails shows its failure in the conversation with the
  text offered back (unit test of the path; live when it can be provoked).
- R3 (M): with the Workspace paused, Select → Delete → Confirm on a chat that
  ran a shell command: the chat is gone after a reload, three times.
- R4 (Lows): no red paused line in the Store; Coding Agents shows "Workspace"
  and "Paused" whole; chip "Starting…" within 2 s of Start; the card's Start
  clickable with a window over its corner.

### As built, and staging verification (combined check 5 fixes, 2026-11-13)

**Code.** PantheonOS `043d7578` (criteria), `51afcc91` (a `retiring` receipt
lets the conversation go; test) and this section; UI `0174114c` (calls follow
the generation on a 502, failed-send message with its text offered back, a
refused delete shown in the chat list, the Lows), `fe57e01c` (a new
generation's new gateway origin is accepted; the frame reload waits for calls
in flight) and `a81dfb4a` (the Store's bottom notice does not repeat the pause
in red). Built on frontend #72 (`2e6a387b`). Live on staging: Agent App
0.9.139, frontend #75; hub image `b6aaba5f` and controller `9701b190`
unchanged.

**Verification (acct4).**

- R1 PASS 2/2: an Agent page open on acct4, the Agent pod deleted, ready,
  deleted again 10 s later, ready (generations 29 → 32 → 35, then 35 → 38 →
  41): a one-word message sent in the same page was answered in 7.1 s both
  times, with no lasting 502 and no reload by hand. After the second recreation
  the page reloads its App frame once, by itself, within about 10 s (the frame
  was stable for the next 60 s); a click that lands on that reload goes
  nowhere, and the harness waits for it. Unit tests: two recreations in a row
  (new generation and new gateway origin each time), a call rejected by its
  own instance not resent, the frame reload deferred while a call is in
  flight.
- R2: unit test of `appService` (a call rejected by its own instance is not
  resent); the "was not sent" path itself had no ChatManager test in this round
  (added with the outbox after combined check 6). Not provoked live: after the
  fix no send failed in any run.
- R3 PASS 3/3: Workspace paused (node pause, nothing running), three chats that
  had each run a shell command, Select → Delete → Confirm: no error line, all
  three gone after a reload. Before the fix the Agent answered `success:
  false` ("Conversation retirement is incomplete") and the list ignored it.
- R4 PASS (Workspace paused): OS Mode with the Store open, no red text about
  the pause (before: the bottom notice, rgb(216, 80, 80)); Coding Agents
  shows "Workspace" (69/69 px) and "Paused" (45/45 px) whole; picker Start on
  the Agent page: the dot reads starting and the row "Starting…" 0.44 s after
  the click (before: "Paused" for ~12 s); the card's Start, with 6 of 14
  windows overlapping the card, is what the click hits (z-index 400000).

**Found on the way.** A recreated Agent pod's new generation is served from
a new gateway origin; the check-4 connect refused it ("App gateway returned
an unexpected origin"), so after the second recreation no call could reach
the Agent at all. Accepting the origin after a generation move fixed that;
the frame reload it triggers made test harness clicks land on a detaching
frame ("Frame was detached"). The first R4 pass also found the Store's
bottom operation notice still showing the pause in red; fixed in
`a81dfb4a`.

## Fixes after combined check 6 (2026-11-13)

**What was wrong.**

- *H: a sent message could still vanish without a trace.* After a generation
  move the Agent's frame is reloaded (its gateway origin changed, or the
  backend was replaced, or a lazy module of the old origin failed to load).
  Everything the frame held went with it: a message on its way, and the
  "not sent" line and text of one that had failed. The message was in no fresh
  view either (it had not reached the Agent).
- *M: the Agent pod deleted while the Hub's row still said "starting".* Every
  `/api/chatroom` answered "starting (age=92..296s), recovered to memory"
  without looking at the pod; the pod came back only at the 5-minute creation
  timeout, and sends in between failed ("nats: no responders").

**Design.**

1. *An outbox outside the frame.* Every send is kept in an outbox until the
   Agent's history has its id (the chat call answers, the stream echoes it, or
   a history load shows it). The frame hands the outbox to its host as durable
   state (`outbox`, a `persistState` key of the Agent App, like `drafts`); the
   Agent page keeps it in the tab's storage and an OS Mode window in its saved
   state, and both hand it back to a frame loaded again.
2. *The history decides after a reload.* A frame takes the outbox from its
   first state; entries no send of its own owns are checked against each
   conversation's history once the Agent is connected: there, the entry is
   done; absent and older than 20 s, it is shown as not sent; younger, it is
   looked at again when the 20 s are over (a call in flight at the reload may
   land a moment later). An entry that turns up in a later history load is
   done, notice and all.
3. *A not-sent message is shown from the outbox, not from the loaded
   messages*, under the conversation: why, its text, "Put it back to send
   again" (into the composer, where drafts already survive a reload) and
   "Dismiss". Failures in the frame (a 502, a refusal, no answer within the
   grace) go the same way.
4. *The Hub looks at a "starting" pod before it waits for it.* Creation runs
   under the user's lock, so a "starting" row seen by another request has its
   pod object already. When the provider says NotFound or Failed, the
   assignment recreates the pod at once; Pending, Running or an unreadable
   status keeps waiting, as before.

**Acceptance criteria (staging, acct4).**

- S1 (H): unit tests: a failed send survives a reload of the frame with its
  text; a send in flight at a reload is done when the history has it and shown
  as not sent (text kept) when it does not, after the grace; the Agent page
  keeps and hands back the outbox.
- S2 (H, live): an Agent page open, 2–3 Agent pod recreations in a row, a
  message sent at the times the checker used (right after the last deletion;
  60 s after it): after the frame's own reload, the conversation has either the
  answer or a "not sent" notice with the message's text, never neither; three
  runs.
- S3 (M): unit test (a young "starting" row whose pod the provider reports
  NotFound or Failed recreates it; Pending, Running, Unknown or an error keep
  waiting); live: the Agent pod deleted while the row says starting comes back
  well inside a minute of the next `/api/chatroom` (log "recreating now"),
  not at the 5-minute timeout.

### As built, and staging verification (combined check 6 fixes, 2026-11-14)

**Code.** PantheonOS `3b1e0b92` (criteria), `da730303` (the Agent App's
`persistState` gains `outbox`) and this section; UI `18bb14ba` (the outbox:
`chatOutbox.ts`, `UnsentMessages.vue`, ChatManager, the Agent page keeping it,
the host's latest durable state flushed before a module-failure reload) and
`39d73272` (a deployment-bound App whose preparation failed tries again by
itself); Hub `af4a4fc` (a young "starting" row whose pod is NotFound or Failed
recreates it). Live on staging: hub image `543e4da7`, Agent App 0.9.140,
frontend #77; controller unchanged (`9701b190`).

**Verification (acct4, an Agent page and an OS Mode desktop open).**

- S1 PASS (unit): a failed send keeps its text through a reload; a send in
  flight at a reload is done when the history has it, shown as not sent after
  the 20 s grace when it does not; the Agent page keeps and hands back the
  outbox; the Hub branch (NotFound, Failed recreate; Pending, Running,
  Unknown, an error wait), each test failing on the old code.
- S2 PASS, 4 runs, never neither:
  - 3 recreations, sent 60 s after the last (the checker's run 3): answered in
    6.0 s; the message and its answer in a fresh view.
  - 2 recreations, sent right after the last deletion (run 4): "not sent
    (status code 409)" with the text at 10 s; the frame reloaded by itself at
    +105 s and the notice with its text was still there 20 s later.
  - 3 recreations, sent right after the last deletion (run 5): "not sent
    (status code 502)" with the text at 12 s, still there 4 minutes on.
  - 2 recreations, sent right after (first run): the Agent was away 14 minutes
    (below); once back, the reloaded frame showed "not sent (the Agent
    restarted before it got it)" with the text 4 s after it loaded, 19
    minutes after the send.
  - A full reload of the Agent page kept both not-sent notices with their
    texts.
- S3 PASS: the Agent pod deleted, one assignment (row "starting", new pod
  Pending), the pod deleted again: the next assignment logged "is starting in
  the DB (age=9s) but the provider has it NotFound; recreating now" and the pod
  existed again the same second (before: at the 5-minute timeout).

**Found on the way.**

- After a long outage of the Agent's computer the page stayed on "Node is not
  in this user's Fleet" with Retry until a click; a deployment-bound App now
  tries again by itself after 10, 20 and 30 s, then every minute (`39d73272`).
- Recreating the Agent pod in quick succession sometimes left the new pod
  Pending for 6 to 40 minutes: DigitalOcean's volume driver reported
  "MountDevice failed ... waiting for device" for the workspace volume after a
  successful attach. The Hub recreated the pod at once each time; the wait is
  in the volume layer, outside this round. The outbox kept every message sent
  meanwhile.

## Fixes after combined check 7 (2026-11-14)

**What was wrong.**

- *H: a follow-up queued behind a running turn was lost when the pod went
  away.* The page marked an outbox entry done when the chat call answered
  `success: true`, which it also answers for a message only queued behind a
  running turn. The Agent kept that queue in memory only, and its shutdown
  stops runs as the Stop button does, dropping what is queued behind them: a
  follow-up sent in the last seconds of the old pod's grace reached neither
  the history nor a notice (bursts 7 and 8, a targeted run).
- *M: the Agent page's outbox lived in the tab (sessionStorage) and held 20
  entries in all*: a message sent during an outage and the tab closed was gone
  without a trace, and a burst of failures evicted earlier notices.
- *L: Coding Agents opened ~40 s after a node pause* showed "Workspace"
  without its Paused tag over a cached project view.
- Two not-sent entries of a deleted chat stayed in an OS Mode window's state.

**Design.**

1. *Only the history says a message landed.* The page no longer clears an
   entry on the call's answer or on the stream's echo. It looks at the
   conversation's history (and the Agent's queue) after the call and again as
   the run goes on; an entry is done when the history has its id. One the
   Agent still queues is waiting, not lost; one in neither, with the
   conversation idle and the 20 s grace over, is shown as not sent.
2. *The Agent's queue is durable.* A follow-up queued behind a running turn is
   written to the conversation's metadata (`pending_messages`) and flushed to
   disk before the call answers; the queue keeps that list current as the run
   takes messages in. A Stop by the user still drops what is queued (the page
   shows those as not sent); a shutdown keeps it. When the Agent starts, a
   conversation with queued messages runs them (ids the history already has
   are skipped, so nothing runs twice), and `get_pending_messages` lists them
   until then, so the page waits rather than offers a resend.
3. *The Agent page keeps the outbox in localStorage, per user*, merged across
   tabs (an entry another tab added is kept; one this tab saw go is removed),
   and a frame checks every kept entry against its conversation's history when
   it connects. No count cap evicts an unresolved entry: entries go when the
   history has them, when put back or dismissed, when their conversation is
   deleted (its history answers "not found"), or after 7 days.
4. *Coding Agents* reads the Workspace as paused when its node has left the
   Fleet, whatever the Hub's status says for the first seconds.

**Acceptance criteria (staging, acct4).**

- T1 (H): unit tests: a queued answer does not clear the entry; the page shows
  a not-sent notice only for an id in neither the history nor the Agent's
  queue with the conversation idle; the Agent persists a queued message
  before answering, keeps it on shutdown, drops it on Stop, runs it on start
  and skips an id the history has (no duplicate).
- T2 (H, live): a long answer running, the Agent pod deleted, follow-ups every
  ~3 s through the old pod's last seconds: every follow-up is answered or
  shown as not sent with its text, never neither (checked against the
  Agent's conversation file); no message twice in the history.
- T3 (M, live): a message sent during an outage, the tab closed at once, the
  chat opened later in a new tab: the not-sent notice with its text, or the
  message in the history.
- T4 (L, live): Coding Agents opened ~40 s after a node pause shows the Paused
  tag.
- T5: the OS Mode window's entries for the deleted chat are gone after the
  window's Agent connects.

### As built, and staging verification (combined check 7 fixes, 2026-11-14)

**Code.** PantheonOS `f30ca4e6` (criteria), `9e045060` (the Agent's queue in
the conversation's metadata `pending_messages`, flushed before a `queued`
answer; a shutdown keeps it, the Stop button still drops it; the next start
runs what the history lacks and `get_pending_messages` lists it until then)
and this section; UI `bb2a7d54` (only the history clears an outbox entry; a
look at the history, the Agent's queue and whether the conversation runs
decides each; no count cap; a deleted chat's entries go; the Agent page keeps
the outbox in localStorage per user, merged across tabs; Coding Agents' Paused
tag and pane while the Fleet still lists a paused Workspace) and `f9d7a2be`
(a chat holding only not-sent entries is looked at too on connect; the history
probe passes the Agent's "not found" on). Live on staging: Agent App 0.9.142,
frontend #79; hub image `543e4da7` and controller `9701b190` unchanged.

**Verification (acct4).**

- T1 PASS (unit): the page waits for a queued message (no notice while the
  Agent queues it or the conversation runs), clears it only from the history,
  shows it as not sent when it is in neither with the conversation idle; the
  Agent keeps a queued message on disk before answering and across a real
  App-host termination (the test failed on the old code), runs it on start
  and skips an id the history has; a continuation overtaken by a shutdown
  keeps its messages.
- T2 PASS, 3 runs (the pod deleted without waiting; a long answer, then a
  follow-up every 3 s through the old pod's last seconds), counted in the
  Agent's conversation file: 21 messages, 16 in the file exactly once, 5 (sent
  once the old pod could not take them) shown as not sent with their text;
  none in neither, none twice. In the third run the long turn was cut off with five
  follow-ups queued behind it: all five were in the file and answered by the
  new Agent 16 s after it was up (the queue kept across the shutdown).
- T3 PASS: a message sent while the Agent's pod was gone, its tab closed 2 s
  later; the chat opened in a new tab after the Agent was back: "not sent (the
  Agent restarted before it got it)" with the text after 12 s, and the message
  is not in the conversation file.
- T4 PASS: Coding Agents opened 53 s after a node pause: "Workspace" with its
  Paused tag and the "Your Workspace is paused" pane with Start.
- T5 PASS: the OS Mode window's two entries for the deleted chat were gone once
  its Agent connected, and after a desktop reload.
- Not provoked: the self-retry after "Node is not in this user's Fleet" (no
  outage long enough happened in this round; it is covered by its unit test).

## Fixes after combined check 8 (2026-11-14)

**What was wrong.**

- *M: a second restart call killed a Workspace that had just started.* With the
  Workspace node-paused and two tabs on one chat, a shell command's auto-start
  in one tab resumed it (`POST nodes/workspace/restart`); 41 s later another
  restart call arrived, most likely the other tab's auto-start acting on a
  status read from before the resume. For a node that is not paused the Hub's
  restart is "release, then provision again": the release of the fresh sandbox
  did not confirm in time, the call failed (400) before provisioning, and the
  sandbox exited, leaving nothing running.
- *L: a tab left open on a chat did not show a not-sent notice another tab
  created* until it was reloaded.

**Design.**

1. *The Hub never tears down a node a restart is not meant for.* A restart
   with `?wake=1` (the pages' auto-start: "bring it back if paused") only
   provisions a node that is not paused, it never releases it; and any
   restart within 120 s of the node's last start by the Hub is the same no-op
   (it covers pages that do not send `wake`). A paused node is woken as before,
   and an explicit restart long after a start still rebuilds.
2. *Pages wake only a paused node, once across tabs.* The auto-start sends
   `wake=1`, and a tab that sees another tab of the user asked within the last
   30 s (a timestamp in localStorage) does not ask again.
3. *Tabs share not-sent notices live.* The Agent page listens for the other
   tabs' writes of the outbox (`storage` events) and hands the merged outbox to
   its Agent, which adopts and shows it without a reload.

**Acceptance criteria (staging, acct4).**

- U1 (M): a Hub test: two restarts 40 s apart leave the node running (one
  release); a wake never releases a node that is not paused. Live: the
  Workspace resumed from a node pause, a second restart call (no `wake`) 40 s
  later answers "already running" and the Workspace keeps running; a `wake`
  call on the running Workspace changes nothing.
- U2 (L): a not-sent notice created in one tab appears in another tab open on
  the same chat within a few seconds, without a reload.

### As built, and staging verification (combined check 8 fixes, 2026-11-14)

**Root cause (M).** The pages' auto-start resumes a node-paused Workspace with
`POST nodes/workspace/restart`, sent only when the Hub's status says the node
is paused. The second call came from the other tab on the chat (the only
other client; its auto-start acted on a status read before the first resume
landed). For a node that is not paused, the Hub's restart is release then
provision; the release of the 41-s-old sandbox did not confirm, so the call
failed before provisioning and the sandbox exited.

**Code.** PantheonOS `893e177d` (criteria) and this section; Hub `9501535`
(a wake, or a restart within 120 s of the node's start, keeps a node that is
not paused) and `ef2e972` (also while it is still being provisioned, the
window counting from the node coming up); UI `0d79c397` (the auto-start sends
`wake=1`, once across the user's tabs; another tab's outbox reaches this
tab's Agent through `storage` events; a host copy never brings back an entry
the frame let go) and `252c1061` (those entries also go into what a frame
loaded later starts with). Live on staging: hub image `bcc2b4ab`, Agent App
0.9.143, frontend #81; controller unchanged (`9701b190`).

**Verification (acct4).**

- U1 PASS (Hub tests fail on the old code: two restarts 40 s apart, a wake on
  a running node, a restart while provisioning). Live, through the Hub API
  with no model calls: the Workspace node-paused, woken with `wake=1`; a
  plain restart 5 s later answered "already running or starting" (log: "still
  starting; kept running"); the sandbox took 2.3 min to create; a plain
  restart 86 s after it was up and a wake on it both answered "already
  running"; the same sandbox (`sb-SMmCe…`) ran throughout and 60 s after.
  Found on the way (first live run, before `ef2e972`): with the window
  counted from the restart call, a sandbox that took 4 min to create was torn
  down by a restart 31 s after it was up; that is what `ef2e972` fixed.
- U2 PASS: two tabs on one chat; an outbox entry written by one tab appeared
  in the other without a reload within its first 250 ms poll. During an
  Agent outage the other tab shows "Reconnecting" (no frame to hand it to);
  it showed the not-sent notice as its conversation came back (79.5 s and
  127 s after the send, the pod back at ~60 s), still without a reload.

## Open decisions

1. **Path URLs (`/app/<id>`) or hash?** Path: one install scope per App and
   readable links; one nginx and one dev rewrite.
2. **Move every App's durable state out of window args, or only Apps that
   opt in?** Opt-in (Coding Agents, Canvas first); others keep window args
   until they need it. Every App page still gets a key for `page.*` (panel).
3. **URL default on an App page: system browser or Pantheon Browser?**
   System browser (as today), Pantheon Browser in the link menu and as a
   setting.
4. **Side panel limits.** 20 tabs, 8 kept mounted, default width 40 %,
   overlay below 900 px; `Ctrl/Cmd+\` to collapse — checked free: the key
   reaches the page, unclaimed, in Chrome and Firefox (Playwright, Chromium
   156 and Firefox 157, macOS).
5. **`/` for deployment users becomes the Agent page (Phase 2)?** Yes; the
   desktop stays at `/desktop.html`, one click away in the top bar.
6. **Visitor viewers**: owner lists them per share (`share.viewers`), not
   every installed viewer. Taken (Phase 3).
7. **Installable packaged Apps** (Hub endpoint reading the App index): built-ins
   first, packaged when asked. Taken (Phase 3): packaged Apps from their public
   Store listing, not the App index.
8. **App class from the manifest, or an explicit flag on every App?** Derived
   (backend, `fs`, `placement.requires`), with `standalone.access: "account"`
   to tighten only; no flag can make an App with a backend `public`.
9. **Viewers reading a packaged App's private data folder?** Deferred until
   an App needs it (owner's decision, 2026-11-06); the viewer says the file
   is in the App's own storage (see "Fixes after the second check").
