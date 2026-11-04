# Opening and sharing an App: one model (2026-11-04)

**Status (2026-11-04):** live on staging only (hub 5b17d7c, image 4f027f50;
ui abc848a6). Verified on staging: H1–H8. Not on prod.

**Status (2026-11-04, fix round):** hub `9461ef3` (image `1a0cfbc9`), ui
`617394b1` (frontend `aebb2f1c`). A Mine link records whether it turned the
instance's keep-alive on (`keep_alive_by_link`, kept with the link's target
and never sent to the controller); revoking the last live such link turns it
off again (verified: off → Mine link → on → revoke → off). Revoke asks
first; Shared links lists refresh when a link is made or revoked in any tab
and on focus; Escape closes Share…; a closed `/#/a/` link says why, as `/#/s/`.

Three entries grew separately and overlap: "Open in Own Tab" / "Open in New
Tab", "Host…" (modes `frontend`, `host`, `install`) and "Invite people…".
They answer two questions only, so the UI shows two entries.

## Two questions

1. **Me, elsewhere:** "Open in New Tab" opens this App full page
   (`/app/<id>`, with the window's args). It is a view of my own App, never a
   link for others. One name everywhere: App menu, Dock, Launchpad, Store.
2. **Someone else:** **Share…** opens one dialog that makes one link. The
   link always lands on the guided page (`/invite/<id>`, `docs/apps/invite-links.md`),
   which runs only the steps that link needs and ends on the App.

## The Share dialog

One question, asked only when there is a real choice:

| Choice | Meaning | Offered when | Was |
|---|---|---|---|
| **Their own** (default) | They use the App on their own account: their account, Workspace or computer, install, setup, as the guided page does | always | Invite people…, Host `install`, Host `frontend` |
| **Mine** | They use my running instance, limited to what the App declares shareable (`share.methods`/`paths`); nothing else of mine | the App has a `share` section | Host `host` |

* A `public` App (no backend, no account) under "Their own" needs no steps:
  the guided page opens it at once, signed out, served from the pinned
  release (what `frontend` mode did). So `frontend` is not a choice anymore.
* Common options below the choice, with defaults: expiry (14 days), who
  (anyone with the link / only signed-in people / listed people), and for
  "Their own" the trial credit and max sign-ups (shown only when the App needs
  an account). The pinned release for packaged Apps is shown, not asked.
* Result: the link with Copy, and **Shared links**: every link this account
  made, both kinds, in one list (App, kind, created, expiry, opened / signed
  up / reached the App, Revoke). The same list is in Settings → Shared links
  (replacing `SharesListPage`), and the dialog shows this App's links.
* "Copy link" on a window or side-panel item stays: it copies a link to that
  item for me (`/app/<id>?open=…`), not a share.

## Links

* New links: `/#/s/<id>` → the guided page for both kinds. "Mine" links run
  the Account step only when the link needs sign-in, then the visitor view.
* Existing `/#/a/<id>` (host shares) and `/#/i/<id>` (invites) keep working
  and land on the same page.

## Backend

One owner API over both tables, so the UI has one list and one create:
`POST/GET /api/app-links`, `PATCH/DELETE /api/app-links/{id}`, with `kind:
own|mine` (`/api/shares` is the data-share feature's), plus a public
`GET /api/app-links/{id}` for either kind that also says why a closed link is
closed. `own` is stored in `app_invites`, `mine` in `app_shares` (no data
migration). Visitor endpoints stay as they are. Old owner endpoints stay for
existing callers.

## Removed from the UI

"Host…", "Invite people…", "Open in Own Tab", the Host dialog's mode picker
(browser / my instance / visitors install it), the PageTopBar invite button
(replaced by Share…).

## Acceptance criteria (staging)

* H1 The App menu, Dock, Launchpad, Store "My apps" and the App page top bar
  show only "Open in New Tab" (where an own page exists) and "Share…". No
  "Host…", "Invite people…" or "Open in Own Tab" anywhere.
* H2 Share… on Coding Agents shows no kind choice (no `share` section), makes
  a "Their own" link; a stranger completes it as in invite-links I2–I7.
* H3 Share… on an App with a `share` section (Hello Host) shows Their own /
  Mine; a "Mine" link lets a signed-out visitor call the shared methods only
  (403 for others), and no Workspace or install step appears.
* H4 Share… on Game of Life makes a link that opens the App signed out with no
  steps.
* H5 Shared links (dialog and Settings) lists links of both kinds with counts;
  Revoke works for both and the link then shows why it is closed.
* H6 Old `/#/a/<id>` and `/#/i/<id>` links still work.
* H7 Open in New Tab from the App menu, Dock and Launchpad opens `/app/<id>`
  with the window's args.
* H8 An invite with no sign-ups left still lets anyone with an account,
  among them those who signed up through it, sign in and resume their steps
  from the link; only creating an account through it is refused.

## As built (staging, 2026-11-04)

* `/#/s/<id>` forwards to `/invite/s/<id>`, which asks `GET /api/app-links/<id>`
  where the link is kept and opens the guided page as an invite or as a
  hosted link. A "Mine" link shows the Account step only when it needs a
  sign-in (signed-in or listed people), then opens the visitor view
  `/#/a/<id>`; with no sign-in it goes there at once.
* The Share dialog: Their own / Mine (only with a `share` section), expiry
  (default 14 days), for Mine who (anyone / signed-in / listed people), for
  Their own the note, max sign-ups and trial credit (not for a public App).
  "Who" is not offered for Their own: those links always end on the
  visitor's own account. A public App's link is kept as an invite that
  opens a browser-only hosted link of it; that hosted link is not listed
  apart and is revoked with the invite.
* Shared links are in the dialog (this App's) and in Settings → Shared links
  (all, with closed ones). Older hosted `install` and browser links are listed
  as Their own. `SharesListPage` stays: it lists data shares (files), which
  are not App links.
* Mine needs the running instance: the App menu passes its window's, the Dock
  its window's, the Launchpad and Store any open window of the App.
* An App whose `placement.requires` is only `dom` (a browser) counts as
  public, so Game of Life opens signed out.
