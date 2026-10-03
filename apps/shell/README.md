# Shell

Execute shell commands in the workspace using Fleet Runner’s native Go command backend.

## Using this App

Use `run_command` for a command and inspect its exit status and output. The `shell` interface also supports named shell sessions through `new_shell`, `run_command_in_shell`, `get_shell_output` and `close_shell`.

Commands run on the selected execution backend with its working directory, environment and installed programs. Use Terminal when the user should interact with a visible PTY, and Fleet to address another node. This system package describes the Go implementation compiled into Fleet Runner.

A session accepts one command or output reader at a time. If a command times
out, it keeps running: fetch its pending output with `get_shell_output` before
submitting another command. A busy session returns `success: false` and
`status: busy`; it never borrows another owner's idle shell. Create an explicit
second session with `new_shell` when independent parallel work is intended.
An explicit closed or exited `shell_id` is not silently replaced. Session IDs
must be bound by the owner in scoped App grants; knowing an ID is not itself an
authorization boundary on the legacy owner-authenticated service.

## Managed resource sessions

The optional `resource-session@1` interface exposes hidden owner-control methods:
`resource_session_acquire`, `resource_session_get`, `resource_session_renew`, and
`resource_session_release`. Acquisition takes `owner_ref`, a stable 64-hex
`lease_id`, `kind: shell`, and `ttl_seconds` (30–900, default 900). The receipt
contains the same identifiers plus `session_id`, `state` and `expires`.

Use that `session_id` as the bound `shell_id` in the consumer's tool grant. Retry
acquisition with the same lease ID after a lost reply; it returns the original
receipt and does not renew it. Explicit renewal preserves the session and never
shortens its lifetime. Terminal leases are not recreated. The provider checks
leases on normal tool admission and periodically cleans expired resources without
client polling. Releases close only the corresponding Shell. Session receipts
are retained for this provider process lifetime; a provider generation change
must invalidate old bindings. Automatic platform owner coordination is a separate
integration, not enabled by declaring this interface alone.

## Agent interface

Available tools: `run_command`. See [app.json](app.json) for the declared tool contract; runtime discovery provides the current parameter schema.

## Package and source

This is a system App bundled with Pantheon. Its identity, capabilities and entry points are declared in [app.json](app.json). Updates ship with the Pantheon runtime.

### Opt-in managed native package

Build a standalone package directory on the development/build host:

```sh
python3 apps/shell/build_managed.py --os darwin --arch arm64 --output /tmp/shell-managed
```

Supported build targets are `darwin` and `linux`, `arm64` and `amd64`. The output
path must be new. The builder copies the same tool/interface manifest, declares
managed `process` execution, compiles a native `shell` executable, and writes a
matching `fleet.json`. Package this directory with the ordinary
`pantheon.apps.lifecycle.build_artifact` pipeline; target nodes need neither Go,
Python nor a separate NATS service for this App. The source manifest remains the
legacy builtin until dependency assembly explicitly selects a managed instance.
The binary uses the target node's existing POSIX shell; Windows is not enabled.

The standalone entrypoint initializes an instance-owned `${DATA}/workspace`.
Explicit project workspace binding remains a follow-up; this package does not
silently attach the current desktop project or synchronize files across nodes.
Shells remain native processes under the node's OS-user trust boundary, not
sandboxed per-consumer execution environments.

The reusable `fleet/appsvc.ManagedCommand` supplies `start`, `ready` and `drain`.
Fleet assigns a loopback port and pins the App instance/revision/generation;
RPC, health and drain require its private control credential. Readiness and
component stop hooks use those exact values, not mutable endpoint files. This
requires the matching Fleet version supporting process component hooks; old
nodes reject this execution declaration instead of falling back to a builtin.

During drain, new commands/acquisitions are rejected. Existing callers can use
`get_shell_output`, `close_shell`, `resource_session_get` and
`resource_session_release` to finish/release sessions. A timed-out command with
pending output blocks normal stop until observed or explicitly closed. Cleanup
failure remains unsafe. Stop preserves instance files. App RPC credentials and
private dependency configuration paths are removed from child Shell environments.

The native lifecycle test builds the actual package, starts two deployments,
checks session retry/renewal/release, isolated environment and ports, pending
output stop protection, completion during drain, sibling survival and retained
files. Full detached child-process ownership, automatic owner coordination,
cross-node authorized consumer calls and live rollout still need acceptance;
this package alone does not complete the Agent dependency migration.
