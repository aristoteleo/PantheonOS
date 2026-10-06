# Local General Team product preset

The opt-in local product supports `"preset": "general-team"` in the private
`--setup` JSON. CLI and the native Desktop candidate use the same expansion and
ordinary App profile compiler. This is a new-profile preset, not an importer that
rewrites an existing user's custom dependency configuration.

The preset supplies Files, per-logical-Agent Shell sessions, Notebook, Web,
Evolution, Desktop, Fleet and Model Services management. It binds background
memory and project GUI file access independently of individual Agent sessions.
It keeps the packaged General Team and plugin defaults. Explicit user settings
remain unchanged. It does not select models, download model weights, create a
cloud account, import another application's credentials or start paid compute.

## Owner input

| Field | Required choice |
| --- | --- |
| `protocol` / `preset` | `1` / `"general-team"` |
| `agent` | Original Agent `protocol`, `namespace`, `projects`, `models`; optional `settings`, `active_project`, `default_project` |
| `models` | Explicit Model Services access policy: `deployments`, `routes`, `allow_wake` |
| `model_apps` | Ordinary attached model publications, each with `deployment_id`, `name`, `models`, `app` |
| `files.sampling` | Model reference, `max_tokens`, `max_requests_per_call`, or explicit `{"state":"unconfigured"}` |
| `files.image_generation` | Model reference, `aliases`, `timeout_seconds`, or explicit `{"state":"unconfigured"}` |
| `desktop` | `user_seed`, `catalog`, `store`, `data`; optional `data_roots` and Store `credentials` |
| `evolution` | `execution`, `options`; optional isolated `placement` and its `credentials` |
| `credentials` | Optional explicit Agent vault references, including original BYOK/platform-budget bindings |
| `management` | Optional Hub vault reference `hub` and `hub_ca_pem` for cloud management |
| `notebook` | Optional `execution_timeout` and `execution_logging` |

Use `{"$local":"workspace"}` for a project attached to the launcher's workspace.
Do not supply generated Agent `dependencies`, `auxiliary` or `view_dependencies`
in this format. The compiler rejects conflicts instead of overwriting them.
Keep using the existing detailed setup format for custom tool/MCP compositions.

If Agent models use `fleet_tiers`, explicitly bind **low, normal and high**.
This avoids foreground chat succeeding while background memory fails on its
separate low-tier model. The three tiers may intentionally use the same selected
model; the preset never fills a missing tier by silently substituting another.
BYOK, platform-budget and OAuth configuration retain the original Agent schema.
Availability and capability checks still belong to the model service/runtime.

Files references must be present in the chosen model access policy. The preset
constructs its own model-access App with only the selected deployments/routes,
not every model available to the Agent. Sampling and generation require their
corresponding model capabilities. Model selection does not launch an engine.

An owner who has only a text model can defer image observation and/or generation
explicitly, for example:

```json
"files": {
  "sampling": {"state": "unconfigured"},
  "image_generation": {"state": "unconfigured"}
}
```

This keeps the complete Files App, its tool interfaces, General Team and all
plugins. Calling a deferred capability returns `model_not_configured` with the
owner setting to change, before reading image inputs or making model requests.
Files receives no deployment/route access for that capability. Other file tools,
Agent text inference and memory continue using their own configured bindings.
Missing values, mixed state/model objects and invalid configured credentials
still fail startup. The same Files package accepts a model binding on a new
prepared start. Existing local product profiles pin their entire composition:
editing their setup and reopening is rejected, even after a clean stop. Use the
reviewed configuration update below when selecting from existing model
publications. Adding a new attached model provider or changing its publication
requires the separate release update flow, which remains pending. Do not delete
checkpoints or recreate a profile to work around that protection.

Without `management.hub`, local model management still shares the same persistent
directory as inference. Cloud operations are explicitly unavailable; this is not
an empty observation of cloud resources and does not stop remote models.

## Product release

The bundle must contain the normal `agent`, `allocator` and `model-access` Apps,
plus these aliases:

| Alias | App id |
| --- | --- |
| `files` | `file-manager` with sampling and image generation |
| `shell` | `shell` |
| `notebook` | `integrated-notebook` |
| `web` | `web` |
| `evolution` | `evolution` |
| `desktop` | `desktop` |
| `fleet` | `fleet` |
| `model-management` | `model-services-management` |
| `files-models` | `model-services-control` |

The source build command below assembles all mandatory packages and each
explicitly selected model App alias. It derives Agent versioned dependencies
from the built provider manifests, including startup Files access and runtime
Shell/other tool access. It uses the existing tool-contract compiler, including
explicit hidden Files metadata access. No handwritten dependency map is needed.
The runtime preset still does not modify immutable App manifests.

```sh
python -m pantheon.apps.general_agent_release \
  --output /absolute/general-team-release \
  --platform darwin-arm64 --version 0.7.0 \
  --frontend /absolute/paired-agent-build \
  --notebook-frontend /absolute/notebook-build \
  --transport /absolute/fleet-app-transport \
  --model-app connector --model-app image-connector

python -m pantheon.apps.local_agent \
  --output /absolute/product --release /absolute/general-team-release \
  --platform darwin-arm64 \
  --controller /absolute/fleet-controller \
  --broker /absolute/nats-server --runner /absolute/fleet
```

Use the actual target platform and paired GUI release version. The builder
supports macOS/Linux arm64/amd64; this does not constitute cross-platform runtime
acceptance. It needs Go on the build host for the native Shell (`--go` selects
the executable); the Shell target needs neither Go nor Python. Other Apps retain
their existing runtime requirements. Supply complete GUI builds, including lazy
assets. The Agent builder checks its frontend boundary report and version, and
the supplied transport must match the target architecture.

The output is the existing immutable `release-set.json` distribution, compatible
with normal release delivery and local bundle assembly. Builds require a new
output path and publish it only after all builders and artifact checks succeed.
The index's existing sixteen-package limit permits up to four explicit model
aliases in addition to twelve mandatory packages. Aliases must be unique and
cannot replace product packages. Each model alias contains the original
Connector; endpoint, engine, routes and credentials belong to the private owner
setup. Building does not launch services, download model weights or allocate
cloud compute. Omit model aliases only when the owner uses external deployments.

## Private model and App credentials

The optional `--credentials /absolute/private-credentials.json` flag on
`pantheon local` or `pantheon cli --bundle` supplies keys to this profile's Fleet
vault. The native Desktop launch JSON accepts the same path as its optional
`credentials` field. This file is separate from `--setup`: setup contains only
endpoint-bound references, never their keys.

```json
{
  "protocol": 1,
  "credentials": {
    "node-secret://selected-model": {
      "endpoint": "https://models.example.com/v1",
      "key": "REPLACE_WITH_YOUR_KEY"
    }
  }
}
```

Declare that reference either in an ordinary component's `credentials`, or in
an attached model Connector's `values.connector.secret_ref` beside its endpoint.
Each supplied entry must match the selected composition. A reference reused for
different endpoints is rejected. Generated profile owner/bus references cannot
be supplied here. An omitted entry can use an already provisioned vault value;
missing required credentials still fail through ordinary App readiness.

Keep the source an owner-private regular file (`0600`) outside the workspace,
App package directories and Desktop-served catalog/data roots. Symbolic links,
hard links, oversized sources and duplicate fields are rejected. Startup reads
one in-memory snapshot before opening local infrastructure. Keys travel through
the existing owner-authenticated encrypted Fleet delivery, not arguments or
recipe/checkpoint JSON. Desktop only displays the source path.

Reopening with identical credentials is idempotent. A different key for an
existing reference is a vault conflict, not an instruction to replace it.
Credential rotation and graphical key entry remain separate work; restarting
or editing this file does not authorize overwriting an existing vault entry.

## Launch and current limits

The native candidate now offers **Create workspace setup…**. Choose a workspace,
the Python runtime containing Pantheon and a complete App bundle. Enter a project
name, existing model service URL, any required API key, explicit low/normal/high
model ids and the App Store URL. Ollama, LM Studio and SGLang use an already
running loopback engine; Model API supports an existing HTTPS API service.
This does not install an engine, download weights or allocate cloud compute.

Optional model choices include a context limit, image inspection through the
normal model and a separate image-generation model on the same API service.
Omitted image choices are explicitly unconfigured; all default tools/plugins
remain installed. The generator preserves the normal Agent and Evolution defaults
and supplies Notebook's original 3600-second execution timeout and enabled logs.

Saving compiles the full General Team preset, creates a **new** private setup
directory, stores the key separately and publishes `launch.json` last. It does
not start a Fleet or change another profile. Desktop shows the completed choice;
**Start local Apps** is still a separate action. Cancel or submit clears the
password field. The optional credential source is shown by path only.

CLI clients can use the same `pantheon local-setup` command with `--bundle`,
`--output`, `--workspace` and `--python` absolute paths. It reads at most 32 KiB
of JSON choices from stdin, never keys from command arguments, and returns launch
metadata only. Required choice fields are `protocol: 1`, `project_name`, `engine`,
`endpoint`, `tiers` (explicit `low`, `normal`, `high` ids), and `store_origin`.
Optional fields are `key`, `context_limit`, `image_inspection` and `image_model`.
The destination must be new and outside the workspace/App bundle. A failed write
can leave private partial files, but no completed `launch.json`; it is never
automatically treated as a runnable setup or overwritten on retry.

This is initial configuration for an installed local distribution, not automatic
runtime installation, model discovery, account login, existing-user migration or
editing an active profile. Existing prepared configurations remain supported.

Keep setup JSON private (`0600`). An existing built local bundle can be launched
through the opt-in terminal path:

```sh
pantheon cli --bundle /absolute/product --setup /absolute/setup.json \
  --profile /absolute/profile --workspace /absolute/workspace
```

The native Desktop candidate's `PANTHEON_LOCAL_AGENT_CONFIG` launch file uses the
same `bundle` and `setup` paths and the same profile owner. Its `launcher` remains
an explicit executable/argument array, e.g. an installed Python with
`["/absolute/python", "-m", "pantheon"]`. Do not run two owners of the same
profile at once. Closing the native owner drains its Apps; the profile only
reopens automatically after a verified clean stop.

Without a usable launch configuration, the native candidate also offers **Choose
configuration…**. Select the private launch JSON, review its paths, then use
**Start local Apps**. Starting remembers a private copy in the candidate's app
config directory; selection/cancellation alone does not. The environment override
takes precedence over a remembered copy. The chooser consumes a prepared setup;
use the creation form above for a new setup. Neither path migrates an existing
installation.

## Review and apply a configuration update

Keep the source setup and save the candidate as a separate private (`0600`) JSON
file. Stop the original profile cleanly first, then review:

```sh
python -m pantheon.platform.local_profile_update \
  --profile /absolute/profile --workspace /absolute/workspace \
  --bundle /absolute/product \
  --source-setup /absolute/setup.json --target-setup /absolute/candidate.json
```

The output contains a `review_id`, source/target hashes and changed JSON pointers.
Values and credential references are not printed; inspect the two private setup
files to review the actual selections and authority changes. Repeat the command
with `--approve REVIEW_ID` to approve that exact candidate. Approval starts the
owned Fleet infrastructure to verify installed contracts and stopped generations,
but does not start Apps, issue their grants or rewrite the previous cycle. It
stores private immutable review receipts and an atomic approval reference.

Then use the ordinary CLI/Desktop launch with the **target** setup and the same
profile/workspace. Normal restart revalidates generations, advances the cycle and
prepares the new configuration, retaining instance identities and App data.
An edited target, changed checkpoint, uncertain stop or malformed contract is
rejected. The old setup can still be reopened before the approved target starts;
doing so makes the earlier approval stale and requires a fresh review.

This command permits changes to existing Apps' component values, credentials and
bindings, including model selections and access policies. It deliberately requires
unchanged package artifacts, App names/scopes and attached model publications.
It does not add/remove Apps, upgrade code or replace an engine publication.
The opt-in native Desktop also exposes this flow: choose **Stop Apps & settings**
in the compact title bar, wait for confirmed shutdown and owner exit, then choose
**Review configuration update…**. The file chooser starts in the source setup's
directory. Select the candidate **setup JSON**, not a launch JSON. Review the
changed paths and inspect the private files for their actual values, then choose
**Apply reviewed configuration**. Approval remembers the target setup without
starting Apps; **Start local Apps** explicitly opens it. Cancellation/discard
preserves the current setup. If saving launch metadata fails after approval, the
window retains the approved target and retries saving before starting. An explicit
`PANTHEON_LOCAL_AGENT_CONFIG` still overrides remembered metadata on process restart.

This native flow requires the public `pantheon local-update` command (equivalent
to the module command above) in its explicit launcher. It does not shell-expand
paths or accept candidate paths/review ids from remote App JavaScript. While a
review/approval command runs, selection, startup and normal window exit wait for
completion; the command is never killed on a timer during a journal write.

A configuration reversal is a new review
after a clean stop; it does not restore data or undo work already performed by
the Apps. An incomplete startup still needs its original recovery flow, not an
automatic rollback. Full package/migration rollback remains separate work.

This removes manual graph/grant/schema wiring from the setup file. It does not yet
provide a graphical first-run model selector, migrate existing data or switch
legacy CLI/Desktop defaults. Full cross-node, release/rollback and default
production acceptance remain separate gates.

## Verification

The native General Team gate builds through the production release assembler
and exercises four lifetimes of the same profile: two
through the profile owner API, a resumed streamed call through `pantheon cli`,
and a call through the native Desktop control/HTTP-view entry point. It verifies
real Shell output, stable Agent identities, history and clean owner shutdown.
Text/image upstreams are deterministic fixtures. Compiler tests separately cover
complete provider selection, narrow Files model policy, preserved owner choices
and compilation without importing Agent runtime. Rendered installed-Desktop and
live-provider acceptance remain outstanding.

The gate runs both fully configured and explicitly deferred Files model choices.
The latter omits the image Connector publication, retains the entire team/tool
surface and memory checks, and verifies `model_not_configured` without image
requests. A separate packaged Files test verifies an unconfigured generation
followed by a configured generation of the same artifact, using the original
Model Services image job flow. The configured complete-team scenario also runs
the public configuration review/approval command between its first two lifetimes,
then reopens with the updated Files sampling limit, preserving the same Agent
identities, conversation, background memory and later CLI/Desktop access.
