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

Native viewers negotiate WebRTC through the existing authenticated Fleet gateway.
Media then travels directly between the selected node and the viewer when ICE
can establish a route. Fleet's bundled Pion implementation (already a dependency
of libp2p) runs as an app-owned `fleet capture peer` subprocess; there is no new
Python package, media server, container or separately installed executable.
The viewer uses the browser's built-in WebRTC implementation.

On macOS, ScreenCaptureKit feeds the system VideoToolbox hardware H.264 encoder
at up to 60 fps, with no B-frame reordering. RTP carries each owned window as a
separate video track. Hardware encoding starts only while a viewer subscribes;
failed/unavailable hardware falls back to JPEG. Windows currently uses JPEG over
an unordered, non-retransmitted DataChannel at up to 20 fps. Windows hardware
video encoding and audio are not part of this change.

The gateway continues to serve JPEG during ICE negotiation. If direct media
fails or UDP is blocked, JPEG and input automatically return to the existing
WebSocket, and the viewer retries WebRTC with bounded backoff. Healthy H.264
viewers stop receiving duplicate JPEG media; occasional local JPEG snapshots
remain available to the Agent API. JPEG fragments and decode queues are bounded,
and dropped H.264 delta frames require a new keyframe.

The small media badge reports **Direct**, **Relay**, or **Gateway**, codec and
network round-trip time. This number is not total capture-to-display latency.
The app's node/instance selection remains unchanged during a transport switch.
An ordered input barrier prevents the WebSocket-to-DataChannel transition from
reordering keystrokes. Media stops when its authenticated gateway connection
ends, so direct transport does not bypass Fleet credential lifetime or ownership.

No public STUN or TURN provider is contacted by default. Same-machine and LAN
host candidates need neither. For internet NAT traversal, the node administrator
can optionally supply ICE servers when launching Fleet:

```sh
export PANTHEON_STREAM_ICE_SERVERS='[{"urls":["stun:stun.example.com:3478"]}]'
fleet up
```

TURN entries additionally support `username` and `credential` as in the standard
WebRTC ICE server configuration; use scoped, short-lived TURN credentials where
available. Configuration is admin-controlled, not accepted from viewer offers.
It is delivered only to authenticated viewers and is never placed in URLs/logs.
A TURN service is optional: without one, unreachable direct routes fall back to
the Fleet gateway rather than preventing app startup. This does not reuse the
libp2p file-transfer relay; browser media and node file transfers are separate
protocols within Fleet.

This version does not include clipboard synchronization or a virtual/headless
macOS/Windows desktop. Linux Xpra streaming is unchanged.

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

### Direct-media validation

`go test -race ./internal/streamrtc` (inside `fleet/`) checks real loopback ICE,
DataChannel input and fragmented media, plus packet bounds. Frontend tests cover
reassembly, stale negotiation, ICE timeout/retry and gateway fallback. The macOS
helper's `--test-encoder` emits H.264 from synthetic pixels without recording the
screen; exit 77 explicitly means hardware encoding is unavailable.

For an end-to-end check, bundle the UI's `nativeCapture.ts` as browser ESM with
esbuild, build `fleet`, and run `fleet/native-capture/tests/peer_smoke.py` with the
Fleet binary, ESM file, and optionally a packet file from `--test-encoder`.
This uses real headless Chromium and the production adapter to verify direct
JPEG/H.264, input, media-process failure fallback and viewer-disconnect cleanup.
It never attaches to an existing user application.
