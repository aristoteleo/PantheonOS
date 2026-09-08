# QuPath in Atrium

The QuPath desktop app runs the official Linux QuPath GUI in the workspace.
Atrium adopts its main X11 window through the same seamless Xpra session used
by Browser. QuPath can start without launching Chromium. Menus and dialogs are
native QuPath windows, and files remain in the workspace.

## Using the app

- Open **QuPath** from the desktop or launcher. Clicking its launcher again
  focuses the existing window.
- Open a `.qpproj` file from Files, or choose **Open with → QuPath** for a
  supported image. Each newly opened Atrium window has its own native session.
- Use QuPath's File menu to open further images or projects in that session.
  Changing the desktop window's `path` cannot replace unsaved work; open a new
  window when using the desktop file routing instead.
- Closing the Atrium window requests QuPath's normal close. Its Save/Cancel
  dialog remains usable; the Atrium frame disappears only after QuPath closes.
- Reloading the webpage reconnects to the existing session. Restarting the
  workspace stops native processes, so save annotations/projects beforehand.

Standalone `.qpdata` files are not registered for direct opening; open their
owning `.qpproj` project. Agent control uses the shared Desktop interface described below.

## Agent control

The app ships [a discoverable QuPath skill](SKILL.md), declared by `skill` in
`app.json` and included in the Pantheon app package. Agents discover the path
through `desktop_apps()` and read it before scripting. It is an app-owned
skill, not a personal Codex installation.

QuPath uses the same public tools as other desktop apps:

- `desktop_windows`, `desktop_open`, and `desktop_read` select a window and
  inspect its actual session state.
- `desktop_screenshot` and `desktop_act` provide native-window visual and
  keyboard/mouse interaction.
- `desktop_call` routes `status`, `focus`, `close`, `run_script`, and
  `script_status` to that window. There is no separate `qupath_*` tool family.

The script bridge runs Groovy in the **existing QuPath process**. QuPath's
script engine receives the current GUI project and image, so scripts can
inspect and modify unsaved annotations. The GUI state includes an opaque
`image_token`; scripts can require it with `expected_image` so a queued
operation fails if the user switches images before it starts. Use worker scripts for analysis and
short JavaFX scripts for view changes. Results contain a request id, lifecycle
state, bounded output, and JSON-compatible return value. A wait timeout leaves
the request running; query its id instead of submitting the mutation again.
Read-only, export and save scripts should pass `update_hierarchy: false` to
avoid marking the image as changed through an automatic hierarchy refresh.
The default is `true` for annotation/analysis updates; opting out never clears
pre-existing unsaved changes.
The skill documents this contract and links to small
[inspection, annotation, and export examples](references/scripting.md).

This exposes QuPath's installed scripting capabilities rather than claiming
that every plugin or workflow has a dedicated wrapper. Models, extensions and
analysis parameters still depend on the workspace installation and the data.
Screenshots show the rendered view; quantitative analysis reads image data.
Saving exports is separate from saving the image/project's working state.
JavaFX's native key path cannot reliably enter non-BMP characters such as many
emoji. Those batches fail before input; the skill provides an explicit script
for entering Unicode into a verified focused text field in the same GUI.

## Runtime

`docker/install-qupath.sh` installs
[QuPath v0.7.0](https://github.com/qupath/qupath/releases/tag/v0.7.0) and its
bundled Java runtime at `/opt/qupath`. The Linux amd64 archive's SHA-256 is
pinned in the installer. JavaFX uses software rendering for the virtual
display; the default heap limit is 2 GiB unless `JAVA_TOOL_OPTIONS` already
specifies `-Xmx`. Larger slide workloads may require a larger workspace and
heap. QuPath preferences live under the workspace's `.pantheon/qupath`; cache
and temporary files remain on local sandbox disk.

The launcher uses the documented [`--quiet`, `--image`, and `--project`
options](https://qupath.readthedocs.io/en/stable/docs/advanced/command_line.html).
Quiet launch skips first-run/update prompts. Installation happens at image
build time, never when the user opens the app.

`apps/desktop/native_apps.py` owns the process and stamps only its main window
with `pantheon-native-qupath-<session hash>`. The stable Atrium window id is
the session key; transient Xpra window ids are not persisted as process
identities. File arguments are restricted to workspace roots and passed as
argv, never through a shell. Startup failure cleanup terminates only the
process group it created. A running app is closed with `WM_DELETE_WINDOW`.

The frontend host is `src/desktop/apps/qupath/QuPathApp.vue` in pantheon-ui.
It shares `xpraLib`, reserves the managed native window class, and leaves
ordinary dialogs to `XWindowApp`. Native close handling is deferred so QuPath
can confirm saving without losing its Atrium host.

## Verification

Backend: `tests/test_native_apps.py` plus the existing Browser/native-window
and desktop-session tests. Frontend: QuPath/store tests and existing
Browser/Xpra tests. `scripts/check-native-qupath.mjs` in pantheon-ui tests the
real Vue host and Xpra stream against a scratch sandbox using a JSON transport
bridge to the actual desktop toolset. The fixture must have no user volumes
or Hub registration; it covers launch, menus, geometry, reconnect, reload,
and native close without opening windows in a user's session.

`tests/test_qupath_guard_live.py` is a manual, isolated QuPath/Xvfb acceptance
runner. It uses synthetic images to verify stable image tokens, idempotent
mutations, image replacement while worker/JavaFX requests are queued, request
expiry during a blocked JavaFX event loop, and the empty-image state. It closes
its own GUI normally and stops only the Xvfb process it created.
