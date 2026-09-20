# Native streaming on Fleet nodes

Browser and QuPath use ScreenCaptureKit on macOS and Windows Graphics Capture
on Windows. Linux continues to use Xpra. The Desktop uses the same Fleet node
selection, service gateway, instance identity and lifecycle for all three.

## Node setup

Install a Fleet build that includes `fleet-native-capture`. The macOS helper is
bundled and signed inside Fleet.app; the Windows installer downloads the helper
next to fleet.exe. Updating only the Desktop frontend or copying an older Fleet
binary does not enable native capture.

Native streaming needs an interactive, logged-in desktop on the selected node.
It is not a headless display server. The applications also have real windows on
that machine. Locking the machine, logging out, or sleeping its display can pause
capture; unlock the node and retry. Windows Session 0 services and UAC secure
desktop capture are not supported.

On macOS 13 or newer, run these commands on the node:

```sh
fleet capture doctor
fleet capture permissions
```

Grant Screen Recording and Accessibility to Fleet in System Settings, then
restart Fleet. Permission requests are local and explicit; launching an app from
the Desktop never silently requests or changes OS permissions. `doctor` reports
recording, input and interactive-desktop status without opening a prompt.

On Windows 10 version 1903 or newer, run Fleet as the logged-in desktop user.
`fleet capture doctor` verifies Windows Graphics Capture availability. The app
and Fleet must have compatible integrity levels: input to an elevated app may
be rejected. Fleet does not bypass foreground-window or UAC restrictions.

Browser installs its own Chromium into the reusable Python environment once.
Each Atrium Browser window has a separate persistent profile, keyed by its
Desktop window ID; cookies are not shared between separate Browser windows in
this implementation. Existing local Chrome profiles are never attached.

QuPath must already be installed on that node. Standard macOS and Windows
installation paths are detected. For a custom installation, set
`PANTHEON_QUPATH_EXECUTABLE` to its absolute native executable path when starting
Fleet. QuPath's script bridge is started with the owned application. Agent file
paths refer to that node's app workspace.

After updating both Fleet and Desktop, choose the node with **Open on node** or
configure the app's default backend in Fleet. Incompatible nodes show the missing
component or permission instead of starting a Linux-only adapter.

## Capture and control contract

The helper is bound to the application's PID at launch and pins its process
identity. It enumerates, captures and controls only windows owned by that process.
There is no desktop capture command or client-provided PID. Applications whose
launcher exits and hands the GUI to a different process need a dedicated adapter;
Fleet does not guess another process to attach to. OS-owned dialogs in a separate
process are not captured by this adapter.

The initial transport sends JPEG window frames at up to 20 fps. Each client keeps
only the latest frame per window; slow clients reconnect instead of accumulating
video or replaying delayed input. This version does not include audio, H.264,
clipboard synchronization or a virtual/headless macOS/Windows desktop.

The native stream listens on loopback behind Fleet's authenticated service
gateway. A fresh per-instance secret is additionally required in the first
WebSocket message, never in its URL. Reconnects retain window handles only for
the same backend incarnation. Input is limited to known window IDs; held keys
and mouse buttons are released on disconnect. The browser viewer supports IME
composition and the Agent API accepts Unicode text and screenshot coordinates.

Closing a window requests the application's normal close action. Save/Cancel
dialogs stay visible. The backend stop guard refuses to terminate an owned
application with remaining windows; once the final window closes it can clean
up the background process and capture helper. Recording starts are not reported
as ready until the first main-window frame arrives.

## Building and testing

`fleet/scripts/build-macos-app.sh` builds the helper for Apple Silicon and Intel
before signing Fleet.app. On Windows, build `fleet/native-capture/windows` with
CMake and a Windows SDK containing C++/WinRT; x64 and ARM64 are supported.
The Fleet release workflow publishes both Windows helpers and includes them in
the release checksums.

The native-capture CI compiles both macOS architectures and both Windows
architectures. The Windows x64 runner exercises a synthetic window, foreign-PID
rejection, Unicode input, resize and normal close, plus a real Chromium launch
and authenticated stream. The same window fixture can run locally on macOS
with an awake desktop and recording permission:

```sh
swiftc fleet/native-capture/tests/window.swift -o /tmp/fleet-capture-fixture
python fleet/native-capture/tests/smoke.py \
  /path/to/Fleet.app/Contents/MacOS/fleet-native-capture /tmp/fleet-capture-fixture
```

These tests create and clean up their own windows and browser profile. They never
capture the entire desktop or attach to an existing user application. A skipped
permission/interactive-desktop test is reported explicitly, not counted as a
successful capture.
