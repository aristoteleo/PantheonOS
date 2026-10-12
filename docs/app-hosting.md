# Hosting an App for others (2026-10-11)

An owner turns one of their Apps into a link. Whoever opens the link uses the
App in their own browser, with no Atrium desktop around it.

## Modes

| Mode | Visitor signs in | Runs on | What the link is |
|---|---|---|---|
| `frontend` | no | nothing (static) | The App's frontend, served from its pinned Store version |
| `host` | optional (owner's choice) | the owner's running instance | A visitor grant to that instance, limited to what the App declares shareable |
| `install` | yes | the visitor's own workspace | An offer to install the pinned Store version and open it on the visitor's desktop |

* An App without a backend is always `frontend`.
* An App with a backend can be `host` only if its manifest declares a `share`
  section (below); otherwise it can only be offered as `install`.
* `install` and `frontend` pin the Store version current at hosting time; `host`
  follows whatever instance the owner runs (an upgrade or restart keeps the
  link working).

Every link has an optional expiry (none means until revoked), can be revoked at
any time, and records visits (who, when; anonymous visitors by session).

## What a visitor can reach (`host`)

The owner's grant reaches an instance's whole `/rpc` surface, `/_fleet/fs`,
`/package/*` and served files, and carries the owner's credential. A visitor
must never get that. A **visitor grant** is a separate gateway grant kind:

* **No owner credential.** The gateway forwards the visitor's credential in
  `X-Pantheon-App-Token` instead (below) and never the owner's.
* **Only declared methods.** `POST /rpc` passes only for methods listed in the
  App's `share.methods`; argument names are checked like dependency grants
  (`internal/appgateway/dependency.go`). The list is read by the controller
  from the *installed* manifest of the bound revision at grant time, never
  taken from the Hub or a browser.
* **Only declared paths.** `GET/HEAD` for `/package/*`, the host page and its
  assets, plus `share.paths` prefixes the App declares (e.g. served files).
  `/_fleet/fs`, `/__fleet/model-media`, tunnels and anything else: 403.
* **Same embedding rules as today.** The visitor page is an Atrium origin
  (`/#/a/<id>` on the Hub frontend; the Hub web app uses hash routing), so the existing ticket → partitioned cookie
  → iframe flow and Origin/Referer checks apply unchanged.
* **Bounded.** One-hour grants renewed by the visitor page while the share is
  live; a revoked or expired share stops renewal and the Hub asks the
  controller to drop its grants. Per-share visitor and request rate limits.

### Manifest

```json
"share": {
  "methods": [{"name": "render", "arguments": ["spec"]}, {"name": "status", "arguments": []}],
  "paths": ["/files/"],
  "description": "Anyone with the link can render plots; nothing in your workspace is exposed."
}
```

The owner sees `description` and the method list when hosting, plus a warning
when the App's placement includes `fs:workspace` or `proc`.

### Who is using it

Per-visitor state is the App's business (owner's decision: no state isolation
by the platform). The App learns who is calling:

* **Backend:** `X-Pantheon-App-Token` on every request through a visitor grant
  is a Hub JWT with `scope: app_visitor` and claims
  `{share_id, binding, visitor: {kind: user|anonymous, id, username?}}`.
  `GET /api/app-shares/visitor` (Bearer that token) confirms it. Anonymous ids
  are stable per browser for the life of the share (a random id kept by the
  visitor page). The portable runtime exposes it to methods as `ctx.visitor`.
* **Frontend:** the boot message carries `visitor` the same way, and `host:
  {share_id, mode}`; `fs` is not offered.

An App that ignores this simply serves everyone the same state.

### Keeping the instance up

Hosting sets `keep_alive` on the bound instance (restored on revoke, unless the
owner had set it). A stopped or replaced instance makes the visitor page show
"temporarily unavailable" and retry; it resolves the owner's *current* ready
instance of the same App (deployment App by name, or node + app_id + scope).

## `frontend` and `install`

* `frontend`: the Hub serves the pinned Store version's frontend files under
  `/api/app-shares/<id>/package/…` into the visitor page's sandboxed iframe
  (`allow-scripts` only: an opaque origin, never the Hub's). No `call`, no
  `fs`; state lives in the visitor's browser.
* `install`: the visitor signs in, sees name, version, publisher and the App's
  requested capabilities, and confirms. The Hub records access to that exact
  version (also for a private Store repository), then opens the visitor's
  desktop with `?install=<package>@<version>`, which runs the existing Store
  install flow and opens the App.

## Data

Hub table `app_shares`: `id` (URL token, 128-bit), `owner_user_id`, `mode`,
`require_login`, `allowed_users` (optional usernames/emails for `host`),
`target` (`{deployment, app}` or `{node_id, app_id, scope}`), `package_id` +
`version` (`frontend`/`install`), `title`, `expires_at`, `revoked_at`,
`created_at`, plus `app_share_visits` (`share_id`, visitor kind/id, first/last
seen, count).

## API

Owner (login): `POST /api/app-shares`, `GET /api/app-shares`,
`PATCH /api/app-shares/{id}` (expiry, allowed users), `DELETE` (revoke).

Visitor: `GET /api/app-shares/{id}` (public summary; 404 when revoked or
expired), `POST /api/app-shares/{id}/connect` (optional login; returns the
gateway origin + ticket for `host`, or the package URL for `frontend`),
`POST /api/app-shares/{id}/install` (login; `install`).

Controller: `POST /apps/connect` gains `visitor: {share_id, ...}`; the
controller then reads `share` from the installed manifest and creates a visitor
grant (refused when the App declares none). `POST /apps/revoke-share` drops a
share's live grants.

## UI

* Desktop: window menu and Store "My apps" → **Host…**: mode, sign-in required,
  allowed people, expiry; the link with Copy; a list of hosted Apps with visits
  and Revoke.
* `/#/a/<id>`: a page with just the App (title bar with the owner's name, the
  App, a sign-in prompt when required), or the install confirmation.

## Phases

1. `frontend` mode end to end (smallest; no gateway change).
2. `host` mode: visitor grants in the gateway, manifest `share`, visitor
   identity, keep-alive, owner UI.
3. `install` mode.

Status 2026-10-11: phase 1 live on staging (hub 957773d+, ui 9a9da2ff+).
Hosting Game of Life 1.1.25 as a link runs it at `/#/a/<id>` for anonymous
visitors; sign-in-required links answer 401 until the visitor signs in;
visits are counted; revoke closes the link and its files. The link is built
from the page origin, so a desktop app build must use the Hub web URL instead.

Status 2026-10-12: phase 2 live on staging (controller visitor grants, hub
4e2ac92+, ui 5336f7d3+). Hello Host running on a Mac node, hosted from App →
Host…: signed-in and anonymous visitors call `greet`/`guestbook` and the
backend sees their identity (`ctx.visitor()`); `secret`, undeclared arguments,
`/_fleet/fs` and `/__fleet/model-media` answer 403; Revoke turns a live
visitor connection into 401 within one request and the link into 404. The
visitor header is ASCII (JSON `\u` escapes) so non-Latin names survive.
