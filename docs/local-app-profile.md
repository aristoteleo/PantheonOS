# Local App profiles

`pantheon local` is an opt-in macOS/Linux host for an explicit composition of
ordinary Apps. It starts a private Controller, NATS broker and Runner, then uses
the ordinary Fleet deployment and Model Services coordinators. It does not join
the installed Fleet or use ambient Fleet credentials. An explicit `--agent`
option opens its terminal frontend; no Desktop window is opened. Existing
`pantheon cli` and `pantheon ui` defaults are unchanged.

This is the local lifecycle building block for those product launchers. A product
bundle can supply the three executables and already-built App packages, and the
launcher compiles an explicit Agent setup into the ordinary composition. There
is no automatic download, first-run configuration wizard or native Desktop installer.
The host itself imports no Agent implementation.

## Packaged Agent entry

A built product can be opened without supplying individual executable paths,
artifact digests, node IDs, generations, owner credentials or an App profile
manifest:

```sh
pantheon local \
  --bundle /absolute/path/to/local-product \
  --setup /absolute/path/to/agent-setup.json \
  --profile /absolute/path/to/profile \
  --workspace /absolute/path/to/workspace \
  --agent agent
```

Add `-i PROMPT`, `--stream`, `--resume` or other conversation options below as
needed. Omitting `--agent` runs the same composition without a terminal frontend.
`pantheon cli --bundle ... --setup ... --profile ... --workspace ...` selects the
same explicit product mode and opens its `agent` frontend automatically. Without
`--bundle`, `pantheon cli` continues to use its existing implementation.
`--bundle` cannot be mixed with `--manifest` or executable overrides. It requires
the existing Python CLI environment; this is not yet a standalone native Desktop
installer or a change to the default `pantheon cli`/`pantheon ui` launch path.

The bundle's `local-bundle.json` pins its exact host platform and SHA-256 of the
Controller, broker and Runner. Its ordinary `release-set.json` pins the App
artifacts. Only macOS/Linux arm64/amd64 are accepted. The launcher checks binary
integrity and release metadata before starting processes; the ordinary profile
staging path verifies App bytes before first installation and reuses installed
digests on clean reopen. These digests detect changes; distribution signing and
release publication remain separate work. Neither the compiler nor launcher
silently selects another architecture, node, model, or dependency.

`agent-setup.json` is a private (mode 0600) JSON configuration snapshot, kept
outside the product. It has these fields:

| Field | Meaning |
| --- | --- |
| `protocol` | `1` |
| `agent` | Existing prepared Agent values: namespace, project mappings, settings, models and dependency profiles/defaults |
| `tools` | Existing allocator policies, keyed by the aliases in those dependency profiles |
| `models` | Existing Model Services selection: `deployments`, `routes`, `allow_wake` |
| `providers` | Provider aliases with ordinary `scope`, `components`, `bindings` |
| `model_apps` | Attached model publications with `deployment_id`, `name`, `models`, and an `app` containing `scope`, `components`, `bindings` |
| `credentials` | Optional existing Agent node-vault references; never inline keys |
| `extra_bindings` | Optional ordinary Agent GUI/plugin bindings |

Aliases map to the same aliases in the bundle's release set. The three required
aliases are `agent`, `allocator`, and `model-access`, with their existing App IDs.
Use `{"$local":"workspace"}` in project paths to bind the selected workspace.
The canonical Agent deployment composer creates the allocator/model-access
dependencies; the profile supplies its own owner, node, TLS, directory, vault
references and restart generations. It preserves all selected settings, tool
profiles and model routes, rejecting missing Apps or policies instead of disabling
them. Configuration capture/setup UI and new external credential provisioning
are not supplied by this command; existing migration and Model Services flows
remain responsible for those inputs. Changed setup is not an implicit upgrade of
an existing profile and is rejected by its composition journal.

For distribution maintainers, build the immutable bundle from an already-built
ordinary release set:

```sh
python -m pantheon.apps.local_agent \
  --output /absolute/path/to/new-local-product \
  --release /absolute/path/to/agent-release-set \
  --platform darwin-arm64 \
  --controller /absolute/path/to/controller \
  --broker /absolute/path/to/nats-server \
  --runner /absolute/path/to/fleet
```

Every bundled App must have that explicit native variant. The builder verifies
copied artifacts, excludes the same `.env`/cache/VCS directories as ordinary App
packaging and publishes a new directory only after all checks pass. It does not
overwrite an existing product, install hooks, download engines, or copy user
setup into the bundle. Individual Apps retain their normal manifests and version
identities. Multi-platform providers retain an explicit package `platform` so
installation uses the same canonical bytes as the indexed release.

## Start

Create an existing workspace directory, a private manifest file (`chmod 600`),
and choose a new profile directory. Run from the checkout/environment containing
this implementation:

```sh
pantheon local \
  --profile /absolute/path/to/profile \
  --workspace /absolute/path/to/workspace \
  --manifest /absolute/path/to/profile.json \
  --controller /absolute/path/to/controller \
  --broker /absolute/path/to/nats-server \
  --runner /absolute/path/to/fleet
```

The host holds the profile's exclusive local lock. Its directory contains durable
Fleet identities, private TLS material, credentials, process logs, installed App
artifacts, App-owned data, and the composition journal. Keep this directory across
clean exits. Do not share one profile between concurrently running hosts.

Stdout contains JSON status objects. `ready` means the selected composition has
passed its ordinary startup checks; it is not evidence of a connected GUI.
`needs_attention` means the current operation needs review while healthy Fleet
infrastructure is retained. Public statuses do not contain manifest configuration,
model publications, bearer tokens or arbitrary transport exception text.

- **Ctrl-C or SIGTERM:** request ordered App shutdown. Wait for `stopped` and the
  process to exit. App data and installed artifacts remain available.
- **SIGUSR1:** after addressing an error, retry the same startup or stop operation.
  This does not invent a replacement operation or launch a duplicate instance.
- **Embedded callers:** `serve(..., commands=queue, on_status=callback)` accepts
  `retry`, `stop` and `status` commands. It does not change the caller's signal
  handlers. Its lifetime owns Fleet; do not cancel it as a substitute for draining
  Apps. A callback should remain available for the entire host lifetime.

An embedding frontend can also supply `on_ready=session_callback`. The callback
runs once as an owned foreground task after the full composition becomes ready.
It may obtain `await session.bind_rpc(alias, app_id)` to call that exact App
instance/generation using the local profile owner's capability. This is a local
owner client, not a scoped dependency grant to pass to another App. Callback
completion or failure drains the composition; callback cancellation is joined
before shutdown. A failed drain retains the host for explicit retry. Do not use
this callback to start another local Agent backend.

A stop request during startup is processed between bounded lifecycle advances.
It durably fences that startup, then uses the original generic deployment aborts
to stop consumers before attached model providers. The host reports `stopping`
until all instances are stopped without held resources. Data and stopped model
publications are retained. Missing journals are accepted only when the native
ledger proves their targets were unused; changed generations require inspection.

After a confirmed stop and owner exit, explicitly reopening starts a new cycle
with fresh authority and the same retained data. A failed upgraded candidate may
instead use reviewed retained-source rollback. Another upgrade of a cleaned-up,
never-ready candidate is rejected until it is restarted successfully or rolled
back. Native Desktop offers **Cancel startup & settings** during startup and
**Stop Apps & settings** after a recoverable failure. Configuration/version
changes stay unavailable until both the stopped receipt and owner exit succeed.

If the owner exits abruptly while its original Controller, broker and Runner
survive, use the explicit recovery below. Partial infrastructure loss or a changed
authority still requires further recovery work; do not delete journals or edit
recorded generations to force another startup. In-flight node calls are observed
rather than cancelled and replayed under new operation IDs.

## Recovery after a local owner crash

New local profiles retain their lifetime lock in each owned infrastructure
process. Killing the Python product owner therefore cannot allow another normal
startup to rewrite a live profile's coordinates or credentials. A separate
management lock excludes simultaneous owners or recovery attempts.

When all three original infrastructure processes survive, run:

```sh
pantheon local --launch /absolute/private/launch.json --recover
```

The native Desktop failure screen exposes the same **Recover & stop local Apps**
action. This verifies and drains the original Apps; it does not start an Agent,
replay a prompt, apply another release or select another model. Wait for a stopped
receipt and successful owner exit, then explicitly reopen the saved profile.
An already-stopped profile is confirmed without advancing its startup cycle.

The owner-private `processes.json` receipt includes OS process birth identities,
command fingerprints and original coordinates. Recovery rejects a still-live
owner, missing/replaced processes, changed trust or a missing authenticated node.
It uses the original loopback TLS authority and renews its ordinary owner
credential; no new infrastructure is spawned. Invalid admission cannot signal
processes. An error after takeover, without a confirmed App drain, retains the
original infrastructure for subsequent inspection/recovery.

This covers owner-process failure with surviving infrastructure, including
incomplete App startup. It does not cover machine reboot, a lost Controller,
broker or Runner, an older profile without a receipt, or distributed takeover.
Those cases fail closed rather than inferring termination or replacing unknown
operations. No new dependency is added; process identity checks use the existing
`psutil` dependency. Windows local hosting remains outside this POSIX path.

## Terminal calls through the Agent App

With an Agent composition manifest, add `--agent ALIAS` to open an interactive
terminal, or `--agent ALIAS -i PROMPT` to run one terminal turn. `ALIAS` is the consumer name in `apps`, and its
installed App must be `agent` with native client protocol version 1. Startup
completes before the frontend binds the exact prepared generation. The frontend
calls ordinary App RPC; it does not create a second Agent runtime or open the
backend's data directory.

```sh
pantheon local \
  --profile /absolute/path/to/profile \
  --workspace /absolute/path/to/workspace \
  --manifest /absolute/path/to/agent-profile.json \
  --controller /absolute/path/to/controller \
  --broker /absolute/path/to/nats-server \
  --runner /absolute/path/to/fleet \
  --agent agent -i 'Continue the analysis' --resume
```

Conversation options:

- `--chat-id ID`: continue an exact conversation owned by this Agent App.
- `--resume` / `-r`: continue its most recently active conversation. An optional
  value selects a one-based recency index, ID prefix, or name prefix.
- `--template-json FILE`: private JSON team template for a new conversation.
  It cannot be combined with resume/chat ID; it does not edit an existing team.
- `--model MODEL`: explicitly set the first Agent's model through the App's own
  model validation and configured providers. This never supplies a new API key.

For one-shot calls stdout contains one JSON object with `chat_id` and `response`.
Add `--stream` alongside `-i` for JSON lines with `kind: event`,
`kind: history_reset`, and a final `kind: result` containing `chat_id` and
`response`. Streaming and interactive calls require event cursor protocol 1.
Host statuses go to stderr. Conversation data stays in the App. The client verifies protocol
support and checks live conversation metadata before submitting; it does not
download history for each prompt. The frontend also provides an explicit history
reader for interactive restoration: fragmented snapshots are size/digest
checked and released, including on rejection. Its default download limit is
64 MiB and fails rather than truncating history; it does not limit conversations
that can be continued by the one-shot command.

The interactive frontend streams assistant text and tool results. `/help` lists
`/chats`, `/resume ID|NAME|INDEX`, `/new`, `/history`, `/models`, `/model MODEL`,
`/agents`, `/agent NAME`, `/stop` and `/quit`. EOF also exits. TTY input uses line
editing; UTF-8 pipes and redirected regular files are supported with a 128 KiB
line limit. Terminal commands call the same bound App; they never execute a local
Shell themselves or read backend-owned result paths. Ctrl-C/SIGTERM uses the
profile shutdown behavior below; it does not yet reproduce every legacy REPL
interrupt shortcut.

Event fragments are validated and assembled before display. A replay retention
gap reloads the saved history and current in-flight events, with an explicit
replacement notice in text mode. That recovery never resends the user's prompt.
A broken event transport is reported and requests cancellation of a still-pending
call; it is not silently retried or treated as a confirmed backend stop.

After the frontend completes or fails, the host drains the composition before
exiting. An error returns nonzero after a confirmed stop. Ctrl-C/SIGTERM interrupts
the foreground; if it is awaiting a submitted turn, it explicitly asks the Agent
to stop, then ordinary App shutdown joins outstanding work and saves. Cancelling
an RPC observer alone is never treated as cancellation of backend execution.
A failed drain keeps the profile alive for inspection/retry. The frontend is
started at most once in that host lifetime and is not replayed by SIGUSR1.

No inference or conversation creation is automatically retried after an uncertain
response. Inspect the conversation before sending again. The one-shot client
refuses a known running conversation; a concurrent queued-message acknowledgement
is reported as an incomplete outcome instead of a completed response.

The existing rich interactive REPL remains available through the old/prepared
CLI entrypoints. The new frontend has basic streaming and interactive commands;
complete command/interrupt parity, rich reasoning/media presentation, template
editing, Markdown templates, image input and direct native Desktop composition
are still being migrated. This command
does not yet replace the full interactive CLI or change its defaults.

## Manifest

The version-1 manifest has exactly `protocol`, `packages`, `apps`, and
`model_apps`. Example shape for an already-built App without configuration:

```json
{
  "protocol": 1,
  "packages": {
    "example": {
      "path": "/absolute/path/to/built-app",
      "revision": "REPLACE_WITH_64_CHARACTER_ARTIFACT_SHA256"
    }
  },
  "apps": {
    "example": {
      "package": "example",
      "scope": "local-example",
      "components": {},
      "bindings": {}
    }
  },
  "model_apps": {}
}
```

This shape is not a runnable sample until the path and revision refer to a real
package. Calculate the digest using the same artifact builder used for staging:

```python
from pathlib import Path
from pantheon.apps.lifecycle import build_artifact
_, revision = build_artifact(Path('/absolute/path/to/built-app'))
print(revision)
```

An optional package `platform` pins `darwin-arm64`, `darwin-amd64`, `linux-arm64`
or `linux-amd64` when its source contains multiple execution manifests.

Each App's immutable manifest determines its required components, configuration,
credentials and dependency interfaces. Supply these through ordinary `components`
and `bindings`; the host validates them before starting the App. `$app` and
`$model` references use the existing deployment/bootstrap contract. There may be
1–24 used packages, 1–16 consumer Apps and up to 8 attached model Apps. Input JSON
is limited to 64 KiB and must not contain duplicate keys.

Inside component configuration and bindings, an exact object `{"$local": NAME}`
resolves one of these profile-owned values:

| Name | Value |
| --- | --- |
| `controller` | Current loopback HTTPS authority URL |
| `trust_roots_pem` | This profile's CA certificate |
| `directory_root` | Its private Model Services directory path |
| `workspace` | Its explicit workspace path |
| `owner_credential` | Endpoint-scoped node-vault reference, never an inline key |

Use `owner_credential` only for owner brokers whose declared configuration needs
it. Consumer Apps receive scoped dependency bindings. Do not put plaintext API
keys in manifests; the host provisions only its own endpoint-scoped owner vault
entry, not arbitrary external-provider credentials.

An attached-model provider has the following shape in `model_apps`:

```json
{
  "connector": {
    "deployment_id": "local",
    "name": "Local model",
    "models": [{"id": "example:8b", "context_limit": 4096}],
    "app": {
      "package": "connector",
      "scope": "model-local",
      "components": {
        "backend": {
          "values": {
            "connector": {
              "engine": "ollama",
              "endpoint": "http://127.0.0.1:11434"
            }
          }
        }
      },
      "bindings": {}
    }
  }
}
```

The Connector package must be declared in `packages`, and the endpoint/model must
actually exist. The original Model Services manager verifies the publication.
This host does not start an Ollama/LM Studio/SGLang engine for that endpoint; the
current profile format supports attached providers. The model-access broker and
Agent or other consumers use the original Model Services binding mechanisms.
For complete tested compositions, see `tests/test_local_profile.py` and
`tests/test_local_profile_agent.py`.

## Lifetime and recovery boundaries

Installed immutable packages are reused on subsequent launches; their source
bundles are not rebuilt/uploaded and install hooks are not rerun. A missing
installation requires the original package and matching digest. Changing the
manifest, workspace, owner, or CA is an explicit reconfiguration/migration task,
not an implicit restart. There is not yet a profile-upgrade command.

After an acknowledged clean shutdown, the next launch verifies the stopped App
generations, prepares new generations, rebinds the original stopped model
publications, and starts consumers against the fresh local endpoint. Model
selection and App data remain unchanged. During shutdown, consumers drain before
providers; unconfirmed or failed drains keep infrastructure available. Other
running Apps on the profile prevent infrastructure shutdown.

The host periodically maintains the original dependency grants. Failed
observations are reported and retried; they never replay App starts or tool calls,
issue replacement grants, or revive expired authorization. Changed maintenance
warnings and recovery are reported through `dependency_maintenance` statuses.

Native tests cover clean reopen, model calls through the original Connector,
retained Agent conversations and distinct real Shell sessions after restart,
installed-artifact reuse, SIGINT shutdown, and resuming a lost startup reply.
Upstream model replies in those tests are fixtures. Abrupt crash recovery,
managed-engine recovery, Windows hosting, cross-node orchestration, automatic
CLI/Desktop composition and full release migration remain separate work.


## Native Desktop candidate

The opt-in Tauri configuration `src-tauri/tauri.agent.conf.json` in the UI
repository builds **Pantheon Agent Candidate**. Its trusted native shell starts
this same local product owner using `pantheon local --desktop-agent agent`.
It does not start the legacy ChatRoom backend or the user's installed Fleet.
The original Desktop and CLI defaults are unchanged.

Set `PANTHEON_LOCAL_AGENT_CONFIG` to a private (0600), regular JSON file:

```json
{
  "protocol": 1,
  "launcher": ["/absolute/python", "-m", "pantheon"],
  "bundle": "/absolute/product",
  "setup": "/absolute/private-setup.json",
  "profile": "/absolute/profile",
  "workspace": "/absolute/workspace"
}
```

The prepared setup and pinned product have the same format as the CLI bundle
entry. First-run setup capture, a bundled Python launcher, code signing,
installation and upgrades are not provided by this candidate. A shell-only test
setup cannot run the shipped General Team: its Files, Notebook, Web, Evolution
and Desktop dependencies must also be installed and explicitly bound. Missing
bindings are reported by name; the runtime does not drop team members or tools.

The owner emits versioned readiness records with exact App revision, instance,
node and generation. The native shell validates a private loopback capability
URL and embeds the paired GUI in a sandboxed webview without native IPC. Fleet
credentials never enter that GUI. Readiness URLs are ephemeral credentials and
must not be logged or included in reports. GUI assets are copied from a verified
canonical App artifact, and selected conversation state is kept in the private
profile. Reload restores the conversation without replaying a turn.

Window close, application quit, or loss of the native stdin control pipe request
an ordered stop. The native window waits for acknowledged profile drain before
exiting. A failed/uncertain stop leaves the owner and its recovery state intact;
the shell neither force-kills it nor silently opens another Agent. An interrupted
asset snapshot joins its writer before removing temporary files.
