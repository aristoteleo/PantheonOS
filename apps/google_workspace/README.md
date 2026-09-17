# Google Workspace

Shell-hosted integration for Google Docs, Sheets and Slides. The Vue frontend
lives in `pantheon-ui/src/desktop/apps/google-workspace`.

Users connect their Google account with a real-browser OAuth popup and choose
files with Google Picker (`drive.file`). Optional **Browse all Drive files** asks
for `drive.metadata.readonly` to list all file types and folders, with folder
navigation, search and pagination. This additional restricted scope must be
configured and verified in Google Cloud as applicable. The app creates native
Workspace files and opens Google in an Atrium Browser window by default; users
can choose an external browser tab instead. The sandbox browser has a separate
Google website login; OAuth does not sign it into Google. No cookies or tokens
are transferred to it. A working sandbox and successful Google login there are
required for internal editing. Google Drive remains the
source of truth. This app does not upload local documents or replace Office.

The site administrator configures a Web OAuth client ID once. Users only click
Connect Google and authorize through Google's popup; no personal API key is
needed. For Add from Drive, optionally configure a browser-restricted Picker
API key and the same project's numeric project number. Use deployment variables for
all users, or Google connection → Administrator setup for browser-local testing:

- `VITE_GOOGLE_WORKSPACE_CLIENT_ID`
- `VITE_GOOGLE_WORKSPACE_API_KEY`
- `VITE_GOOGLE_WORKSPACE_APP_ID`

Enable Drive, Docs, Sheets, Slides and Google Picker APIs; authorize the Pantheon frontend's exact
origin. Never supply a client secret to the frontend. Access tokens are retained
only in the window's memory, cleared on disconnect, expiry, window close, or
Pantheon account changes. Reconnect after refreshing the desktop. Disconnect
ends this local session; it does not revoke the Google application's grant.

Google authentication is disabled in Tauri WebViews. Use the web desktop in a
regular browser for this first version. Connecting and granting file access are
user-driven operations. Read the UI repository's `docs/google-workspace.md` for
setup and verification instructions.

Agent support is exposed through the app's live desktop bridge. `interface`
describes the 21 actions; `SKILL.md` explains file discovery, scoped authorization,
native content reads, targeted edits and verification. Drive supports search,
metadata, native creation, copy, folders, rename, move and reversible trash.
Docs and Slides support structured reads and revision-guarded batch updates;
Sheets supports metadata, range values, formulas and structural batch updates.
These use the user's existing `drive.file` grant without passing OAuth tokens to
the agent or depending on the Google editor's browser login. Files not yet
authorized must be selected with Add from Drive. Selecting a folder does not
grant recursive content access. API editing supports native Google formats.

The app window must remain open and connected during agent operations. The full
Drive listing scope grants metadata only and is optional for agent content
operations. There is no arbitrary HTTP, sharing, ownership or permanent-delete
action. Read `SKILL.md` before writing; timeouts may have an uncertain outcome,
and Sheets has no atomic revision guard. Verify writes by reading back.
