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
prepared start. However, existing local product profiles pin their entire
composition: editing their setup and reopening is rejected, even after a clean
stop. This marker supports first-run choices; enabling a capability in an existing
profile still requires the reviewed profile-update flow, which remains pending.
Do not delete checkpoints or recreate a profile to work around that protection.

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

## Launch and current limits

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
takes precedence over a remembered copy. This entry consumes a prepared setup;
it does not yet create model policies or migrate an existing installation.

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
Model Services image job flow. This is not yet a local-profile configuration
update test; that transaction remains pending as described above.
