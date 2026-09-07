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
owning `.qpproj` project. The first integration exposes the GUI and the
`status`, `focus`, and `close` desktop actions, not a separate analysis RPC API.

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
